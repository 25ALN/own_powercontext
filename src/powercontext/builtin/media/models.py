# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Versioned, bounded values retained by the shared media repository."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.http import ExperimentalImageContext, ExperimentalImageOrigin, ExperimentalImageParseProfile
from powercontext.sources import SourceRef

IMAGE_PROCESSING_KIND = "image.interpretation"
IMAGE_PROCESSING_BINDING = "image-interpretation"
IMAGE_ANALYSIS_FAMILY = "image-analysis"
ACTIVE_REQUEST_STATES = ("pending", "running", "retry_wait", "blocked")
MAX_ASSET_BYTES = 8 * 1024 * 1024
MAX_MEDIA_INPUT_BYTES = 32 * 1024
MAX_MEDIA_RECEIPT_BYTES = 16 * 1024
MAX_MEDIA_ATTEMPTS = 5
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
ScopeId = Annotated[str, Field(min_length=1, max_length=256, pattern=r".*\S.*")]


class MediaValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class ScopedMediaSource(MediaValue):
    scope_id: ScopeId
    source: SourceRef


class ScopedMediaArtifact(MediaValue):
    scope_id: ScopeId
    artifact: ArtifactRef


class ImageTechnicalMetadata(MediaValue):
    schema_: Literal["powercontext.media.image.v1"] = Field(default="powercontext.media.image.v1", alias="schema")
    width: StrictInt = Field(gt=0, le=16384)
    height: StrictInt = Field(gt=0, le=16384)

    @model_validator(mode="after")
    def pixel_budget(self):
        if self.width * self.height > 20_000_000:
            raise ValueError("image pixel budget exceeded")  # noqa: TRY003
        return self


class MediaAsset(MediaValue):
    asset_id: str = Field(pattern=r"^ma_[0-9a-f]{64}$")
    digest: Digest
    media_type: Literal["image/png", "image/jpeg"]
    byte_size: StrictInt = Field(gt=0, le=MAX_ASSET_BYTES)
    metadata: ImageTechnicalMetadata

    @model_validator(mode="after")
    def exact_identity(self):
        if self.asset_id != "ma_" + self.digest.removeprefix("sha256:"):
            raise ValueError("asset identity does not match its digest")  # noqa: TRY003
        return self


class ImageProcessingInput(MediaValue):
    """The first registered handler input; other kinds require their own schema."""

    schema_: Literal["powercontext.media.image-input.v1"] = Field(
        default="powercontext.media.image-input.v1", alias="schema"
    )
    processing_kind: Literal["image.interpretation"] = IMAGE_PROCESSING_KIND
    profile: ExperimentalImageParseProfile
    context: ExperimentalImageContext
    expected_artifact_revision: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def bounded(self):
        if len(self.model_dump_json(by_alias=True).encode()) > MAX_MEDIA_INPUT_BYTES:
            raise ValueError("media handler input exceeds 32 KiB")  # noqa: TRY003
        return self


class MediaAttempt(MediaValue):
    attempt: StrictInt = Field(ge=1, le=MAX_MEDIA_ATTEMPTS)
    state: Literal["started", "succeeded", "failed", "interrupted"]
    started_at: datetime
    finished_at: datetime | None = None
    provider_requests: StrictInt | None = Field(default=0, ge=0, le=1)
    input_digest: Digest | None = None
    input_tokens: StrictInt | None = Field(default=None, ge=0)
    output_tokens: StrictInt | None = Field(default=None, ge=0)
    latency_ms: float | None = Field(default=None, ge=0)
    http_status: StrictInt | None = Field(default=None, ge=100, le=599)
    error_code: str | None = Field(default=None, max_length=128)


class MediaRequestState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    RETRY_WAIT = "retry_wait"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class MediaProcessingRequest(MediaValue):
    """Public progress never embeds image bytes, prompts, or provider responses."""

    schema_: Literal["powercontext.media.request.v1"] = Field(default="powercontext.media.request.v1", alias="schema")
    scope_id: ScopeId
    request_id: str = Field(pattern=r"^mr_[0-9a-f]{64}$")
    processing_kind: Literal["image.interpretation"] = IMAGE_PROCESSING_KIND
    operation: Literal["capture", "retry", "reparse"]
    original: ScopedMediaSource
    state: MediaRequestState
    state_version: StrictInt = Field(ge=1)
    profile_digest: Digest
    attempt_count: StrictInt = Field(ge=0, le=MAX_MEDIA_ATTEMPTS)
    max_attempts: StrictInt = Field(ge=1, le=MAX_MEDIA_ATTEMPTS)
    attempts: tuple[MediaAttempt, ...] = Field(default=(), max_length=MAX_MEDIA_ATTEMPTS)
    result: ScopedMediaArtifact | None = None
    publication: ScopedMediaSource | None = None
    error_code: str | None = Field(default=None, max_length=128)
    next_attempt_at: datetime | None = None
    created_at: datetime

    @model_validator(mode="after")
    def coherent(self):
        succeeded = self.state == MediaRequestState.SUCCEEDED
        last = self.attempts[-1] if self.attempts else None
        valid_attempt = self.state not in {
            MediaRequestState.SUCCEEDED,
            MediaRequestState.FAILED,
            MediaRequestState.RUNNING,
        } or (
            last is not None
            and (
                (self.state == MediaRequestState.RUNNING and last.state == "started" and last.finished_at is None)
                or (
                    self.state == MediaRequestState.SUCCEEDED
                    and last.state == "succeeded"
                    and last.finished_at is not None
                )
                or (
                    self.state == MediaRequestState.FAILED
                    and last.state in {"failed", "interrupted"}
                    and last.finished_at is not None
                )
            )
        )
        if (
            not valid_attempt
            or self.original.scope_id != self.scope_id
            or self.attempt_count > self.max_attempts
            or len(self.attempts) != self.attempt_count
            or tuple(a.attempt for a in self.attempts) != tuple(range(1, self.attempt_count + 1))
            or (self.result is not None) != succeeded
            or (self.publication is not None) != succeeded
            or (self.result is not None and self.result.scope_id != self.scope_id)
            or (self.publication is not None and self.publication.scope_id != self.scope_id)
            or (self.state == MediaRequestState.RETRY_WAIT and self.next_attempt_at is None)
        ):
            raise ValueError("inconsistent media request")  # noqa: TRY003
        return self


class MediaCaptureReceipt(MediaValue):
    status: Literal["accepted"] = "accepted"
    original: ScopedMediaSource
    asset: MediaAsset
    sequence: StrictInt = Field(ge=1)
    request_id: str = Field(pattern=r"^mr_[0-9a-f]{64}$")
    replayed: bool = False


class MediaRequestPage(MediaValue):
    items: tuple[MediaProcessingRequest, ...] = Field(max_length=100)
    next_cursor: str | None = None


class ImageAnalysisRequest(MediaValue):
    scope_id: ScopeId
    operation: Literal["retry", "reparse"]
    idempotency_key: str = Field(min_length=1, max_length=256)
    original: SourceRef
    request_id: str | None = Field(default=None, pattern=r"^mr_[0-9a-f]{64}$")
    expected_state_version: StrictInt | None = Field(default=None, ge=1)
    expected_artifact_revision: StrictInt = Field(ge=0)
    expected_profile_digest: Digest
    processing_kind: Literal["image.interpretation"] = IMAGE_PROCESSING_KIND

    @model_validator(mode="after")
    def exact_parent(self):
        if (self.request_id is None) != (self.expected_state_version is None):
            raise ValueError("request identity and state version must be supplied together")  # noqa: TRY003
        if self.operation == "retry" and self.request_id is None:
            raise ValueError("retry requires the exact failed request and state version")  # noqa: TRY003
        return self

    @field_validator("idempotency_key")
    @classmethod
    def nonblank_key(cls, value: str):
        if not value.strip():
            raise ValueError("idempotency key must not be blank")  # noqa: TRY003
        return value


class ImageCaptureInput(MediaValue):
    scope_id: ScopeId
    capture_key: str = Field(min_length=1, max_length=256, pattern=r".*\S.*")
    processing_kind: Literal["image.interpretation"] = IMAGE_PROCESSING_KIND
    media_type: Literal["image/png", "image/jpeg"]
    bytes_base64: str = Field(
        min_length=4, max_length=11_184_812, pattern=r"^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$"
    )
    origin: ExperimentalImageOrigin
    context: ExperimentalImageContext

    @model_validator(mode="after")
    def capture_provenance(self):
        if self.origin.kind == "codex" and not all((
            self.origin.host_instance_id,
            self.origin.session_id,
            self.origin.turn_id,
        )):
            raise ValueError("Codex origin requires host, session and turn")  # noqa: TRY003
        return self
