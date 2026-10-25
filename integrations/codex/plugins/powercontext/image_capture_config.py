# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Project-local consent and endpoint binding, shared by CLI and Desktop hooks."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import tomllib
from collections.abc import Callable
from pathlib import Path
from time import monotonic, time_ns
from typing import Any


def normalized_path(value: str | Path) -> Path:
    path = str(value)
    if os.name == "nt":
        if path.startswith("\\\\?\\UNC\\"):
            path = "\\\\" + path.removeprefix("\\\\?\\UNC\\")
        else:
            path = path.removeprefix("\\\\?\\")
    return Path(path)


def codex_home() -> Path:
    return normalized_path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser().resolve()


def config_path() -> Path:
    return codex_home() / "powercontext" / "image-capture-config.json"


def workspace_key(cwd: str | Path) -> str:
    return os.path.normcase(str(normalized_path(cwd).resolve(strict=True)))


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {"version": 1, "workspaces": {}}
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError("image_config_untrusted")
    value = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value.get("version") != 1
        or not isinstance(value.get("workspaces"), dict)
    ):
        raise ValueError("image_config_unsupported")
    return value


def enabled_profile(cwd: str | Path, server_url: str) -> dict[str, Any] | None:
    value = load_config()["workspaces"].get(workspace_key(cwd))
    if not isinstance(value, dict) or value.get("enabled") is not True:
        return None
    if value.get("server_url") != server_url:
        raise ValueError("image_config_endpoint_changed")
    scope = value.get("scope_id")
    if not isinstance(scope, str) or not scope or len(scope) > 128:
        raise ValueError("image_config_scope_invalid")
    return value


def workspace_disabled(cwd: str | Path) -> bool:
    value = load_config()["workspaces"].get(workspace_key(cwd))
    return isinstance(value, dict) and value.get("enabled") is False


def any_enabled() -> bool:
    return any(
        isinstance(value, dict) and value.get("enabled") is True for value in load_config()["workspaces"].values()
    )


def plugin_enabled() -> bool:
    root = Path(__file__).resolve().parent
    if root.parents[2].name != "cache":
        return True  # Source and isolated host tests do not have an installed plugin identity.
    path = codex_home() / "config.toml"
    if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
        return False
    value = tomllib.loads(path.read_text(encoding="utf-8"))
    plugins = value.get("plugins", {})
    if not isinstance(plugins, dict):
        return False
    plugin = plugins.get("powercontext@" + root.parents[1].name, {})
    return isinstance(plugin, dict) and plugin.get("enabled", True) is True


def write_profile(
    cwd: str | Path,
    *,
    server_url: str,
    scope_id: str,
    enabled: bool,
    activate: Callable[[], None] | None = None,
) -> None:
    """Persist consent and restore its exact epoch if lifecycle activation fails."""
    # The CLI is the only writer; the lock also makes simultaneous setup safe.
    from diagnostics import _locked

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with _locked(path.with_suffix(".lock")):
        previous_config = load_config()
        value = {**previous_config, "workspaces": dict(previous_config["workspaces"])}
        key = workspace_key(cwd)
        previous = value["workspaces"].get(key, {})
        if not isinstance(previous, dict):
            previous = {}
        epoch = (
            previous.get("epoch")
            if previous.get("enabled") is enabled
            and previous.get("scope_id") == scope_id
            and previous.get("server_url") == server_url
            else None
        )
        value["workspaces"][key] = {
            "enabled": enabled,
            "server_url": server_url,
            "scope_id": scope_id,
            "epoch": epoch or time_ns(),
            "workspace": str(normalized_path(cwd).resolve(strict=True)),
        }
        _save_config(path, value)
        if activate is not None:
            try:
                activate()
            except BaseException:
                _save_config(path, previous_config)
                raise


def _save_config(path: Path, value: dict[str, Any]) -> None:
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".image-config-")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def configure_capture(action: str, workspace: str, scope_id, settings) -> None:
    """Apply opt-in consent and its owned current-user lifecycle."""
    from scope_binding import bind_scope, resolve_scope_id

    if action in {"setup", "enable"}:
        scope = scope_id or resolve_scope_id(workspace, session_id=None, settings=settings, deadline=monotonic() + 6)
        from scope_binding import _post_json

        readiness = _post_json(
            "/v1/capabilities",
            {},
            method="GET",
            settings=settings,
            deadline=monotonic() + 6,
        )
        readiness = readiness.get("media")
        if not isinstance(readiness, dict):
            raise ValueError("image_server_not_ready")
        if not all(
            readiness.get(key) is True for key in ("enabled", "available", "vision_configured", "processing_enabled")
        ) or readiness.get("error_code"):
            raise ValueError("image_server_not_ready")
        # Pin the setup choice as a server-owned workspace binding, including
        # a resolved default. Image-only turns can then capture before SessionStart completes.
        bind_scope(workspace, scope, settings=settings, deadline=monotonic() + 6)
        # Setup records consent; capture still resolves the authoritative session binding.
        from collector_service import install

        write_profile(workspace, server_url=settings.server_url, scope_id=scope, enabled=True, activate=install)
    elif action == "disable":
        previous = load_config()["workspaces"].get(workspace_key(workspace), {})
        if not isinstance(previous, dict):
            previous = {}
        write_profile(
            workspace,
            server_url=settings.server_url,
            scope_id=previous.get("scope_id", "disabled"),
            enabled=False,
        )
        if not any_enabled():
            from collector_service import uninstall

            uninstall()
    elif action == "uninstall":
        if any_enabled():
            raise ValueError("image_collector_disable_workspaces_first")
        from collector_service import uninstall

        uninstall()


def capture_status(workspace: str, settings) -> dict[str, Any]:
    """Read bounded local delivery and scoped server readiness without inference."""
    from image_outbox import Outbox, outbox_path
    from scope_binding import resolve_scope_id

    profile = enabled_profile(workspace, settings.server_url)
    value: dict[str, Any] = {"enabled": profile is not None, "server_url": settings.server_url}
    if profile:
        value["scope_id"] = profile["scope_id"]
    from image_desktop_watch import watch_status

    value["desktop_collector"] = watch_status(settings.server_url)
    from collector_service import status

    value["lifecycle"] = status() if any_enabled() else {"registered": False, "error": None}
    if profile:
        from scope_binding import _post_json

        try:
            scope = resolve_scope_id(workspace, session_id=None, settings=settings, deadline=monotonic() + 3)
            value["scope_id"] = scope
            value["server_images"] = (
                _post_json(
                    "/v1/capabilities",
                    {},
                    method="GET",
                    settings=settings,
                    deadline=monotonic() + 3,
                ).get("media")
                or {}
            )
        except Exception:
            value["server_images"] = {"available": False, "error_code": "image_server_status_unavailable"}
    value["outbox"] = Outbox(outbox_path()).status(workspace_key(workspace)) if outbox_path().exists() else []
    return value


def main() -> int:
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root / "hooks"))
    sys.path.insert(0, str(root / "scripts"))
    from image_outbox import Outbox, outbox_path
    from settings import CodexPluginSettings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=("setup", "enable", "disable", "uninstall", "status", "drain", "retry", "watch")
    )
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--scope-id")
    args = parser.parse_args()
    try:
        settings = CodexPluginSettings()
        configure_capture(args.action, args.workspace, args.scope_id, settings)
        if args.action in {"drain", "retry"}:
            from image_capture_worker import drain

            if args.action == "retry" and outbox_path().exists():
                Outbox(outbox_path()).reset_delivery(workspace_key(args.workspace))
            drain(settings, wait_seconds=0)
        elif args.action == "watch":
            from image_desktop_watch import watch

            watch(settings)
            return 0
        print(json.dumps(capture_status(args.workspace, settings), ensure_ascii=False, indent=2))
    except Exception as error:
        code = (
            str(error)
            if str(error).startswith("image_") and len(str(error)) < 100
            else "image_configuration_unavailable"
        )
        print(
            "PowerContext image capture: " + code + ". Check endpoint, Scope and local permissions.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
