# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Transaction-owned media persistence, with no decoder, model or scheduler policy."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, insert, select, tuple_, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.images.identity import image_profile_digest
from powercontext.builtin.media.errors import MediaError
from powercontext.builtin.media.models import (
    ACTIVE_REQUEST_STATES,
    MAX_MEDIA_RECEIPT_BYTES,
    ImageProcessingInput,
    MediaAsset,
    MediaCaptureReceipt,
    MediaProcessingRequest,
)
from powercontext.builtin.persistence.media_tables import MEDIA_ASSETS, MEDIA_CAPACITY, MEDIA_REQUESTS


def media_json(value: BaseModel) -> bytes:
    return value.model_dump_json(by_alias=True).encode("utf-8")


def _receipt_matches(value, row):
    if value.operation == "capture":
        receipt = MediaCaptureReceipt.model_validate_json(row["receipt"])
        return receipt.request_id == value.request_id and receipt.original == value.original
    receipt = MediaProcessingRequest.model_validate_json(row["receipt"])
    return (
        receipt.request_id == value.request_id
        and receipt.original == value.original
        and receipt.profile_digest == value.profile_digest
        and receipt.state.value == "pending"
        and receipt.state_version == 1
        and receipt.operation == value.operation
    )


def media_request_from_row(row: Mapping[Any, Any]) -> MediaProcessingRequest:
    try:
        value = MediaProcessingRequest.model_validate_json(row["payload"])
        handler = ImageProcessingInput.model_validate_json(row["input"])
        active = value.state.value in ACTIVE_REQUEST_STATES
        if (
            value.scope_id != row["scope_id"]
            or value.request_id != row["request_id"]
            or value.operation != row["operation"]
            or value.processing_kind != row["processing_kind"]
            or value.state.value != row["state"]
            or value.state_version != row["state_version"]
            or value.original.source.source_type != row["original_source_type"]
            or value.original.source.source_id != row["original_source_id"]
            or value.profile_digest != row["profile_digest"]
            or value.profile_digest != image_profile_digest(handler.profile)
            or handler.processing_kind != value.processing_kind
            or handler.schema_ != row["handler_schema"]
            or (row["active_key"] == "active") != active
            or value.max_attempts != handler.profile.max_attempts
            or not _receipt_matches(value, row)
        ):
            raise MediaError(500, "media_request_corrupt")
    except (ValidationError, ValueError, TypeError, KeyError) as error:
        raise MediaError(500, "media_request_corrupt") from error
    else:
        return value


def _asset_verification_batches(headers):
    keys = []
    size = 0
    for row in headers:
        byte_size = row["byte_size"]
        if type(byte_size) is not int or not 0 < byte_size <= 8 * 1024 * 1024:
            raise MediaError(500, "media_asset_corrupt")
        if keys and size + byte_size > 16 * 1024 * 1024:
            yield tuple(keys)
            keys = []
            size = 0
        keys.append((row["scope_id"], row["byte_digest"]))
        size += byte_size
    if keys:
        yield tuple(keys)


class MediaRepository:
    """All methods join the caller's transaction; admission locks global then Scope."""

    async def lock_capacity(self, connection: AsyncConnection, scope_id: str) -> dict[str, Mapping[Any, Any]]:
        if connection.dialect.name != "sqlite":
            raise MediaError(503, "media_backend_unsupported")
        # This first write reserves SQLite's writer slot before any admission read.
        for boundary, key in (("global", ""), ("scope", scope_id)):
            await connection.execute(
                sqlite_insert(MEDIA_CAPACITY)
                .values(boundary=boundary, scope_id=key, retained_bytes=0, active_requests=0)
                .on_conflict_do_nothing()
            )
        rows = (
            (
                await connection.execute(
                    select(MEDIA_CAPACITY).where(
                        (MEDIA_CAPACITY.c.boundary == "global")
                        | ((MEDIA_CAPACITY.c.boundary == "scope") & (MEDIA_CAPACITY.c.scope_id == scope_id))
                    )
                )
            )
            .mappings()
            .all()
        )
        return {row["boundary"]: row for row in rows}

    async def charge(
        self,
        connection: AsyncConnection,
        scope_id: str,
        *,
        retained_bytes: int,
        active_requests: int,
        max_bytes: int,
        max_requests: int,
        max_scope_requests: int,
    ) -> None:
        rows = await self.lock_capacity(connection, scope_id)
        if rows["global"]["retained_bytes"] + retained_bytes > max_bytes:
            raise MediaError(503, "media_capacity_exceeded")
        if (
            rows["global"]["active_requests"] + active_requests > max_requests
            or rows["scope"]["active_requests"] + active_requests > max_scope_requests
        ):
            raise MediaError(503, "media_queue_full")
        for boundary, key in (("global", ""), ("scope", scope_id)):
            row = rows[boundary]
            if row["retained_bytes"] + retained_bytes < 0 or row["active_requests"] + active_requests < 0:
                raise MediaError(500, "media_capacity_corrupt")
            await connection.execute(
                update(MEDIA_CAPACITY)
                .where(MEDIA_CAPACITY.c.boundary == boundary, MEDIA_CAPACITY.c.scope_id == key)
                .values(
                    retained_bytes=MEDIA_CAPACITY.c.retained_bytes + retained_bytes,
                    active_requests=MEDIA_CAPACITY.c.active_requests + active_requests,
                )
            )

    async def release_request(self, connection: AsyncConnection, scope_id: str) -> None:
        rows = await self.lock_capacity(connection, scope_id)
        if any(row["active_requests"] < 1 for row in rows.values()):
            raise MediaError(500, "media_capacity_corrupt")
        for boundary, key in (("global", ""), ("scope", scope_id)):
            await connection.execute(
                update(MEDIA_CAPACITY)
                .where(MEDIA_CAPACITY.c.boundary == boundary, MEDIA_CAPACITY.c.scope_id == key)
                .values(active_requests=MEDIA_CAPACITY.c.active_requests - 1)
            )

    async def asset(self, connection: AsyncConnection, scope_id: str, asset_id: str) -> tuple[MediaAsset, bytes]:
        if not asset_id.startswith("ma_") or len(asset_id) != 67:
            raise MediaError(404, "media_asset_not_found")
        row = (
            (
                await connection.execute(
                    select(MEDIA_ASSETS).where(
                        MEDIA_ASSETS.c.scope_id == scope_id,
                        MEDIA_ASSETS.c.byte_digest == "sha256:" + asset_id[3:],
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise MediaError(404, "media_asset_not_found")
        return self.checked_asset(row)

    @staticmethod
    def checked_asset(row: Mapping[Any, Any]) -> tuple[MediaAsset, bytes]:
        try:
            asset = MediaAsset.model_validate_json(row["descriptor"])
            data = bytes(row["bytes"])
            if (
                len(data) != asset.byte_size
                or len(data) != row["byte_size"]
                or asset.digest != row["byte_digest"]
                or asset.media_type != row["media_type"]
                or "sha256:" + hashlib.sha256(data).hexdigest() != asset.digest
            ):
                raise MediaError(500, "media_asset_corrupt")
        except (ValidationError, TypeError, ValueError, KeyError) as error:
            raise MediaError(500, "media_asset_corrupt") from error
        else:
            return asset, data

    async def retain(
        self, connection: AsyncConnection, scope_id: str, asset: MediaAsset, data: bytes, now: int
    ) -> bool:
        """Charge outside this method, in the same transaction as Source/request writes."""
        row = (
            (
                await connection.execute(
                    select(MEDIA_ASSETS).where(
                        MEDIA_ASSETS.c.scope_id == scope_id, MEDIA_ASSETS.c.byte_digest == asset.digest
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is not None:
            existing, saved = self.checked_asset(row)
            if existing != asset or saved != data:
                raise MediaError(409, "media_asset_conflict")
            return False
        if len(data) != asset.byte_size or "sha256:" + hashlib.sha256(data).hexdigest() != asset.digest:
            raise MediaError(422, "media_asset_mismatch")
        await connection.execute(
            insert(MEDIA_ASSETS).values(
                scope_id=scope_id,
                byte_digest=asset.digest,
                media_type=asset.media_type,
                byte_size=asset.byte_size,
                descriptor=media_json(asset),
                bytes=data,
                created_at=now,
            )
        )
        return True

    async def request_row(self, connection: AsyncConnection, scope_id: str, request_id: str):
        row = (
            (
                await connection.execute(
                    select(MEDIA_REQUESTS).where(
                        MEDIA_REQUESTS.c.scope_id == scope_id, MEDIA_REQUESTS.c.request_id == request_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise MediaError(404, "media_request_not_found")
        media_request_from_row(row)
        return row

    async def replay(
        self,
        connection: AsyncConnection,
        scope_id: str,
        *,
        kind: str,
        operation: str,
        target: str,
        key: str,
        digest: str,
    ):
        row = (
            (
                await connection.execute(
                    select(MEDIA_REQUESTS).where(
                        MEDIA_REQUESTS.c.scope_id == scope_id,
                        MEDIA_REQUESTS.c.processing_kind == kind,
                        MEDIA_REQUESTS.c.operation == operation,
                        MEDIA_REQUESTS.c.operation_target == target,
                        MEDIA_REQUESTS.c.idempotency_key == key,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        media_request_from_row(row)
        if row["request_digest"] != digest:
            raise MediaError(409, "media_idempotency_conflict")
        return row

    async def insert_request(
        self,
        connection: AsyncConnection,
        value: MediaProcessingRequest,
        handler: ImageProcessingInput,
        *,
        target: str,
        key: str,
        request_digest: str,
        receipt: BaseModel,
    ) -> None:
        encoded = media_json(receipt)
        if len(encoded) > MAX_MEDIA_RECEIPT_BYTES:
            raise MediaError(422, "media_receipt_too_large")
        await connection.execute(
            insert(MEDIA_REQUESTS).values(
                scope_id=value.scope_id,
                request_id=value.request_id,
                original_source_type=value.original.source.source_type,
                original_source_id=value.original.source.source_id,
                processing_kind=value.processing_kind,
                handler_schema=handler.schema_,
                operation=value.operation,
                operation_target=target,
                idempotency_key=key,
                request_digest=request_digest,
                profile_digest=value.profile_digest,
                state=value.state.value,
                state_version=value.state_version,
                active_key="active",
                due_at=int(value.created_at.timestamp()),
                input=media_json(handler),
                receipt=encoded,
                payload=media_json(value),
                created_at=int(value.created_at.timestamp()),
            )
        )

    async def _verified_assets(self, connection):
        """Seek by the primary key; keep both header pages and BLOB memory bounded."""
        cursor = None
        key = tuple_(MEDIA_ASSETS.c.scope_id, MEDIA_ASSETS.c.byte_digest)
        while True:
            statement = (
                select(MEDIA_ASSETS.c.scope_id, MEDIA_ASSETS.c.byte_digest, MEDIA_ASSETS.c.byte_size)
                .order_by(MEDIA_ASSETS.c.scope_id, MEDIA_ASSETS.c.byte_digest)
                .limit(128)
            )
            if cursor is not None:
                statement = statement.where(key > cursor)
            headers = (await connection.execute(statement)).mappings().all()
            if not headers:
                return
            for keys in _asset_verification_batches(headers):
                rows = (await connection.execute(select(MEDIA_ASSETS).where(key.in_(keys)))).mappings().all()
                if len(rows) != len(keys):
                    raise MediaError(500, "media_asset_corrupt")
                for row in rows:
                    asset, _ = self.checked_asset(row)
                    yield row["scope_id"], asset
            cursor = (headers[-1]["scope_id"], headers[-1]["byte_digest"])
            if len(headers) < 128:
                return

    async def reconcile(self, connection: AsyncConnection) -> dict[str, int]:
        """Startup recovery is O(all retained bytes + requests), not an admission operation."""
        await self.lock_capacity(connection, "__recovery__")
        scopes: dict[str, list[int]] = {}
        assets = 0
        retained_bytes = 0
        async for scope, asset in self._verified_assets(connection):
            scopes.setdefault(scope, [0, 0])[0] += asset.byte_size
            retained_bytes += asset.byte_size
            assets += 1
        cursor = None
        requests = 0
        while True:
            statement = (
                select(MEDIA_REQUESTS).order_by(MEDIA_REQUESTS.c.scope_id, MEDIA_REQUESTS.c.request_id).limit(64)
            )
            if cursor is not None:
                scope, request_id = cursor
                statement = statement.where(
                    tuple_(MEDIA_REQUESTS.c.scope_id, MEDIA_REQUESTS.c.request_id) > (scope, request_id)
                )
            rows = (await connection.execute(statement)).mappings().all()
            for row in rows:
                value = media_request_from_row(row)
                counts = scopes.setdefault(value.scope_id, [0, 0])
                counts[1] += value.state.value in ACTIVE_REQUEST_STATES
                requests += 1
            if len(rows) < 64:
                break
            cursor = (rows[-1]["scope_id"], rows[-1]["request_id"])
        await connection.execute(delete(MEDIA_CAPACITY))
        values = [
            {
                "boundary": "global",
                "scope_id": "",
                "retained_bytes": retained_bytes,
                "active_requests": sum(v[1] for v in scopes.values()),
            }
        ]
        values.extend(
            {"boundary": "scope", "scope_id": scope, "retained_bytes": counts[0], "active_requests": counts[1]}
            for scope, counts in scopes.items()
        )
        await connection.execute(insert(MEDIA_CAPACITY), values)
        return {"assets": assets, "requests": requests, "retained_bytes": retained_bytes}
