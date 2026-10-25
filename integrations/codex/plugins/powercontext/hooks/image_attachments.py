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

"""Version-gated rollout reader. Never interpret user text as an attachment path."""

from __future__ import annotations

import base64
import json
import os
import stat
from pathlib import Path
from typing import Any

from image_capture_config import codex_home, normalized_path
from image_outbox import digest

MAX_IMAGE = 8 * 1024 * 1024
MAX_RECORD = 48 * 1024 * 1024  # Four maximum-sized base64 images plus envelope.
MAX_PASS = 64 * 1024 * 1024
SUPPORTED_HOSTS = {
    ("0.141.0", "exec", "codex_exec"): "legacy_exec",
    ("0.162.0-alpha.2", "vscode", "Codex Desktop"): "desktop",
    ("0.162.0-alpha.2", "exec", "codex_exec"): "cli_exec",
    ("0.162.0-alpha.17.2", "vscode", "Codex Desktop"): "desktop",
    ("0.162.0-alpha.17.2", "exec", "codex_exec"): "cli_exec",
}


def decode_record(raw: bytes) -> dict[str, Any]:
    """Reject unsupported envelopes before interpreting incremental host events."""
    record = json.loads(raw)
    if not isinstance(record, dict) or not isinstance(record.get("payload", {}), dict):
        raise TypeError("image_message_unsupported")
    return record


def transcript_info(payload: dict[str, Any]) -> tuple[Path, str, list[int]]:
    path = normalized_path(payload["transcript_path"])
    if path.is_symlink():
        raise ValueError("image_transcript_untrusted")
    path = path.resolve(strict=True)
    if not path.is_relative_to(codex_home() / "sessions"):
        raise ValueError("image_transcript_untrusted")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("image_transcript_untrusted")
    with path.open("rb") as stream:
        raw = stream.readline(65537)
    if len(raw) > 65536 or not raw.endswith(b"\n"):
        raise ValueError("image_host_unsupported")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise TypeError("image_host_unsupported")
    meta = value.get("payload", {})
    if not isinstance(meta, dict) or not all(
        isinstance(meta.get(key), str) for key in ("cli_version", "source", "originator")
    ):
        raise ValueError("image_host_unsupported")
    variant = SUPPORTED_HOSTS.get((meta.get("cli_version"), meta.get("source"), meta.get("originator")))
    if value.get("type") != "session_meta" or meta.get("id") != payload["session_id"] or variant is None:
        raise ValueError("image_host_unsupported")
    return path, variant, [info.st_dev, info.st_ino]


def anchor(path: Path, turn_id: str) -> int:
    """Find only this live turn in a bounded tail; never enroll historical turns."""
    with path.open("rb") as stream:
        end = stream.seek(0, os.SEEK_END)
        start = max(0, end - MAX_RECORD)
        stream.seek(start)
        raw = stream.read(MAX_RECORD)
    if start:
        boundary = raw.find(b"\n")
        if boundary < 0:
            raise ValueError("image_transcript_budget")
        start += boundary + 1
        raw = raw[boundary + 1 :]
    result = end
    for line in raw.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break
        record = decode_record(line)
        value = record.get("payload", {})
        # Use the first marker; repeated compaction contexts must not drop messages.
        marker = record.get("type") == "turn_context" or (
            record.get("type") == "event_msg" and value.get("type") == "task_started"
        )
        if marker and value.get("turn_id") == turn_id and result == end:
            result = start
        start += len(line)
    return result


def _inline(value: Any) -> tuple[bytes, str]:
    if not isinstance(value, str) or len(value) > ((MAX_IMAGE + 2) // 3) * 4 + 64:
        raise ValueError("image_attachment_budget")
    for media in ("image/png", "image/jpeg"):
        prefix = "data:" + media + ";base64,"
        if value.startswith(prefix):
            data = base64.b64decode(value[len(prefix) :], validate=True)
            if not data or len(data) > MAX_IMAGE:
                raise ValueError("image_attachment_budget")
            if (media == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n")) or (
                media == "image/jpeg" and data.startswith(b"\xff\xd8\xff")
            ):
                return data, media
            break
    raise ValueError("image_attachment_format")


def _modern_images(value: dict[str, Any], job: dict[str, Any], offset: int) -> list[tuple[dict[str, Any], bytes]]:
    parts = value.get("content")
    if not isinstance(parts, list) or not all(isinstance(part, dict) for part in parts):
        raise ValueError("image_message_unsupported")
    images = [part for part in parts if part.get("type") == "input_image"]
    if not images:
        return []
    message = value.get("id") or "offset:" + str(offset)
    if not isinstance(message, str) or len(message) > 128:
        raise ValueError("image_message_unsupported")
    context = "\n".join(str(part.get("text", "")) for part in parts if part.get("type") == "input_text")[:2048]
    result = []
    for index, part in enumerate(images[:4]):
        data, media = _inline(part.get("image_url"))
        result.append((
            {
                "scope_id": job["scope_id"],
                "capture_key": "codex-message:"
                + digest([job["host_instance_id"], job["session_id"], job["turn_id"], message]),
                "media_type": media,
                "origin": {
                    "kind": "codex",
                    "host_instance_id": job["host_instance_id"],
                    "session_id": job["session_id"],
                    "turn_id": job["turn_id"],
                    "message_id": message,
                    "attachment_index": index,
                    "representation": "host_image",
                    "client_variant": job["variant"],
                },
                "context": {"text": context},
            },
            data,
        ))
    return result


def _legacy_images(
    record: dict[str, Any], job: dict[str, Any], reader: dict[str, Any]
) -> list[tuple[dict[str, Any], bytes]]:
    # Retain the established original-file checks and old server capture identity.
    from image_capture import _original

    value = record["payload"]
    paths, text = value.get("local_images"), value.get("message")
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths) or not isinstance(text, str):
        raise ValueError("image_message_unsupported")
    if value.get("images") or len(paths) != reader.get("image_count") or digest(text) != job["prompt_digest"]:
        raise ValueError("image_attachment_ambiguous")
    return [
        (
            {
                "scope_id": job["scope_id"],
                "capture_key": "codex:" + digest([job["session_id"], job["turn_id"]]),
                "media_type": media,
                "origin": {
                    "kind": "codex",
                    "host_instance_id": job["host_instance_id"],
                    "session_id": job["session_id"],
                    "turn_id": job["turn_id"],
                    "attachment_index": index,
                },
                "context": {"text": text[:2048]},
            },
            data,
        )
        for index, path in enumerate(paths[:4])
        for data, media in [_original(path, record["timestamp"])]
    ]


def _record_images(
    record: dict[str, Any], job: dict[str, Any], reader: dict[str, Any], offset: int
) -> tuple[list[tuple[dict[str, Any], bytes]], str | None]:
    value = record.get("payload", {})
    variant = job["variant"]
    if record.get("type") == "response_item" and value.get("type") == "message" and value.get("role") == "user":
        if variant == "legacy_exec":
            reader["image_count"] = sum(part.get("type") == "input_image" for part in value.get("content", []))
        else:
            images = _modern_images(value, job, offset)
            count = sum(part.get("type") == "input_image" for part in value.get("content", []))
            return images, "image_attachment_limit" if count > 4 else None
    if variant == "legacy_exec" and record.get("type") == "event_msg" and value.get("type") == "user_message":
        images = _legacy_images(record, job, reader)
        return images, "image_attachment_limit" if len(value.get("local_images", [])) > 4 else None
    return [], None


def scan(job_row: dict[str, Any], outbox: Any) -> bool:
    """Stage bytes atomically with each complete record's cursor; partial lines wait."""
    job = json.loads(job_row["payload"])
    reader = json.loads(job_row["reader"])
    path, variant, identity = transcript_info(job)
    if identity != job["file_identity"] or variant != job["variant"] or path.stat().st_size < job_row["cursor"]:
        raise ValueError("image_transcript_changed")
    progressed = False
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if [opened.st_dev, opened.st_ino] != identity:
            raise ValueError("image_transcript_changed")
        stream.seek(job_row["cursor"])
        size = 0
        while size < MAX_PASS:
            offset = stream.tell()
            raw = stream.readline(min(MAX_RECORD + 1, MAX_PASS - size))
            if len(raw) > MAX_RECORD:
                raise ValueError("image_transcript_budget")
            if not raw.endswith(b"\n"):
                break
            size += len(raw)
            record = decode_record(raw)
            value = record.get("payload", {})
            if record.get("type") == "turn_context" or (
                record.get("type") == "event_msg" and value.get("type") == "task_started"
            ):
                current = value.get("turn_id")
                if reader.get("active") and current != job["turn_id"]:
                    outbox.advance(job_row["id"], offset, [], reader=reader, state="done")
                    return True
                reader["active"] = current == job["turn_id"]
            images, error = _record_images(record, job, reader, offset) if reader.get("active") else ([], None)
            if error:
                reader["warning"] = error
            terminal = (
                reader.get("active")
                and record.get("type") == "event_msg"
                and value.get("type") in {"task_complete", "turn_aborted"}
                and value.get("turn_id") in {None, job["turn_id"]}
            )
            outbox.advance(
                job_row["id"],
                stream.tell(),
                images,
                reader=reader,
                error=reader.get("warning"),
                state="done" if terminal else "watching",
            )
            progressed = True
            if terminal:
                return True
    return progressed
