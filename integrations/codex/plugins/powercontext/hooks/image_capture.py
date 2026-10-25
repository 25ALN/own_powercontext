# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Opt-in Codex image capture; short hooks register durable client delivery."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import stat
import sys
import threading
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts"))
sys.path.insert(0, str(_PLUGIN_ROOT / "hooks"))

from diagnostics import _locked  # noqa: E402
from image_capture_config import any_enabled, enabled_profile, workspace_disabled, workspace_key  # noqa: E402
from scope_binding import ScopeBindingError, _post_json, resolve_scope_id  # noqa: E402
from settings import CodexPluginSettings  # noqa: E402

SUPPORTED_VERSION = "0.141.0"
MAX_WINDOW = 4 * 1024 * 1024
MAX_IMAGE = 8 * 1024 * 1024


class CaptureError(ValueError):
    """A stable diagnostic without private paths, bytes or provider content."""


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _transcript(payload: dict[str, Any], home: Path) -> tuple[Path, dict[str, Any]]:
    path = Path(payload["transcript_path"])
    if path.is_symlink():
        raise CaptureError("image_transcript_untrusted")
    path = path.resolve(strict=True)
    if not path.is_relative_to((home / "sessions").resolve()):
        raise CaptureError("image_transcript_untrusted")
    with path.open("rb") as stream:
        raw = stream.readline(65537)
    if not raw.endswith(b"\n") or len(raw) > 65536:
        raise CaptureError("image_transcript_unsupported")
    header = json.loads(raw)
    metadata = header.get("payload", {})
    if (
        header.get("type") != "session_meta"
        or metadata.get("id") != payload["session_id"]
        or metadata.get("cli_version") != SUPPORTED_VERSION
        or metadata.get("source") != "exec"
        or metadata.get("originator") != "codex_exec"
    ):
        raise CaptureError("image_host_unsupported")
    return path, metadata


def _identity(path: Path) -> list[int]:
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise CaptureError("image_transcript_untrusted")
    return [info.st_dev, info.st_ino]


def _arm_offset(path: Path, turn_id: str) -> int:
    # The verified host can append turn_context before the submit command
    # starts. Include that exact marker, using only a bounded EOF tail.
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        start = max(0, size - 65536)
        stream.seek(start)
        tail = stream.read(65536)
    if start:
        boundary = tail.find(b"\n") + 1
        start += boundary
        tail = tail[boundary:]
    offset = size
    for line in tail.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        value = json.loads(line)
        if value.get("type") == "turn_context" and value.get("payload", {}).get("turn_id") == turn_id:
            offset = start
        start += len(line)
    return offset


def _save(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(state, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _attachments(path: Path, state: dict[str, Any]) -> tuple[list[str], str, str]:
    if _identity(path) != state["file_identity"] or path.stat().st_size < state["offset"]:
        raise CaptureError("image_transcript_changed")
    with path.open("rb") as stream:
        stream.seek(state["offset"])
        raw = stream.read(MAX_WINDOW + 1)
    if len(raw) > MAX_WINDOW:
        raise CaptureError("image_transcript_budget")
    if raw and not raw.endswith(b"\n"):
        raise CaptureError("image_transcript_incomplete")
    current = None
    users: list[tuple[list[str], str, str]] = []
    image_count = None
    for line in raw.splitlines():
        record = json.loads(line)
        value = record.get("payload", {})
        if record.get("type") == "turn_context":
            current = value.get("turn_id")
            image_count = None
        if current != state["turn_id"]:
            continue
        if record.get("type") == "response_item" and value.get("type") == "message" and value.get("role") == "user":
            image_count = sum(part.get("type") == "input_image" for part in value.get("content", []))
        if record.get("type") == "event_msg" and value.get("type") == "user_message":
            users.append(_user_message(record, state, image_count))
    if len(users) != 1:
        raise CaptureError("image_turn_unavailable")
    return users[0]


def _user_message(record: dict[str, Any], state: dict[str, Any], image_count: int | None) -> tuple[list[str], str, str]:
    value = record["payload"]
    paths, text = value.get("local_images"), value.get("message")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths) or not isinstance(text, str):
        raise CaptureError("image_transcript_unsupported")
    if value.get("images") or image_count != len(paths) or _digest(text) != state["prompt_digest"]:
        raise CaptureError("image_attachment_ambiguous")
    return paths, text, record["timestamp"]


def _original(name: str, submitted_at: str) -> tuple[bytes, str]:
    path = Path(name)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_IMAGE:
        raise CaptureError("image_original_unavailable")
    submitted = datetime.fromisoformat(submitted_at.replace("Z", "+00:00")).timestamp()
    if before.st_mtime > submitted:
        raise CaptureError("image_original_changed")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        data = stream.read(MAX_IMAGE + 1)
        after = os.fstat(stream.fileno())
    if (
        (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        or (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
        or len(data) != before.st_size
        or len(data) > MAX_IMAGE
    ):
        raise CaptureError("image_original_changed")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return data, "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return data, "image/jpeg"
    raise CaptureError("image_original_format")


def _legacy_receipt(
    path: Path, payload: dict[str, Any], scope: str, settings: CodexPluginSettings
) -> dict[str, Any] | None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 65536:
        return None
    previous = json.loads(path.read_text(encoding="utf-8"))
    if all((
        previous.get("version") == 1,
        previous.get("turn_id") == payload["turn_id"],
        previous.get("scope_id") == scope,
        previous.get("server") == _digest(settings.server_url),
        previous.get("complete") is True,
    )):
        return {"status": "replayed", "accepted": previous.get("accepted", [])}
    return None


def enqueue_capture(payload: dict[str, Any], settings: CodexPluginSettings, *, deadline: float) -> dict[str, Any]:
    from image_attachments import anchor, transcript_info
    from image_capture_worker import launch
    from image_outbox import Outbox, outbox_path

    if payload.get("hook_event_name") not in {"UserPromptSubmit", "Stop", "Interrupt"}:
        return {"status": "disabled"}
    for name in ("session_id", "turn_id", "cwd", "transcript_path"):
        if (
            not isinstance(payload.get(name), str)
            or not payload[name]
            or len(payload[name]) > (4096 if name in {"cwd", "transcript_path"} else 128)
        ):
            raise CaptureError("image_hook_invalid")
    if workspace_disabled(payload["cwd"]):
        return {"status": "disabled"}
    profile = enabled_profile(payload["cwd"], settings.server_url)
    if os.environ.get("POWERCONTEXT_CODEX_CAPTURE_IMAGES", "").lower() in {"false", "0", "no", "off"}:
        return {"status": "disabled"}
    if profile is None and not settings.capture_images:
        return {"status": "disabled"}
    scope = resolve_scope_id(
        payload["cwd"], session_id=payload["session_id"], settings=settings, deadline=deadline, allow_default=False
    )
    path, variant, identity = transcript_info(payload)
    prompt = payload.get("prompt", "")
    if not isinstance(prompt, str) or len(prompt) > 65536:
        raise CaptureError("image_context_budget")
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
    if variant == "legacy_exec":
        legacy_path = home / "powercontext" / "image-capture" / (_digest(payload["session_id"]) + ".json")
        receipt = _legacy_receipt(legacy_path, payload, scope, settings)
        if receipt is not None:
            return receipt
    job = {
        "session_id": payload["session_id"],
        "turn_id": payload["turn_id"],
        "transcript_path": str(path),
        "workspace": workspace_key(payload["cwd"]),
        "scope_id": scope,
        "server_url": settings.server_url,
        "host_instance_id": _digest(str(home)),
        "variant": variant,
        "file_identity": identity,
        "prompt_digest": _digest(prompt),
        "consent": "profile" if profile else "environment",
        "epoch": profile.get("epoch") if profile else None,
    }
    outbox = Outbox(outbox_path())
    job_id = outbox.register(job, lambda: anchor(path, payload["turn_id"]))
    launch()
    return {"status": "queued", "job_id": job_id}


def capture(
    payload: dict[str, Any], settings: CodexPluginSettings, *, deadline: float, background: bool = False
) -> dict[str, Any]:
    """Checkpoint or submit one explicitly bound turn; never wait for inference."""
    if background:
        return enqueue_capture(payload, settings, deadline=deadline)
    if isinstance(payload.get("transcript_path"), str):
        from image_attachments import transcript_info

        try:
            _, variant, _ = transcript_info(payload)
        except ValueError as error:
            raise CaptureError(str(error)) from error
        if variant != "legacy_exec":
            return enqueue_capture(payload, settings, deadline=deadline)
    return _legacy_capture(payload, settings, deadline=deadline)


def _legacy_capture(payload: dict[str, Any], settings: CodexPluginSettings, *, deadline: float) -> dict[str, Any]:
    if not settings.capture_images or payload.get("hook_event_name") not in {"UserPromptSubmit", "Stop"}:
        return {"status": "disabled"}
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
    for name in ("session_id", "turn_id", "cwd", "transcript_path"):
        if (
            not isinstance(payload.get(name), str)
            or not payload[name]
            or len(payload[name]) > (4096 if name in {"cwd", "transcript_path"} else 128)
        ):
            raise CaptureError("image_hook_invalid")
    path, _ = _transcript(payload, home)
    directory = home / "powercontext" / "image-capture"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path = directory / (_digest(payload["session_id"]) + ".json")
    with _locked(state_path.with_suffix(".lock")):
        scope = resolve_scope_id(
            payload["cwd"], session_id=payload["session_id"], settings=settings, deadline=deadline, allow_default=False
        )
        if payload["hook_event_name"] == "UserPromptSubmit":
            return _arm_turn(path, state_path, payload, settings, scope)
        if not state_path.is_file():
            raise CaptureError("image_cursor_missing")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        if state.get("version") != 1 or state.get("turn_id") != payload["turn_id"]:
            raise CaptureError("image_cursor_mismatch")
        if state.get("scope_id") != scope or state.get("server") != _digest(settings.server_url):
            raise CaptureError("image_scope_changed")
        return _submit_turn(path, state_path, state, payload, settings, deadline)


def _arm_turn(
    path: Path, state_path: Path, payload: dict[str, Any], settings: CodexPluginSettings, scope: str
) -> dict[str, Any]:
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or len(prompt) > 65536:
        raise CaptureError("image_context_budget")
    state = {
        "version": 1,
        "turn_id": payload["turn_id"],
        "scope_id": scope,
        "server": _digest(settings.server_url),
        "file_identity": _identity(path),
        "offset": _arm_offset(path, payload["turn_id"]),
        "prompt_digest": _digest(prompt),
        "accepted": [],
    }
    if state_path.is_file():
        previous = json.loads(state_path.read_text(encoding="utf-8"))
        if previous.get("version") != 1:
            raise CaptureError("image_cursor_unsupported")
        if previous.get("turn_id") == state["turn_id"]:
            if any(previous.get(key) != state[key] for key in ("scope_id", "server", "file_identity", "prompt_digest")):
                raise CaptureError("image_capture_conflict")
            return {"status": "armed"}
    _save(state_path, state)
    return {"status": "armed"}


def _submit_turn(
    path: Path,
    state_path: Path,
    state: dict[str, Any],
    payload: dict[str, Any],
    settings: CodexPluginSettings,
    deadline: float,
) -> dict[str, Any]:
    if state.get("complete"):
        return {"status": "replayed", "accepted": state["accepted"]}
    paths, text, submitted = _attachments(path, state)
    accepted = state["accepted"]
    for index, name in enumerate(paths[:4]):
        if index < len(accepted):
            continue
        if monotonic() >= deadline:
            raise CaptureError("image_capture_timeout")
        data, media_type = _original(name, submitted)
        response = _post_json(
            "/v1/images/capture",
            {
                "scope_id": state["scope_id"],
                "capture_key": "codex:" + _digest([payload["session_id"], payload["turn_id"]]),
                "media_type": media_type,
                "bytes_base64": base64.b64encode(data).decode("ascii"),
                "origin": {
                    "kind": "codex",
                    "host_instance_id": _digest(str(state_path.parent.parent.parent)),
                    "session_id": payload["session_id"],
                    "turn_id": payload["turn_id"],
                    "attachment_index": index,
                },
                "context": {"text": text[:2048]},
            },
            settings=settings,
            deadline=deadline,
        )
        if response.get("status") != "accepted" or not isinstance(response.get("request_id"), str):
            raise CaptureError("image_acceptance_invalid")
        accepted.append({"index": index, "task_id": response["request_id"]})
        _save(state_path, state)
    state["complete"] = True
    _save(state_path, state)
    return {
        "status": "accepted" if paths else "no_images",
        "accepted": accepted,
        "not_accepted": max(0, len(paths) - 4),
    }


def main(settings: CodexPluginSettings | None = None) -> int:
    outcome: list[dict[str, Any]] = []
    try:
        if (
            settings is None
            and os.environ.get("POWERCONTEXT_CODEX_CAPTURE_IMAGES", "false").lower()
            not in {
                "true",
                "1",
                "yes",
                "on",
            }
            and not any_enabled()
        ):
            return 0
        settings = CodexPluginSettings() if settings is None else settings
        if not settings.capture_images and not any_enabled():
            return 0
        raw = sys.stdin.read(131073)
        if len(raw) > 131072:
            raise CaptureError("image_hook_budget")  # noqa: TRY301 - bounded stdin at the hook boundary
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise CaptureError("image_hook_invalid")  # noqa: TRY301 - validate envelope before event dispatch.
        budget = (
            min(settings.image_capture_budget_seconds, 1.5)
            if payload.get("hook_event_name") == "Interrupt"
            else settings.image_capture_budget_seconds
        )
        deadline = monotonic() + budget

        def run() -> None:
            try:
                outcome.append(capture(payload, settings, deadline=deadline, background=True))
            except CaptureError as error:
                outcome.append({"error": str(error)})
            except (
                ScopeBindingError,
                OSError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
                RecursionError,
                sqlite3.Error,
            ):
                outcome.append({"error": "image_capture_unavailable"})

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        worker.join(max(0, deadline - monotonic()))
        result = outcome[0] if outcome else {"error": "image_capture_timeout"}
        if result.get("error") or result.get("not_accepted"):
            print(
                json.dumps({
                    "systemMessage": "PowerContext image capture: "
                    + str(result.get("error", "image_attachment_limit"))
                    + ". Use explicit image upload for unaccepted attachments."
                })
            )
    except (CaptureError, ValueError, OSError, TypeError, RecursionError):
        print(json.dumps({"systemMessage": "PowerContext image capture unavailable; use explicit image upload."}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
