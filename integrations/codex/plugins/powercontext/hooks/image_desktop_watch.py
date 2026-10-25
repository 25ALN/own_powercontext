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

"""Desktop companion for hosts that do not deliver prompt or stop callbacks."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from time import monotonic, sleep, time
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))

from diagnostics import _locked  # noqa: E402
from image_attachments import MAX_PASS, MAX_RECORD, decode_record, transcript_info  # noqa: E402
from image_capture_config import (  # noqa: E402
    codex_home,
    enabled_profile,
    load_config,
    normalized_path,
    plugin_enabled,
    workspace_key,
)
from image_capture_worker import _deliver, _read_jobs  # noqa: E402
from image_outbox import Outbox, digest, outbox_path  # noqa: E402
from scope_binding import ScopeBindingError, resolve_scope_id  # noqa: E402
from settings import CodexPluginSettings  # noqa: E402


def _watch_file(server_url: str, suffix: str) -> Path:
    return outbox_path().with_name("watch-" + digest(server_url)[:16] + suffix)


def watch_status(server_url: str) -> dict[str, Any]:
    path = _watch_file(server_url, ".json")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        value["running"] = 0 <= time() - value["updated_at"] < 60
    except (OSError, ValueError, TypeError, KeyError):
        return {"running": False}
    else:
        return value


def _heartbeat(server_url: str, *, error: str | None = None) -> None:
    path = _watch_file(server_url, ".json")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"updated_at": time(), "error": error, "pid": os.getpid()}), encoding="utf-8")
    os.replace(temporary, path)


def _index_threads(settings: CodexPluginSettings) -> list[dict[str, Any]]:
    # Read only the fixed, observed metadata schema. No prompt/title/preview reads.
    index_home = Path(os.environ.get("CODEX_SQLITE_HOME", codex_home())).expanduser().resolve()
    index = index_home / "state_5.sqlite"
    if not index.is_file() or index.is_symlink():
        raise ValueError("image_desktop_index_unavailable")
    result = []
    with closing(sqlite3.connect(index.as_uri() + "?mode=ro", uri=True, timeout=0.5)) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(threads)")}
        if not {"id", "cwd", "rollout_path", "created_at_ms", "updated_at", "archived"}.issubset(columns):
            raise ValueError("image_desktop_index_unsupported")
        for cwd, profile in load_config()["workspaces"].items():
            if (
                not isinstance(profile, dict)
                or not profile.get("enabled")
                or profile.get("server_url") != settings.server_url
            ):
                continue
            native_path = Path(profile["workspace"]) if profile.get("workspace") else Path(cwd).resolve()
            if not native_path.is_dir():
                continue
            variants = {str(native_path), native_path.as_posix()}
            if os.name == "nt":
                native = str(native_path)
                variants.add("\\\\?\\UNC\\" + native[2:] if native.startswith("\\\\") else "\\\\?\\" + native)
            query = (
                "SELECT id,cwd,rollout_path,created_at_ms "
                "FROM threads WHERE cwd IN (?,?,?) AND archived=0 ORDER BY updated_at DESC LIMIT 32"
            )
            rows = connection.execute(query, tuple(variants) + (None,) * (3 - len(variants)))
            result.extend(
                {"session_id": row[0], "cwd": row[1], "transcript_path": row[2], "created": row[3]} for row in rows
            )
    return result


def _retire_idle_watches(outbox: Outbox, settings: CodexPluginSettings, entries: list[dict[str, Any]]) -> None:
    """Recycle authorized new threads outside the bounded recent host window."""
    recent = {str(normalized_path(entry["transcript_path"]).resolve()) for entry in entries}
    active = {json.loads(row["payload"])["transcript_path"] for row in outbox.jobs()}
    for row in outbox.watches():
        payload = json.loads(row["payload"])
        if payload["server_url"] != settings.server_url or row["path"] in recent or row["path"] in active:
            continue
        profile = enabled_profile(payload["workspace"], settings.server_url)
        if profile is None or profile.get("epoch") != payload.get("epoch"):
            outbox.retire_watch(row["path"])
            continue
        try:
            size = Path(row["path"]).stat().st_size
        except FileNotFoundError:
            outbox.retire_watch(row["path"])
            continue
        except OSError:
            continue  # A temporarily unreadable old file must not stop delivery.
        # A later return can replay an authorized new thread from its header.
        # Pre-consent threads keep their cursor so history is never backfilled.
        if (
            payload.get("created_at", 0) >= profile["epoch"] / 1_000_000
            and size == row["cursor"]
            and not row["skip_partial"]
        ):
            outbox.retire_watch(row["path"])


def discover(outbox: Outbox, settings: CodexPluginSettings, *, began: float) -> str | None:
    warning = None
    entries = _index_threads(settings)
    _retire_idle_watches(outbox, settings, entries)
    for entry in entries:
        path = Path(entry["transcript_path"])
        if not path.is_file():
            continue
        try:
            path, variant, identity = transcript_info(entry)
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            warning = "image_host_unsupported"
            continue
        if variant != "desktop":
            continue
        profile = enabled_profile(entry["cwd"], settings.server_url)
        if profile is None:
            continue
        payload = {
            "session_id": entry["session_id"],
            "transcript_path": str(path),
            "file_identity": identity,
            "variant": variant,
            "workspace": workspace_key(entry["cwd"]),
            "scope_id": profile["scope_id"],
            "server_url": settings.server_url,
            "host_instance_id": digest(str(codex_home())),
            "consent": "profile",
            "epoch": profile.get("epoch"),
            "created_at": entry["created"],
        }
        with path.open("rb") as stream:
            # The authorization boundary survives collector restarts. Existing
            # pre-consent conversations still begin at EOF on first enrollment.
            header_end = len(stream.readline(65537))
            end = stream.seek(0, os.SEEK_END)
            stream.seek(max(0, end - 1))
            partial = stream.read(1) != b"\n"
        consent_started = profile.get("epoch", int(began * 1_000_000_000)) / 1_000_000
        cursor = header_end if entry["created"] >= consent_started else end
        try:
            outbox.enroll(payload, cursor, skip_partial=partial and cursor == end)
        except ValueError as error:
            if str(error) != "image_watcher_limit":
                raise
            warning = "image_watcher_limit"  # Existing staged deliveries must still drain.
    return warning


def _started_turn(raw: bytes) -> str | None:
    record = decode_record(raw)
    value = record.get("payload", {})
    marker = record.get("type") == "turn_context" or (
        record.get("type") == "event_msg" and value.get("type") == "task_started"
    )
    return value["turn_id"] if marker and isinstance(value.get("turn_id"), str) else None


def _read_watch(row: dict[str, Any], outbox: Outbox, settings: CodexPluginSettings) -> None:
    payload = json.loads(row["payload"])
    if payload["server_url"] != settings.server_url:
        return
    profile = enabled_profile(payload["workspace"], settings.server_url)
    if profile is None or profile.get("epoch") != payload.get("epoch"):
        return
    path, variant, identity = transcript_info(payload)
    if variant != payload["variant"] or identity != payload["file_identity"] or path.stat().st_size < row["cursor"]:
        raise ValueError("image_desktop_transcript_changed")
    if path.stat().st_size == row["cursor"]:
        return
    with path.open("rb") as stream:
        stream.seek(row["cursor"])
        consumed = 0
        skip_partial = bool(row["skip_partial"])
        while consumed < MAX_PASS:
            offset = stream.tell()
            raw = stream.readline(min(MAX_RECORD + 1, MAX_PASS - consumed))
            if len(raw) > MAX_RECORD:
                raise ValueError("image_transcript_budget")
            if not raw.endswith(b"\n"):
                break
            consumed += len(raw)
            if not skip_partial:
                turn_id = _started_turn(raw)
                if turn_id is not None:
                    scope = resolve_scope_id(
                        payload["workspace"],
                        session_id=payload["session_id"],
                        settings=settings,
                        deadline=monotonic() + settings.http_budget_seconds,
                        allow_default=False,
                    )
                    job = payload | {"turn_id": turn_id, "prompt_digest": digest(""), "scope_id": scope}
                    outbox.register(job, offset)
            skip_partial = False
            outbox.advance_watch(row["path"], stream.tell())


def poll(outbox: Outbox, settings: CodexPluginSettings, *, began: float) -> str | None:
    if os.environ.get("POWERCONTEXT_CODEX_CAPTURE_IMAGES", "").lower() in {"false", "0", "no", "off"}:
        return None
    warning = discover(outbox, settings, began=began)
    for row in outbox.watches():
        try:
            _read_watch(row, outbox, settings)
        except FileNotFoundError:
            outbox.retire_watch(row["path"])
        except ScopeBindingError:
            warning = "image_scope_unavailable"  # Leave the cursor for recovery; never guess a destination.
        except (OSError, ValueError, TypeError, KeyError, RecursionError):
            warning = "image_desktop_read_unavailable"
    # Queue staging uses the same OS lock and receipts as CLI delivery.
    try:
        with _locked(outbox.path.with_suffix(".worker.lock")):
            _read_jobs(outbox, settings)
            _deliver(outbox, settings)
    except OSError:
        pass  # An existing CLI companion owns and drains the registered jobs.
    return warning


def watch(settings: CodexPluginSettings, *, began: float | None = None) -> None:
    if os.environ.get("POWERCONTEXT_CODEX_CAPTURE_IMAGES", "").lower() in {"false", "0", "no", "off"}:
        return
    # Use the launcher timestamp even when this child has a slow cold start.
    began = time() if began is None else began
    outbox = Outbox(outbox_path())
    try:
        with _locked(_watch_file(settings.server_url, ".lock")):
            while plugin_enabled() and any(
                isinstance(value, dict)
                and value.get("enabled") is True
                and value.get("server_url") == settings.server_url
                for value in load_config()["workspaces"].values()
            ):
                error = None
                try:
                    error = poll(outbox, settings, began=began)
                except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, RecursionError) as failure:
                    code = str(failure)
                    error = code if code.startswith("image_") and len(code) < 80 else "image_desktop_read_unavailable"
                _heartbeat(settings.server_url, error=error)
                sleep(1)
    except OSError:
        return  # A companion is already active for this client.


def launch_watch() -> None:
    if watch_status(CodexPluginSettings().server_url).get("running"):
        return
    from collector_service import launch

    launch()


if __name__ == "__main__":
    try:
        watch(CodexPluginSettings(), began=float(sys.argv[1]) if len(sys.argv) == 2 else None)
    except Exception:
        raise SystemExit(0) from None
