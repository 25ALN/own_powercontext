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

"""Capture the complete visible history of a selected Pi session branch."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, JsonValue

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

PI_EVENT_SOURCE_NAME = "pi-event"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"


class PiSessionInput(BaseModel):
    """One complete Pi v3 JSONL session and an optional selected leaf."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event on a selected Pi session history path."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSource(Source):
    """A captured Pi event with its complete original record."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


class PiEventSourceAdapter:
    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


class PiEventTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_DEFINITION: SourceDefinition[PiEventInput, PiEventSource, PiEventInput] = AdapterSourceDefinition(
    PiEventSourceAdapter(), projections=(PiEventTextEvidenceProjection(),)
)


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Return a stable, compact identity for one session event."""

    if not isinstance(session_id, str) or not session_id or not isinstance(event_id, str) or not event_id:
        raise ValueError("session_id and event_id must be non-empty strings")  # noqa: TRY003
    session_bytes = session_id.encode("utf-8")
    event_bytes = event_id.encode("utf-8")
    payload = len(session_bytes).to_bytes(8, "big") + session_bytes + event_bytes
    source_id = f"pi:{hashlib.sha256(payload).hexdigest()}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("Pi event identity exceeds the Source limit")  # noqa: TRY003
    return source_id


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    """Parse a complete Pi v3 archive and return the selected root-to-leaf history."""

    session_id, records, leaf_id = _parse_archive(value)
    path: list[dict[str, Any]] = []
    current = leaf_id
    while current is not None:
        record = records[current]
        path.append(record)
        current = record["parentId"]
    path.reverse()

    events: list[PiEventInput] = []
    for record in path:
        event = _visible_event(session_id, record)
        if event is not None:
            events.append(event)
    return tuple(events)


class PiSessionConnector:
    """Incrementally submit visible events from one selected Pi session branch."""

    name = PI_SESSION_CONNECTOR_NAME
    version = "1"
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        raw = self.path.read_bytes()
        archive_input = PiSessionInput(content=raw, leaf_id=self.leaf_id)
        session_id, records, leaf_id = _parse_archive(archive_input)

        previous_session_id, previous = _decode_checkpoint(session.checkpoint)
        if previous_session_id is not None and previous_session_id != session_id:
            raise ValueError("Pi session identity changed after events were imported")  # noqa: TRY003
        current_records = {
            event_id: _record_fingerprint(record)
            for event_id, record in records.items()
            if _visible_event(session_id, record) is not None
        }
        for event_id, fingerprint in previous.items():
            if current_records.get(event_id) != fingerprint:
                raise ValueError(f"previously imported Pi event was changed or removed: {event_id}")  # noqa: TRY003

        path_records: list[dict[str, Any]] = []
        current = leaf_id
        while current is not None:
            record = records[current]
            path_records.append(record)
            current = record["parentId"]
        path_records.reverse()

        imported = dict(previous)
        for record in path_records:
            event = _visible_event(session_id, record)
            if event is None:
                continue
            source_id = pi_event_source_id(event.session_id, event.event_id)
            fingerprint = _record_fingerprint(record)
            if event.event_id in imported:
                continue
            await session.submit(source_id, PI_EVENT_SOURCE_NAME, event)
            imported[event.event_id] = fingerprint

        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"version": 1, "session_id": session_id, "events": imported},
        )


def _parse_archive(value: PiSessionInput) -> tuple[str, dict[str, dict[str, Any]], str | None]:
    records_list = _decode_jsonl(value.content)
    session_id = _validate_session_header(records_list[0])
    records = _index_events(records_list[1:])
    leaf_id = value.leaf_id if value.leaf_id is not None else (next(reversed(records)) if records else None)
    if leaf_id is not None and leaf_id not in records:
        raise ValueError(f"Pi session leaf does not exist: {leaf_id}")  # noqa: TRY003
    return session_id, records, leaf_id


def _decode_jsonl(content: bytes) -> list[dict[str, Any]]:
    try:
        text = content.decode("utf-8-sig")
    except (AttributeError, UnicodeDecodeError) as error:
        raise ValueError("Pi session content must be valid UTF-8") from error  # noqa: TRY003

    records_list: list[dict[str, Any]] = []
    try:
        for line in text.splitlines():
            if line.strip():
                record = json.loads(line, parse_constant=_reject_json_constant)
                if not isinstance(record, dict):
                    _invalid_json_line()
                records_list.append(record)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise ValueError("invalid Pi session JSONL") from error  # noqa: TRY003

    if not records_list:
        raise ValueError("Pi session must contain a session header")  # noqa: TRY003
    return records_list


def _validate_session_header(header: dict[str, Any]) -> str:
    session_id = header.get("id")
    if header.get("type") != "session" or header.get("version") != 3:
        raise ValueError("Pi session header must have type='session' and version=3")  # noqa: TRY003
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Pi session header id must be a non-empty string")  # noqa: TRY003
    return session_id


def _index_events(records_list: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for record in records_list:
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id:
            raise ValueError("Pi event id must be a non-empty string")  # noqa: TRY003
        if event_id in records:
            raise ValueError(f"duplicate Pi event id: {event_id}")  # noqa: TRY003
        if "parentId" not in record:
            raise ValueError(f"Pi event {event_id!r} is missing parentId")  # noqa: TRY003
        parent_id = record["parentId"]
        if parent_id is not None and (not isinstance(parent_id, str) or parent_id not in records):
            raise ValueError(f"Pi event {event_id!r} has a missing or invalid parent")  # noqa: TRY003
        records[event_id] = record
    return records


def _invalid_json_line() -> None:
    raise ValueError("Pi session lines must contain JSON objects")  # noqa: TRY003


def _visible_event(session_id: str, record: dict[str, Any]) -> PiEventInput | None:
    event_type = record.get("type")
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("role"), str) or not message["role"]:
            return None
        role = message["role"]
        body = _message_body(role, message)
    elif event_type in {"compaction", "branch_summary"}:
        role = event_type
        body_value = record.get("summary")
        body = body_value if isinstance(body_value, str) else None
    elif event_type == "custom_message":
        role = "custom"
        body = _visible_text(record.get("content"))
    else:
        return None

    if body is None or not body.strip():
        return None
    return PiEventInput(
        session_id=session_id,
        event_id=record["id"],
        parent_id=record["parentId"],
        event_type=event_type,
        role=role,
        content=f"{role}: {body}",
        record=record,
    )


def _message_body(role: str, message: dict[str, Any]) -> str | None:
    if role == "toolResult":
        text = _visible_text(message.get("content"))
        if not text.strip():
            return None
        return f"[tool_result {message.get('toolName', '')} {message.get('toolCallId', '')}] {text}"
    if role == "bashExecution":
        command = message.get("command", "")
        output = message.get("output", "")
        command_text = command if isinstance(command, str) else str(command)
        output_text = output if isinstance(output, str) else str(output)
        return f"$ {command_text}\n{output_text}" if command_text or output_text else None

    content = message.get("content")
    if isinstance(content, str):
        return content or None
    if not isinstance(content, list):
        return None
    pieces = [_message_block_text(block) for block in content]
    visible = [piece for piece in pieces if piece is not None]
    return "\n".join(visible) if visible else None


def _message_block_text(block: object) -> str | None:
    if not isinstance(block, dict):
        return None
    if block.get("type") == "text" and isinstance(block.get("text"), str):
        return cast(str, block.get("text"))
    if block.get("type") == "toolCall":
        arguments = json.dumps(block.get("arguments", {}), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return f"[tool_call {block.get('name', '')} {block.get('id', '')}] {arguments}"
    return None


def _visible_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        pieces: list[str] = []
        for block in value:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                pieces.append(cast(str, block.get("text")))
        return "\n".join(pieces)
    return ""


def _record_fingerprint(record: dict[str, Any]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _decode_checkpoint(checkpoint: JsonValue | None) -> tuple[str | None, dict[str, str]]:
    if checkpoint is None:
        return None, {}
    if (
        not isinstance(checkpoint, dict)
        or checkpoint.get("version") != 1
        or not isinstance(checkpoint.get("session_id"), str)
        or not isinstance(checkpoint.get("events"), dict)
    ):
        raise ValueError("invalid Pi session checkpoint")  # noqa: TRY003
    events = checkpoint["events"]
    if not all(isinstance(source_id, str) and isinstance(fingerprint, str) for source_id, fingerprint in events.items()):
        raise ValueError("invalid Pi session checkpoint event map")  # noqa: TRY003
    return checkpoint["session_id"], events


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"invalid JSON constant: {value}")  # noqa: TRY003


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PI_SESSION_CONNECTOR_NAME",
    "PiEventInput",
    "PiEventSource",
    "PiEventSourceAdapter",
    "PiEventTextEvidenceProjection",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
