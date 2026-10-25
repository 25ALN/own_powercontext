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

"""Standard Codex setup and doctor boundary for client-side image consent."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from shutil import which
from typing import Any

from powercontext.client.transport_policy import parse_client_boolean


def plugin_root(marketplace: str, version: str) -> Path:
    if any(part in {"", ".", ".."} or "/" in part or "\\" in part for part in (marketplace, version)):
        raise ValueError("Invalid Codex plugin cache identity")  # noqa: TRY003
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    return home / "plugins" / "cache" / marketplace / "powercontext" / version


def run_image_config(root: Path, action: str) -> dict[str, Any]:
    from powercontext.cli.system import SetupError

    script = root / "image_capture_config.py"
    if not script.is_file():
        raise SetupError("The Codex plugin has no image collector. Update the plugin and rerun setup.")  # noqa: TRY003
    executable = which("uv")
    if executable is None:
        raise SetupError("Install uv before configuring the Codex image collector")  # noqa: TRY003
    from powercontext.cli.transport import setup_environment

    arguments = []
    scope = (setup_environment() | dict(os.environ)).get("POWERCONTEXT_CODEX_SCOPE_ID")
    if action == "setup" and scope:
        arguments = ["--scope-id", scope]
    try:
        result = subprocess.run(  # noqa: S603 - installed packaged script; no shell.
            [
                executable,
                "run",
                "--frozen",
                "--quiet",
                "--project",
                str(root),
                "python",
                str(script),
                action,
                "--workspace",
                str(Path.cwd()),
                *arguments,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=35,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise SetupError(result.stderr.strip() or "Codex image collector setup failed")
        value = json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise SetupError(  # noqa: TRY003 - local installation diagnostic.
            "Cannot manage the Codex image collector; check uv, plugin and user service permissions"
        ) from error
    if not isinstance(value, dict):
        raise SetupError("Codex image collector returned invalid status")  # noqa: TRY003
    return value


def configure_images(root: Path, selected: bool | None = None) -> dict[str, Any] | None:
    from powercontext.cli.transport import setup_environment

    values = setup_environment() | dict(os.environ)
    declaration = values.get("POWERCONTEXT_CODEX_CAPTURE_IMAGES")
    if selected is None:
        try:
            requested = parse_client_boolean(declaration) if declaration is not None else False
        except ValueError as error:
            from powercontext.cli.system import SetupError

            raise SetupError("POWERCONTEXT_CODEX_CAPTURE_IMAGES must be a boolean") from error  # noqa: TRY003
        if not requested:
            return None  # Default-off configuration must not revoke saved consent.
        selected = True
    if selected and values.get("POWERCONTEXT_SERVER_IMAGES_ENABLED", "true").lower() in {"false", "0", "no", "off"}:
        from powercontext.cli.system import SetupError

        raise SetupError("Enable POWERCONTEXT_SERVER_IMAGES_ENABLED before authorizing Codex image capture")  # noqa: TRY003
    if not selected and not (root / "image_capture_config.py").is_file():
        return {"enabled": False}
    state = run_image_config(root, "setup" if selected else "disable")
    return {key: state[key] for key in ("enabled", "server_url", "scope_id", "lifecycle") if key in state}


def _capture_selected() -> bool:
    from powercontext.cli.transport import setup_environment

    declaration = (setup_environment() | dict(os.environ)).get("POWERCONTEXT_CODEX_CAPTURE_IMAGES")
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    path = home / "powercontext" / "image-capture-config.json"
    if not path.exists():
        return parse_client_boolean(declaration) if declaration is not None else False
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
    profile = value["workspaces"].get(os.path.normcase(str(Path.cwd().resolve())))
    if profile is None:
        return parse_client_boolean(declaration) if declaration is not None else False
    if not isinstance(profile, dict) or type(profile.get("enabled")) is not bool:
        raise ValueError("image_config_invalid_profile")
    return profile.get("enabled") is True


def image_diagnostic(plugin: dict[str, Any]):
    from powercontext.cli.system import Diagnostic, DiagnosticStatus

    try:
        selected = _capture_selected()
    except (OSError, ValueError, TypeError, KeyError, RecursionError) as error:
        return Diagnostic(
            status=DiagnosticStatus.FAILED, detail="image consent configuration is invalid: " + type(error).__name__
        )
    if not selected:
        return Diagnostic(status=DiagnosticStatus.OK, detail="image capture is off for this workspace")
    identity = plugin.get("pluginId", "")
    version = plugin.get("version")
    if not isinstance(identity, str) or "@" not in identity or not isinstance(version, str):
        return Diagnostic(
            status=DiagnosticStatus.FAILED, detail="image collector metadata is unavailable; update the plugin"
        )
    try:
        state = run_image_config(plugin_root(identity.split("@", 1)[1], version), "status")
    except (ValueError, RuntimeError) as error:
        return Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error))
    if not state.get("enabled"):
        return Diagnostic(
            status=DiagnosticStatus.FAILED, detail="image capture has not been authorized; rerun setup codex"
        )
    collector = state.get("desktop_collector", {})
    manager = state.get("lifecycle", {})
    server = state.get("server_images", {})
    ready = (
        collector.get("running")
        and not collector.get("error")
        and manager.get("registered")
        and not manager.get("error")
        and server.get("enabled")
        and server.get("available")
        and server.get("vision_configured")
        and server.get("processing_enabled")
        and not server.get("error_code")
    )
    return Diagnostic(
        status=DiagnosticStatus.OK if ready else DiagnosticStatus.FAILED,
        detail="Codex image capture and client collector are ready"
        if ready
        else "Codex image capture is not ready; inspect collector, model and Scope below; rerun setup after repair",
        checks={
            "scope_id": str(state.get("scope_id")),
            "collector": json.dumps(collector),
            "lifecycle": json.dumps(manager),
            "server": json.dumps(server),
        },
    )
