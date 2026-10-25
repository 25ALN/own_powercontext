# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Exact repository-owned origin anchors for derived evidence."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from powercontext.sources.models import SourceRef


class EvidenceAnchor(BaseModel):
    """Repository-validated original position, never a model-supplied timestamp."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str = Field(min_length=1, max_length=256)
    source: SourceRef
    journal_position: StrictInt = Field(ge=1)
    recorded_at: datetime | None = None

    @field_validator("recorded_at")
    @classmethod
    def aware_recording_time(cls, value: datetime | None):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("recording time requires a timezone")  # noqa: TRY003
        return value
