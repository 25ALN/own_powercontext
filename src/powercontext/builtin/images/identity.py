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

"""Versioned canonical identities for the local Image Source experiment."""

from __future__ import annotations

import hashlib

import rfc8785
from pydantic import JsonValue, TypeAdapter

from powercontext.http import ExperimentalImageParseProfile

_JSON = TypeAdapter(JsonValue)


def canonical_image_digest(value: JsonValue, /) -> str:
    """Hash declared JSON meaning without depending on dictionary insertion order."""

    return "sha256:" + hashlib.sha256(rfc8785.dumps(_JSON.validate_python(value))).hexdigest()


def original_image_source_id(scope_id: str, capture_key: str, attachment_index: int, /) -> str:
    """Name an upload occurrence so changed bytes under the same key must conflict."""

    from powercontext.builtin.sources.journal import validate_scope_id

    validate_scope_id(scope_id)
    if not capture_key.strip() or len(capture_key) > 256:
        raise ValueError("capture_key must contain text and be at most 256 characters")  # noqa: TRY003
    if type(attachment_index) is not int or not 0 <= attachment_index <= 3:
        raise ValueError("attachment_index must be an integer from 0 through 3")  # noqa: TRY003
    identity: JsonValue = {
        "contract_version": "1",
        "scope_id": scope_id,
        "capture_key": capture_key,
        "attachment_index": attachment_index,
    }
    return "img_" + canonical_image_digest(identity).removeprefix("sha256:")


def image_profile_digest(profile: ExperimentalImageParseProfile, /) -> str:
    """Preserve the phase-two canonical profile identity, excluding credentials."""

    return canonical_image_digest({
        "contract_version": "1",
        "protocol": "openai_chat",
        "output_schema_version": "1",
        "profile": profile.model_dump(mode="json"),
    })
