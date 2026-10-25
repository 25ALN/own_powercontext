# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The image-owned interpretation Family; publication does not create a Memory."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import Field, model_validator

from powercontext.artifacts import Artifact, ArtifactDraft
from powercontext.builtin.images.identity import canonical_image_digest, image_profile_digest
from powercontext.builtin.images.models import validate_parse_output
from powercontext.builtin.media.models import Digest, MediaValue, ScopedMediaSource
from powercontext.http import ExperimentalImageContext, ExperimentalImageParseOutput, ExperimentalImageParseProfile
from powercontext.sources.evidence import EvidenceAnchor


class ImageAnalysisContent(MediaValue):
    schema_: Literal["powercontext.image-analysis.v1"] = Field(default="powercontext.image-analysis.v1", alias="schema")
    original: ScopedMediaSource
    origin_anchor: EvidenceAnchor
    profile: ExperimentalImageParseProfile
    profile_digest: Digest
    input_digest: Digest
    context_digest: Digest
    context: ExperimentalImageContext
    output: ExperimentalImageParseOutput
    provider_revision: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def fixed_input(self):
        if self.original.scope_id != self.origin_anchor.scope_id or self.original.source != self.origin_anchor.source:
            raise ValueError("image analysis origin does not match its anchor")  # noqa: TRY003
        if image_profile_digest(self.profile) != self.profile_digest:
            raise ValueError("image analysis profile digest mismatch")  # noqa: TRY003
        if self.context_digest != canonical_image_digest(self.context.model_dump(mode="json")):
            raise ValueError("image analysis context digest mismatch")  # noqa: TRY003
        validate_parse_output(self.output)
        if len(self.output.model_dump_json().encode("utf-8")) > 65_536:
            raise ValueError("image analysis output exceeds 64 KiB")  # noqa: TRY003
        return self


class ImageAnalysis(Artifact[ImageAnalysisContent]):
    family: ClassVar[str] = "image-analysis"


class ImageAnalysisDraft(ArtifactDraft[ImageAnalysisContent]):
    family: ClassVar[str] = "image-analysis"
