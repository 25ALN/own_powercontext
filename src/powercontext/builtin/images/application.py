# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Image admission and commands over shared media storage and Artifact history."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from datetime import UTC, datetime

from sqlalchemy import inspect, select, tuple_, update

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.images.config import ImageConfig, parse_profile
from powercontext.builtin.images.identity import canonical_image_digest, image_profile_digest, original_image_source_id
from powercontext.builtin.images.models import MediaImageOriginalSource, OriginalImageSource
from powercontext.builtin.images.validation import decode_image_bytes, validate_image
from powercontext.builtin.media.errors import MediaError
from powercontext.builtin.media.models import (
    IMAGE_ANALYSIS_FAMILY,
    IMAGE_PROCESSING_BINDING,
    IMAGE_PROCESSING_KIND,
    ImageAnalysisRequest,
    ImageCaptureInput,
    ImageProcessingInput,
    ImageTechnicalMetadata,
    MediaAsset,
    MediaCaptureReceipt,
    MediaProcessingRequest,
    MediaRequestPage,
    MediaRequestState,
    ScopedMediaSource,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.media import MediaRepository, media_json, media_request_from_row
from powercontext.builtin.persistence.media_tables import MEDIA_ASSETS, MEDIA_CAPACITY, MEDIA_REQUESTS
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.tables import SCOPES_TABLE
from powercontext.http import ExperimentalImageContext
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, SourceMaterialization, SourceRef


def image_analysis_id(scope_id: str, original: SourceRef) -> str:
    return "image_" + canonical_image_digest({"scope_id": scope_id, "original": original.model_dump(mode="json")})[7:]


def _decode_request_cursor(cursor, scope, kind, states):
    if len(cursor) > 2048:
        raise MediaError(422, "media_invalid_cursor")
    try:
        token = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        created, request_id = token["after"]
        valid = token["scope"] == scope and token["kind"] == kind and token["states"] == list(states)
    except (ValueError, TypeError, KeyError, RecursionError) as error:
        raise MediaError(422, "media_invalid_cursor") from error
    if not valid or type(created) is not int or not isinstance(request_id, str):
        raise MediaError(422, "media_invalid_cursor")
    return created, request_id


class ImageApplication:
    """The image domain owns validation and publication, never a second scheduler."""

    def __init__(
        self,
        database: AsyncDatabase,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        config: ImageConfig,
        inference: object,
        registry=None,
    ) -> None:
        self.database = database
        self._decoding = asyncio.Semaphore(2)
        self.sources = sources
        self.artifacts = artifacts
        self.config = config
        try:
            self.profile = parse_profile(inference)
        except ValueError:
            self.profile = None
        if registry is None:
            from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY

            registry = BUILTIN_SOURCE_REGISTRY
        self.registry = registry
        self.media = MediaRepository()
        self.intents = ArtifactProcessingIntentRepository()
        self.source_pending = ArtifactProcessingPendingRepository()
        self.processing_enabled = False
        self.available = False
        self.unavailable_code = "media_migration_required"
        self.recovery_cost: dict[str, int] | None = None

    @classmethod
    async def open(cls, database, sources, artifacts, config, inference, *, worker: bool = False, registry=None):
        service = cls(database, sources, artifacts, config, inference, registry)
        if database.engine.dialect.name != "sqlite":
            service.unavailable_code = "media_backend_unsupported"
            return service
        async with database.transaction() as connection:
            names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
            if not {MEDIA_ASSETS.name, MEDIA_REQUESTS.name, MEDIA_CAPACITY.name} <= names:
                return service
            from powercontext.builtin.persistence.media_schema import require_media_schema
            from powercontext.builtin.persistence.migrations.models import MigrationError

            try:
                await connection.run_sync(require_media_schema)
            except MigrationError:
                return service
            if config.enabled:
                databases = (await connection.exec_driver_sql("PRAGMA database_list")).mappings().all()
                journal = (await connection.exec_driver_sql("PRAGMA journal_mode")).scalar_one()
                synchronous = (await connection.exec_driver_sql("PRAGMA synchronous")).scalar_one()
                if (
                    not any(row["name"] == "main" and row["file"] for row in databases)
                    or journal not in {"wal", "delete"}
                    or synchronous < 2
                ):
                    service.unavailable_code = "media_database_not_durable"
                    return service
                if not worker:
                    try:
                        async with connection.begin_nested():
                            service.recovery_cost = await service.media.reconcile(connection)
                    except MediaError as error:
                        service.unavailable_code = error.code
                        return service
        service.available = True
        return service

    def diagnostics(self):
        return {
            "enabled": self.config.enabled,
            "available": self.available,
            "processing_enabled": self.processing_enabled,
            "vision_configured": self.profile is not None,
            "profile_digest": None if self.profile is None else image_profile_digest(self.profile),
            "error_code": None if self.available else self.unavailable_code,
        }

    def require_available(self, *, admission=False):
        if not self.available:
            raise MediaError(503, self.unavailable_code)
        if admission and not self.config.enabled:
            raise MediaError(503, "image_disabled")

    async def _context(self, connection, scope: str, context: ExperimentalImageContext):
        if context.source is None:
            return
        if context.source.scope_id != scope:
            raise MediaError(422, "image_context_scope_mismatch")
        try:
            stored = await self.sources.get(
                connection, scope, SourceRef.model_validate(context.source.source.model_dump(mode="json"))
            )
        except RepositoryNotFoundError as error:
            raise MediaError(422, "image_context_not_found") from error
        if TEXT_EVIDENCE_PROJECTION_KEY not in self.registry.projection_keys(stored.value):
            raise MediaError(422, "image_context_not_text")

    async def _charge(self, connection, scope, *, retained_bytes=0, active_requests=1):
        await self.media.charge(
            connection,
            scope,
            retained_bytes=retained_bytes,
            active_requests=active_requests,
            max_bytes=self.config.max_asset_bytes_total,
            max_requests=self.config.max_pending_total,
            max_scope_requests=self.config.max_pending_per_scope,
        )

    async def capture(self, request: ImageCaptureInput, *, authorize_transaction=None) -> MediaCaptureReceipt:
        self.require_available(admission=True)
        data = decode_image_bytes(request.bytes_base64)
        async with self._decoding:
            decoded = await asyncio.to_thread(validate_image, data, request.media_type)
        asset = MediaAsset(
            asset_id="ma_" + decoded.digest.root[7:],
            digest=decoded.digest.root,
            media_type=request.media_type,
            byte_size=len(data),
            metadata=ImageTechnicalMetadata(width=decoded.width, height=decoded.height),
        )
        source_id = original_image_source_id(request.scope_id, request.capture_key, request.origin.attachment_index)
        target = "capture:" + source_id
        digest = canonical_image_digest({
            "processing_kind": request.processing_kind,
            "digest": asset.digest,
            "capture_key": request.capture_key,
            "origin": request.origin.model_dump(mode="json"),
            "context": request.context.model_dump(mode="json"),
        })
        async with self.database.transaction() as connection:
            await self.media.lock_capacity(connection, request.scope_id)
            if authorize_transaction is not None:
                await authorize_transaction(connection)
            replay = await self.media.replay(
                connection,
                request.scope_id,
                kind=request.processing_kind,
                operation="capture",
                target=target,
                key=request.capture_key,
                digest=digest,
            )
            if replay is not None:
                return MediaCaptureReceipt.model_validate_json(replay["receipt"]).model_copy(update={"replayed": True})
            if self.profile is None:
                raise MediaError(503, "image_vision_not_configured")
            if (
                await connection.scalar(
                    select(SCOPES_TABLE.c.scope_id).where(SCOPES_TABLE.c.scope_id == request.scope_id)
                )
                is None
            ):
                raise MediaError(422, "image_scope_not_found")
            await self._context(connection, request.scope_id, request.context)
            exists = await connection.scalar(
                select(MEDIA_ASSETS.c.byte_digest).where(
                    MEDIA_ASSETS.c.scope_id == request.scope_id, MEDIA_ASSETS.c.byte_digest == asset.digest
                )
            )
            await self._charge(connection, request.scope_id, retained_bytes=0 if exists else len(data))
            now = int(time.time())
            await self.media.retain(connection, request.scope_id, asset, data, now)
            source = MediaImageOriginalSource(
                name=source_id,
                materialization=SourceMaterialization.CAPTURED,
                asset=asset,
                capture_key=request.capture_key,
                origin=request.origin,
                context=request.context,
            )
            stored, created = await self.sources.add_with_status(connection, request.scope_id, source)
            if created:
                await self.source_pending.note_source(
                    connection, request.scope_id, TOPIC_MEMORY_SOURCE_WINDOW_BINDING, stored.journal_position
                )
            original = ScopedMediaSource(scope_id=request.scope_id, source=stored.ref)
            handler = ImageProcessingInput(profile=self.profile, context=request.context, expected_artifact_revision=0)
            request_id = (
                "mr_"
                + canonical_image_digest({
                    "scope": request.scope_id,
                    "kind": request.processing_kind,
                    "operation": "capture",
                    "target": target,
                    "key": request.capture_key,
                })[7:]
            )
            value = self._new_request(request_id, "capture", original, handler, now)
            receipt = MediaCaptureReceipt(
                original=original, asset=asset, sequence=stored.journal_position, request_id=request_id
            )
            await self.media.insert_request(
                connection,
                value,
                handler,
                target=target,
                key=request.capture_key,
                request_digest=digest,
                receipt=receipt,
            )
            await self.intents.mark_dirty(connection, request.scope_id, IMAGE_PROCESSING_BINDING)
        return receipt

    @staticmethod
    def _new_request(request_id, operation, original, handler, now):
        return MediaProcessingRequest(
            scope_id=original.scope_id,
            request_id=request_id,
            operation=operation,
            original=original,
            state=MediaRequestState.PENDING,
            state_version=1,
            profile_digest=image_profile_digest(handler.profile),
            attempt_count=0,
            max_attempts=handler.profile.max_attempts,
            created_at=datetime.fromtimestamp(now, UTC),
        )

    async def original(self, connection, scope_id: str, ref: SourceRef):
        try:
            stored = await self.sources.get(connection, scope_id, ref)
        except RepositoryNotFoundError as error:
            raise MediaError(404, "image_original_not_found") from error
        if isinstance(stored.value, OriginalImageSource):
            raise MediaError(422, "experimental_media_import_unsupported")
        if not isinstance(stored.value, MediaImageOriginalSource):
            raise MediaError(422, "image_original_required")
        return stored

    async def analysis_head(self, connection, scope_id, ref):
        try:
            return await self.artifacts.latest(
                connection, scope_id, IMAGE_ANALYSIS_FAMILY, image_analysis_id(scope_id, ref)
            )
        except RepositoryNotFoundError:
            return None

    async def request_analysis(
        self, request: ImageAnalysisRequest, *, authorize_transaction=None
    ) -> MediaProcessingRequest:
        self.require_available(admission=True)
        target = (
            request.request_id
            if request.operation == "retry"
            else canonical_image_digest(request.original.model_dump(mode="json"))
        )
        if target is None:
            raise MediaError(422, "media_parent_request_required")
        digest = canonical_image_digest(request.model_dump(mode="json"))
        async with self.database.transaction() as connection:
            await self.media.lock_capacity(connection, request.scope_id)
            if authorize_transaction is not None:
                await authorize_transaction(connection)
            replay = await self.media.replay(
                connection,
                request.scope_id,
                kind=request.processing_kind,
                operation=request.operation,
                target=target,
                key=request.idempotency_key,
                digest=digest,
            )
            if replay is not None:
                return MediaProcessingRequest.model_validate_json(replay["receipt"])
            stored = await self.original(connection, request.scope_id, request.original)
            head = await self.analysis_head(connection, request.scope_id, request.original)
            if request.expected_artifact_revision != (0 if head is None else head.revision):
                raise MediaError(409, "image_analysis_head_changed")
            active = (
                (
                    await connection.execute(
                        select(MEDIA_REQUESTS).where(
                            MEDIA_REQUESTS.c.scope_id == request.scope_id,
                            MEDIA_REQUESTS.c.original_source_type == request.original.source_type,
                            MEDIA_REQUESTS.c.original_source_id == request.original.source_id,
                            MEDIA_REQUESTS.c.processing_kind == request.processing_kind,
                            MEDIA_REQUESTS.c.active_key == "active",
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            parent = (
                None
                if request.request_id is None
                else await self.media.request_row(connection, request.scope_id, request.request_id)
            )
            if parent is not None and (
                parent["original_source_type"] != request.original.source_type
                or parent["original_source_id"] != request.original.source_id
                or parent["processing_kind"] != request.processing_kind
                or request.expected_state_version != parent["state_version"]
            ):
                raise MediaError(409, "media_request_changed")
            handler = await self._analysis_input(connection, request, stored, active, parent)
            if image_profile_digest(handler.profile) != request.expected_profile_digest:
                raise MediaError(409, "image_profile_changed")
            await self._charge(connection, request.scope_id)
            request_id = (
                "mr_"
                + canonical_image_digest({
                    "scope": request.scope_id,
                    "kind": request.processing_kind,
                    "operation": request.operation,
                    "target": target,
                    "key": request.idempotency_key,
                })[7:]
            )
            value = self._new_request(
                request_id,
                request.operation,
                ScopedMediaSource(scope_id=request.scope_id, source=stored.ref),
                handler,
                int(time.time()),
            )
            await self.media.insert_request(
                connection,
                value,
                handler,
                target=target,
                key=request.idempotency_key,
                request_digest=digest,
                receipt=value,
            )
            await self.intents.mark_dirty(connection, request.scope_id, IMAGE_PROCESSING_BINDING)
        return value

    async def _analysis_input(self, connection, request, stored, active, parent):
        if request.operation == "retry":
            if parent is None or parent["state"] != "failed" or active is not None:
                raise MediaError(409, "media_request_not_retryable")
            old_handler = ImageProcessingInput.model_validate_json(parent["input"])
            handler = old_handler.model_copy(update={"expected_artifact_revision": request.expected_artifact_revision})
        else:
            if self.profile is None:
                raise MediaError(503, "image_vision_not_configured")
            if active is not None:
                if parent is None or parent["request_id"] != active["request_id"]:
                    raise MediaError(409, "media_active_request_conflict")
                previous = media_request_from_row(active)
                superseded = previous.model_copy(
                    update={
                        "state": MediaRequestState.SUPERSEDED,
                        "state_version": previous.state_version + 1,
                        "next_attempt_at": None,
                    }
                )
                await connection.execute(
                    update(MEDIA_REQUESTS)
                    .where(
                        MEDIA_REQUESTS.c.scope_id == request.scope_id,
                        MEDIA_REQUESTS.c.request_id == previous.request_id,
                    )
                    .values(
                        state="superseded",
                        state_version=superseded.state_version,
                        active_key=None,
                        claim_token=None,
                        claim_until=0,
                        payload=media_json(superseded),
                    )
                )
                await self.media.release_request(connection, request.scope_id)
            handler = ImageProcessingInput(
                profile=self.profile,
                context=stored.value.context,
                expected_artifact_revision=request.expected_artifact_revision,
            )
        return handler

    async def get_asset(self, scope_id, asset_id):
        self.require_available()
        async with self.database.transaction() as connection:
            asset, data = await self.media.asset(connection, scope_id, asset_id)
        return asset, data

    async def get_request(self, scope_id, request_id):
        self.require_available()
        async with self.database.transaction() as connection:
            return media_request_from_row(await self.media.request_row(connection, scope_id, request_id))

    async def list_requests(self, scope_id, *, processing_kind=IMAGE_PROCESSING_KIND, states=(), limit=50, cursor=None):
        self.require_available()
        if processing_kind != IMAGE_PROCESSING_KIND or not 1 <= limit <= 100:
            raise MediaError(422, "media_invalid_page")
        valid_states = tuple(sorted(MediaRequestState(state).value for state in states))
        statement = select(MEDIA_REQUESTS).where(
            MEDIA_REQUESTS.c.scope_id == scope_id, MEDIA_REQUESTS.c.processing_kind == processing_kind
        )
        if valid_states:
            statement = statement.where(MEDIA_REQUESTS.c.state.in_(valid_states))
        if cursor is not None:
            created, request_id = _decode_request_cursor(cursor, scope_id, processing_kind, valid_states)
            statement = statement.where(
                tuple_(MEDIA_REQUESTS.c.created_at, MEDIA_REQUESTS.c.request_id) > (created, request_id)
            )
        async with self.database.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        statement.order_by(MEDIA_REQUESTS.c.created_at, MEDIA_REQUESTS.c.request_id).limit(limit + 1)
                    )
                )
                .mappings()
                .all()
            )
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            token = {
                "scope": scope_id,
                "kind": processing_kind,
                "states": valid_states,
                "after": [last["created_at"], last["request_id"]],
            }
            next_cursor = (
                base64.urlsafe_b64encode(json.dumps(token, separators=(",", ":")).encode()).decode().rstrip("=")
            )
        return MediaRequestPage(
            items=tuple(media_request_from_row(row) for row in rows[:limit]), next_cursor=next_cursor
        )
