# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""One fenced image invocation, dispatched exclusively by the existing Supervisor."""

from __future__ import annotations

import asyncio
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from functools import partial
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field
from sqlalchemy import func, inspect, select, update

from powercontext.builtin.artifacts.image_analysis import ImageAnalysisContent, ImageAnalysisDraft
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.images.application import ImageApplication, image_analysis_id
from powercontext.builtin.images.identity import canonical_image_digest
from powercontext.builtin.images.models import ImageInterpretationSource, interpretation_snapshot
from powercontext.builtin.images.vision import VisionFailure, VisionParser, prepare_image
from powercontext.builtin.media.errors import MediaError
from powercontext.builtin.media.models import (
    ACTIVE_REQUEST_STATES,
    IMAGE_ANALYSIS_FAMILY,
    IMAGE_PROCESSING_BINDING,
    IMAGE_PROCESSING_KIND,
    ImageProcessingInput,
    MediaAttempt,
    MediaProcessingRequest,
    MediaRequestState,
    ScopedMediaArtifact,
    ScopedMediaSource,
)
from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError, RepositoryNotFoundError
from powercontext.builtin.persistence.media import media_json, media_request_from_row
from powercontext.builtin.persistence.media_tables import MEDIA_REQUESTS
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingBlock,
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerOutcome,
)
from powercontext.builtin.runtime.processing_execution import InvocationAlreadyHandled, ScopeInvocation
from powercontext.builtin.statistics import ModelUsageOperation, ModelUsagePurpose
from powercontext.sources import SourceMaterialization
from powercontext.sources.evidence import EvidenceAnchor


def transition_request(value: MediaProcessingRequest, **changes) -> MediaProcessingRequest:
    return MediaProcessingRequest.model_validate({
        **value.model_dump(),
        "state_version": value.state_version + 1,
        **changes,
    })


class ImageWorkerSpec(BaseModel):
    config: BuiltinConfig = Field(repr=False)
    worker_security: dict[str, Any] | None = Field(default=None, repr=False)


def run_image_worker(spec: ImageWorkerSpec, assignment: ArtifactProcessingWorkAssignment):
    return asyncio.run(_run_image_worker(spec, assignment))


async def _run_image_worker(spec, assignment):
    from powercontext.builtin.runtime.composition import open_builtin_contexts

    async with AsyncExitStack() as resources:
        # Vision owns its HTTP adapter. No ordinary generation/embedding model is constructed.
        contexts = await resources.enter_async_context(open_builtin_contexts(spec.config, _topic_memory_worker=True))
        service = await ImageApplication.open(
            contexts.database,
            contexts.repositories.sources,
            contexts.repositories.artifacts,
            spec.config.images,
            spec.config.inference,
            worker=True,
        )
        security = None
        if spec.worker_security is not None:
            from powercontext.server.processing_security import open_worker_security

            security = await resources.enter_async_context(
                open_worker_security(spec.worker_security, contexts.database)
            )
        try:
            return await process_image_invocation(
                service,
                spec.config.inference,
                assignment,
                security=security,
                usage_reporter=contexts.model_usage_reporter(assignment.scope_id),
            )
        except InvocationAlreadyHandled:
            return ArtifactProcessingWorkerCompletion(noop=True)
        except ArtifactProcessingLeadershipLostError:
            return ArtifactProcessingWorkerCompletion(ArtifactProcessingWorkerOutcome.LEADERSHIP_LOST)
        finally:
            await contexts.flush_model_usage()


async def _authorize_sources(service, security, connection, scope_id, original, context):
    if security is None:
        return
    from powercontext.server.authz import AccessAction, ResourceRef

    bound = security.access.with_connection(connection)
    await security.authorize_transaction(connection, scope_id)
    # Both inputs are Scope-owned Sources. Artifact sharing never grants this read.
    await bound.require(
        security.principal, AccessAction.SCOPE_READ, ResourceRef.scope(scope_id), context=security.context
    )
    head = await service.analysis_head(connection, scope_id, original.source)
    if head is not None:
        await bound.require(
            security.principal,
            AccessAction.ARTIFACT_WRITE,
            ResourceRef.artifact(scope_id, family=IMAGE_ANALYSIS_FAMILY, artifact_id=head.artifact_id),
            context=security.context,
        )
    if context.source is not None and context.source.scope_id != scope_id:
        raise MediaError(422, "image_context_scope_mismatch")


async def _store_request(connection, value, *, claim=None, claim_until=0, assignment=None):
    state = value.state.value
    await connection.execute(
        update(MEDIA_REQUESTS)
        .where(
            MEDIA_REQUESTS.c.scope_id == value.scope_id,
            MEDIA_REQUESTS.c.request_id == value.request_id,
        )
        .values(
            state=state,
            state_version=value.state_version,
            payload=media_json(value),
            active_key="active" if state in ACTIVE_REQUEST_STATES else None,
            due_at=0 if value.next_attempt_at is None else int(value.next_attempt_at.timestamp()),
            claim_token=claim,
            claim_until=claim_until,
            claim_group=None if assignment is None else assignment.fence.supervisor_group,
            claim_generation=None if assignment is None else assignment.fence.supervisor_generation,
            claim_holder=None if assignment is None else assignment.fence.holder_id,
        )
    )


async def _recover_scope(service, connection, scope_id, now):
    rows = (
        (
            await connection.execute(
                select(MEDIA_REQUESTS)
                .where(
                    MEDIA_REQUESTS.c.scope_id == scope_id,
                    MEDIA_REQUESTS.c.processing_kind == IMAGE_PROCESSING_KIND,
                    MEDIA_REQUESTS.c.state == "running",
                    MEDIA_REQUESTS.c.claim_until <= now,
                )
                .order_by(MEDIA_REQUESTS.c.claim_until)
                .limit(16)
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        value = media_request_from_row(row)
        attempts = list(value.attempts)
        attempts[-1] = attempts[-1].model_copy(
            update={
                "state": "interrupted",
                "finished_at": datetime.fromtimestamp(now, UTC),
                "error_code": "image_attempt_interrupted",
            }
        )
        retry = value.attempt_count < value.max_attempts
        recovered = transition_request(
            value,
            attempts=attempts,
            state=MediaRequestState.RETRY_WAIT if retry else MediaRequestState.FAILED,
            next_attempt_at=datetime.fromtimestamp(now + 2, UTC) if retry else None,
            error_code="image_attempt_interrupted",
        )
        await _store_request(connection, recovered)
        if not retry:
            await service.media.release_request(connection, scope_id)


@dataclass(frozen=True)
class _ImageClaim:
    request: MediaProcessingRequest
    handler: ImageProcessingInput
    attempt: MediaAttempt
    token: str
    headers: dict[str, str]
    media_type: str
    data: bytes


async def _claim_image(service, parser, invocation, security):
    assignment = invocation.assignment
    scope = assignment.scope_id
    async with service.database.transaction() as connection:
        await service.media.lock_capacity(connection, scope)
        await invocation.start(connection)
        now = int(time.time())
        await _recover_scope(service, connection, scope, now)
        row = (
            (
                await connection.execute(
                    select(MEDIA_REQUESTS)
                    .where(
                        MEDIA_REQUESTS.c.scope_id == scope,
                        MEDIA_REQUESTS.c.processing_kind == IMAGE_PROCESSING_KIND,
                        MEDIA_REQUESTS.c.state.in_(("pending", "retry_wait", "blocked")),
                        MEDIA_REQUESTS.c.due_at <= now,
                    )
                    .order_by(MEDIA_REQUESTS.c.due_at, MEDIA_REQUESTS.c.request_id)
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            await invocation.complete(connection, remaining_work=await _remaining(connection, scope))
            return ArtifactProcessingWorkerCompletion(noop=True)
        value = media_request_from_row(row)
        handler = ImageProcessingInput.model_validate_json(row["input"])
        await _authorize_sources(service, security, connection, scope, value.original, handler.context)
        try:
            headers = parser.headers_for(handler.profile)
        except VisionFailure as error:
            blocked = transition_request(
                value,
                state=MediaRequestState.BLOCKED,
                error_code=error.code,
                next_attempt_at=datetime.fromtimestamp(now + 60, UTC),
            )
            await _store_request(connection, blocked)
            await invocation.complete(connection, remaining_work=True)
            return ArtifactProcessingWorkerCompletion(held_count=1, hold_codes=(error.code,))
        if value.attempt_count >= value.max_attempts:
            raise MediaError(500, "media_request_corrupt")
        attempt = MediaAttempt(
            attempt=value.attempt_count + 1, state="started", started_at=datetime.fromtimestamp(now, UTC)
        )
        claimed = transition_request(
            value,
            state=MediaRequestState.RUNNING,
            attempt_count=value.attempt_count + 1,
            attempts=(*value.attempts, attempt),
            next_attempt_at=None,
            error_code=None,
        )
        token = str(uuid4())
        await _store_request(
            connection,
            claimed,
            claim=token,
            claim_until=now + handler.profile.timeout_seconds + 30,
            assignment=assignment,
        )
        original = await service.original(connection, scope, value.original.source)
        asset, data = await service.media.asset(connection, scope, original.value.asset.asset_id)
        return _ImageClaim(claimed, handler, attempt, token, headers, asset.media_type, data)


async def _guard_claim(service, connection, invocation, claim, security):
    scope = claim.request.scope_id
    await service.media.lock_capacity(connection, scope)
    await invocation.guard(connection)
    current = await service.media.request_row(connection, scope, claim.request.request_id)
    if current["claim_token"] != claim.token or current["state_version"] != claim.request.state_version:
        await invocation.complete(connection, remaining_work=await _remaining(connection, scope))
        return False
    await _authorize_sources(service, security, connection, scope, claim.request.original, claim.handler.context)
    return True


async def _publish_analysis(service, connection, claim, head, output, prepared_digest, security):
    scope = claim.request.scope_id
    original = await service.original(connection, scope, claim.request.original.source)
    anchor = EvidenceAnchor(scope_id=scope, source=original.ref, journal_position=original.journal_position)
    content = ImageAnalysisContent(
        original=claim.request.original,
        origin_anchor=anchor,
        profile=claim.handler.profile,
        profile_digest=claim.request.profile_digest,
        input_digest=prepared_digest,
        context_digest=canonical_image_digest(claim.handler.context.model_dump(mode="json")),
        context=claim.handler.context,
        output=output,
    )
    sources = [original.ref]
    if claim.handler.context.source is not None:
        sources.append(claim.handler.context.source.source)
    from powercontext.sources import SourceRef

    direct = tuple(
        {(s.source_type, s.source_id): SourceRef.model_validate(s.model_dump(mode="json")) for s in sources}.values()
    )
    draft = ImageAnalysisDraft(content=content, sources=direct)
    artifact = (
        await service.artifacts.create(connection, scope, image_analysis_id(scope, original.ref), draft)
        if head is None
        else await service.artifacts.revise(connection, scope, head, draft)
    )
    if security is not None:
        from powercontext.server.authz import ResourceRef

        resource = ResourceRef.artifact(scope, family=IMAGE_ANALYSIS_FAMILY, artifact_id=artifact.artifact_id)
        bound = security.access.with_connection(connection)
        if await bound.artifact_owner(resource) is None:
            await bound.establish_artifact_owner(
                resource,
                security.principal,
                idempotency_key=f"image-owner:{scope}:{artifact.artifact_id}",
                context=security.context,
            )
    snapshot = interpretation_snapshot(output)
    scoped_artifact = ScopedMediaArtifact(scope_id=scope, artifact=artifact.as_ref())
    source = ImageInterpretationSource(
        name="image_publication_" + canonical_image_digest(scoped_artifact.model_dump(mode="json"))[7:],
        materialization=SourceMaterialization.CAPTURED,
        result=scoped_artifact,
        original=claim.request.original,
        origin_anchor=anchor,
        readability=output.readability.value,
        content=snapshot,
        content_digest=canonical_image_digest(snapshot),
    )
    stored, created = await service.sources.add_with_status(connection, scope, source)
    if created:
        await service.source_pending.note_source(
            connection, scope, TOPIC_MEMORY_SOURCE_WINDOW_BINDING, stored.journal_position
        )
    return scoped_artifact, ScopedMediaSource(scope_id=scope, source=stored.ref)


def _failed_request(claim, failure, attempt_updates):
    value = claim.request
    retry = failure.retry_after is not None and value.attempt_count < value.max_attempts
    wait = max(2 if value.attempt_count == 1 else 8, min(30, failure.retry_after or 0))
    attempt = claim.attempt.model_copy(update={**attempt_updates, "state": "failed", "error_code": failure.code})
    return transition_request(
        value,
        state=MediaRequestState.RETRY_WAIT if retry else MediaRequestState.FAILED,
        attempts=(*value.attempts[:-1], attempt),
        error_code=failure.code,
        next_attempt_at=datetime.fromtimestamp(int(time.time()) + wait, UTC) if retry else None,
    )


async def _finish_attempt(service, invocation, claim, result, prepared, security):
    scope = claim.request.scope_id
    usage = result.usage
    async with service.database.transaction() as connection:
        if not await _guard_claim(service, connection, invocation, claim, security):
            return ArtifactProcessingWorkerCompletion(noop=True)
        attempt_updates = {
            "finished_at": datetime.now(UTC),
            "provider_requests": usage.requests,
            "input_digest": None if prepared is None else prepared.digest,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "latency_ms": result.latency_ms,
            "http_status": result.http_status,
        }
        head = await service.analysis_head(connection, scope, claim.request.original.source)
        if (
            not isinstance(result, VisionFailure)
            and (0 if head is None else head.revision) != claim.handler.expected_artifact_revision
        ):
            result = VisionFailure(
                "image_analysis_head_changed", usage=usage, latency_ms=result.latency_ms, http_status=result.http_status
            )
        if isinstance(result, VisionFailure):
            completed = _failed_request(claim, result, attempt_updates)
        else:
            if prepared is None:
                raise MediaError(500, "media_prepared_input_missing")
            artifact, publication = await _publish_analysis(
                service, connection, claim, head, result.output, prepared.digest, security
            )
            attempt = claim.attempt.model_copy(update={**attempt_updates, "state": "succeeded"})
            completed = transition_request(
                claim.request,
                state=MediaRequestState.SUCCEEDED,
                attempts=(*claim.request.attempts[:-1], attempt),
                result=artifact,
                publication=publication,
                error_code=None,
            )
        if completed.state in {MediaRequestState.SUCCEEDED, MediaRequestState.FAILED}:
            await service.media.release_request(connection, scope)
        await _store_request(connection, completed)
        await invocation.complete(connection, remaining_work=await _remaining(connection, scope))
    return ArtifactProcessingWorkerCompletion()


async def process_image_invocation(service, inference, assignment, *, security=None, usage_reporter=None):
    if assignment.binding_name != IMAGE_PROCESSING_BINDING or assignment.artifact_family != IMAGE_ANALYSIS_FAMILY:
        raise ValueError("image worker assignment belongs to another binding")  # noqa: TRY003
    service.require_available(admission=True)
    invocation = ScopeInvocation(
        assignment,
        authorize_transaction=None
        if security is None
        else partial(security.authorize_transaction, scope_id=assignment.scope_id),
    )
    parser = VisionParser(inference)
    claim = await _claim_image(service, parser, invocation, security)
    if isinstance(claim, ArtifactProcessingWorkerCompletion):
        return claim
    prepared = None
    try:
        prepared = await asyncio.to_thread(prepare_image, claim.data, claim.media_type)
        async with service.database.transaction() as connection:
            if not await _guard_claim(service, connection, invocation, claim, security):
                return ArtifactProcessingWorkerCompletion(noop=True)
            # Persist possible network dispatch before leaving the transaction.
            attempt = claim.attempt.model_copy(update={"provider_requests": None, "input_digest": prepared.digest})
            dispatched = transition_request(claim.request, attempts=(*claim.request.attempts[:-1], attempt))
            await _store_request(
                connection,
                dispatched,
                claim=claim.token,
                claim_until=int(time.time()) + claim.handler.profile.timeout_seconds + 30,
                assignment=assignment,
            )
            claim = replace(claim, request=dispatched, attempt=attempt)
        result = await parser.parse(claim.handler.profile, claim.handler.context, prepared, claim.headers)
    except VisionFailure as error:
        result = error
    try:
        return await _finish_attempt(service, invocation, claim, result, prepared, security)
    finally:
        if usage_reporter is not None and result.usage.requests:
            await usage_reporter(ModelUsagePurpose.IMAGE_PARSING, ModelUsageOperation.GENERATION, result.usage)


async def _remaining(connection, scope):
    return (
        await connection.scalar(
            select(MEDIA_REQUESTS.c.request_id)
            .where(
                MEDIA_REQUESTS.c.scope_id == scope,
                MEDIA_REQUESTS.c.processing_kind == IMAGE_PROCESSING_KIND,
                MEDIA_REQUESTS.c.active_key == "active",
            )
            .limit(1)
        )
        is not None
    )


class ImageProcessingPendingProvider:
    def __init__(self, database):
        self.database = database

    async def reconcile(self, after_scope_id, limit, *, fence):
        statement = (
            select(MEDIA_REQUESTS.c.scope_id)
            .where(
                MEDIA_REQUESTS.c.processing_kind == IMAGE_PROCESSING_KIND,
                MEDIA_REQUESTS.c.active_key == "active",
            )
            .distinct()
            .order_by(MEDIA_REQUESTS.c.scope_id)
            .limit(limit)
        )
        if after_scope_id is not None:
            statement = statement.where(MEDIA_REQUESTS.c.scope_id > after_scope_id)
        async with self.database.transaction() as connection:
            await ArtifactProcessingLeaseRepository().require_fence(connection, fence)
            if not await connection.run_sync(lambda sync: inspect(sync).has_table(MEDIA_REQUESTS.name)):
                return None
            scopes = tuple(await connection.scalars(statement))
            intents = ArtifactProcessingIntentRepository()
            for scope in scopes:
                intent = await intents.load(connection, scope, IMAGE_PROCESSING_BINDING)
                if intent is None or intent.dirty_generation == intent.clean_generation:
                    await intents.mark_dirty(connection, scope, IMAGE_PROCESSING_BINDING)
        return scopes[-1] if len(scopes) == limit else None


async def image_processing_block(connection, scope, _binding, *, enabled=True):
    if not enabled:
        return ArtifactProcessingBlock(key="images-disabled", stage="admission", error_code="image_disabled")
    if not await connection.run_sync(lambda sync: inspect(sync).has_table(MEDIA_REQUESTS.name)):
        return ArtifactProcessingBlock(key="media-migration", stage="admission", error_code="media_migration_required")
    due = await connection.scalar(
        select(func.min(MEDIA_REQUESTS.c.due_at)).where(
            MEDIA_REQUESTS.c.scope_id == scope,
            MEDIA_REQUESTS.c.processing_kind == IMAGE_PROCESSING_KIND,
            MEDIA_REQUESTS.c.active_key == "active",
            MEDIA_REQUESTS.c.state != "running",
        )
    )
    running_due = await connection.scalar(
        select(func.min(MEDIA_REQUESTS.c.claim_until)).where(
            MEDIA_REQUESTS.c.scope_id == scope,
            MEDIA_REQUESTS.c.processing_kind == IMAGE_PROCESSING_KIND,
            MEDIA_REQUESTS.c.state == "running",
        )
    )
    values = [value for value in (due, running_due) if value is not None]
    next_due = min(values) if values else None
    if next_due is not None and next_due > int(time.time()):
        return ArtifactProcessingBlock(key=f"media-due:{next_due}", stage="admission", error_code="media_retry_wait")
    return None


async def validate_interpretation(connection, stored, sources, artifacts):
    """Recheck the authoritative exact revision before consuming a derived snapshot."""
    source = stored.value
    if not isinstance(source, ImageInterpretationSource):
        raise MediaError(422, "image_interpretation_required")
    try:
        artifact = await artifacts.get(connection, source.result.scope_id, source.result.artifact)
        original = await sources.get(connection, source.original.scope_id, source.original.source)
    except RepositoryNotFoundError as error:
        raise MediaError(410, "image_evidence_unavailable") from error
    if (
        artifact.content.original != source.original
        or artifact.content.origin_anchor != source.origin_anchor
        or original.journal_position != source.origin_anchor.journal_position
        or interpretation_snapshot(artifact.content.output) != source.content
        or artifact.content.output.readability.value != source.readability
    ):
        raise MediaError(500, "image_interpretation_corrupt")
    return artifact, original
