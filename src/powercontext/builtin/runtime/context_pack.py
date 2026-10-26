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

"""Deterministic wire format for complete and incremental context payloads."""

# The exception messages identify the specific malformed protocol field for callers.
# ruff: noqa: TRY003

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from typing import Any, cast

_PACK_SCHEMA = "powercontext.context-pack.v1"
_DELTA_SCHEMA = "powercontext.context-pack-delta.v1"


class ContextPackError(ValueError):
    """A context pack or delta is malformed or cannot be applied."""


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _encode_items(items: tuple[bytes, ...]) -> list[str]:
    return [base64.b64encode(item).decode("ascii") for item in items]


def _decode_items(value: object, field: str) -> tuple[bytes, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContextPackError(f"{field} must be a base64 string array")
    encoded_items = cast(list[str], value)
    try:
        decoded = tuple(base64.b64decode(item, validate=True) for item in encoded_items)
    except (binascii.Error, ValueError) as error:
        raise ContextPackError(f"{field} contains invalid base64") from error
    if any(
        base64.b64encode(raw).decode("ascii") != encoded for raw, encoded in zip(decoded, encoded_items, strict=True)
    ):
        raise ContextPackError(f"{field} contains non-canonical base64")
    return decoded


def _digest(items: tuple[bytes, ...]) -> str:
    unsigned = {"schema": _PACK_SCHEMA, "items": _encode_items(items)}
    return f"sha256:{hashlib.sha256(_json_bytes(unsigned)).hexdigest()}"


def _validate_bytes_tuple(items: object, field: str) -> tuple[bytes, ...]:
    if not isinstance(items, tuple) or any(type(item) is not bytes for item in items):
        raise ContextPackError(f"{field} must be a tuple of bytes")
    return cast(tuple[bytes, ...], items)


@dataclass(frozen=True, slots=True)
class ContextPack:
    """An immutable complete context represented by ordered byte chunks."""

    items: tuple[bytes, ...]

    def __post_init__(self) -> None:
        _validate_bytes_tuple(self.items, "items")

    @classmethod
    def build(cls, items: tuple[bytes, ...]) -> ContextPack:
        return cls(_validate_bytes_tuple(items, "items"))

    @classmethod
    def from_content(cls, content: str, *, chunk_bytes: int = 512) -> ContextPack:
        if not isinstance(content, str):
            raise ContextPackError("content must be text")
        if type(chunk_bytes) is not int or chunk_bytes <= 0:
            raise ContextPackError("chunk_bytes must be a positive integer")
        chunks: list[bytes] = []
        pending = bytearray()
        for character in content:
            try:
                encoded = character.encode("utf-8")
            except UnicodeEncodeError as error:
                raise ContextPackError("content is not valid UTF-8 text") from error
            if len(encoded) > chunk_bytes:
                raise ContextPackError("chunk_bytes cannot hold one UTF-8 character")
            if pending and len(pending) + len(encoded) > chunk_bytes:
                chunks.append(bytes(pending))
                pending.clear()
            pending.extend(encoded)
        if pending:
            chunks.append(bytes(pending))
        return cls.build(tuple(chunks))

    @property
    def digest(self) -> str:
        return _digest(self.items)

    def to_bytes(self) -> bytes:
        payload: dict[str, object] = {
            "schema": _PACK_SCHEMA,
            "items": _encode_items(self.items),
            "digest": self.digest,
            "byte_count": 0,
        }
        byte_count = 0
        while True:
            payload["byte_count"] = byte_count
            encoded = _json_bytes(payload)
            if len(encoded) == byte_count:
                return encoded
            byte_count = len(encoded)

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPack:
        payload = _parse_json(data)
        _require_fields(payload, {"schema", "items", "digest", "byte_count"}, "pack")
        if payload["schema"] != _PACK_SCHEMA:
            raise ContextPackError("unsupported context pack schema")
        items = _decode_items(payload["items"], "items")
        pack = cls.build(items)
        if payload["digest"] != pack.digest:
            raise ContextPackError("context pack digest mismatch")
        if type(payload["byte_count"]) is not int or payload["byte_count"] != pack.byte_count:
            raise ContextPackError("context pack byte_count mismatch")
        return pack


@dataclass(frozen=True, slots=True)
class ContextPackDelta:
    """A target pack expressed as a retained base prefix and appended chunks."""

    base_digest: str
    target_digest: str
    prefix_items: int
    appended: tuple[bytes, ...]

    @classmethod
    def between(cls, base: ContextPack, target: ContextPack) -> ContextPackDelta:
        if not isinstance(base, ContextPack) or not isinstance(target, ContextPack):
            raise ContextPackError("base and target must be ContextPack instances")
        prefix = 0
        for base_item, target_item in zip(base.items, target.items, strict=False):
            if base_item != target_item:
                break
            prefix += 1
        return cls(base.digest, target.digest, prefix, target.items[prefix:])

    @property
    def byte_count(self) -> int:
        return len(self.to_bytes())

    def to_bytes(self) -> bytes:
        _validate_delta(self)
        return _json_bytes({
            "schema": _DELTA_SCHEMA,
            "base_digest": self.base_digest,
            "target_digest": self.target_digest,
            "prefix_items": self.prefix_items,
            "appended": _encode_items(self.appended),
        })

    @classmethod
    def from_bytes(cls, data: bytes) -> ContextPackDelta:
        payload = _parse_json(data)
        _require_fields(payload, {"schema", "base_digest", "target_digest", "prefix_items", "appended"}, "delta")
        if payload["schema"] != _DELTA_SCHEMA:
            raise ContextPackError("unsupported context pack delta schema")
        appended = _decode_items(payload["appended"], "appended")
        delta = cls(payload["base_digest"], payload["target_digest"], payload["prefix_items"], appended)
        _validate_delta(delta)
        return delta


def _validate_delta(delta: ContextPackDelta) -> None:
    if not isinstance(delta.base_digest, str) or not _valid_digest(delta.base_digest):
        raise ContextPackError("invalid base digest")
    if not isinstance(delta.target_digest, str) or not _valid_digest(delta.target_digest):
        raise ContextPackError("invalid target digest")
    if type(delta.prefix_items) is not int or delta.prefix_items < 0:
        raise ContextPackError("prefix_items must be a non-negative integer")
    _validate_bytes_tuple(delta.appended, "appended")


def _valid_digest(value: str) -> bool:
    return len(value) == 71 and value.startswith("sha256:") and all(char in "0123456789abcdef" for char in value[7:])


def apply_context_pack_delta(base: ContextPack, delta: ContextPackDelta) -> ContextPack:
    if not isinstance(base, ContextPack) or not isinstance(delta, ContextPackDelta):
        raise ContextPackError("base and delta have invalid types")
    _validate_delta(delta)
    if base.digest != delta.base_digest:
        raise ContextPackError("base digest mismatch")
    if delta.prefix_items > len(base.items):
        raise ContextPackError("prefix_items exceeds base pack length")
    target = ContextPack.build((*base.items[: delta.prefix_items], *delta.appended))
    if target.digest != delta.target_digest:
        raise ContextPackError("target digest mismatch")
    return target


def _parse_json(data: bytes) -> dict[str, Any]:
    if type(data) is not bytes:
        raise ContextPackError("wire payload must be bytes")
    try:
        payload = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ContextPackError("wire payload is not valid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise ContextPackError("wire payload must be a JSON object")
    return payload


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContextPackError("wire payload contains duplicate fields")
        result[key] = value
    return result


def _require_fields(payload: dict[str, Any], fields: set[str], name: str) -> None:
    if set(payload) != fields:
        raise ContextPackError(f"{name} fields do not match the protocol")
