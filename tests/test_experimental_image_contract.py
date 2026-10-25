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

"""Image transport and identity guarantees before experimental routes activate."""

import base64
from pathlib import Path

import pytest
import yaml
from fastapi.openapi.models import PathItem, Reference, RequestBody, Schema
from pydantic import ValidationError

from powercontext.builtin.images.identity import canonical_image_digest, original_image_source_id
from powercontext.builtin.images.models import OriginalImageSource
from powercontext.http import CaptureImageRequest, ExperimentalImageParseOutput

CONTRACT = Path(__file__).resolve().parents[1] / "openapi" / "powercontext.yaml"


def test_image_transport_preserves_non_ascii_context_and_exact_bytes() -> None:
    # This protects transport serialization, not image-format acceptance.
    data = b"\x89PNG\r\n\x1a\n\x00\xff"
    request = CaptureImageRequest.model_validate({
        "scope_id": "实验项目",
        "capture_key": "轮次 1",
        "media_type": "image/png",
        "bytes_base64": base64.b64encode(data).decode(),
        "origin": {"kind": "explicit_upload", "attachment_index": 0},
        "context": {"text": "检查截图中的错误文字"},
    })

    restored = CaptureImageRequest.model_validate_json(request.model_dump_json())

    assert restored == request
    assert base64.b64decode(restored.bytes_base64, validate=True) == data
    assert restored.context.text == "检查截图中的错误文字"


@pytest.mark.parametrize(
    "changes",
    [
        {"media_type": "image/svg+xml"},
        {"bytes_base64": "data:image/png;base64,AAAA"},
        {"file_path": "C:/private/image.png"},
        {"origin": {"kind": "explicit_upload", "attachment_index": True}},
        {"origin": {"kind": "explicit_upload", "attachment_index": 4}},
        {"bytes_base64": "not base64"},
        {"capture_key": " "},
    ],
)
def test_image_ingress_shape_rejects_unsupported_or_ambiguous_inputs(changes: dict[str, object]) -> None:
    payload = {
        "scope_id": "project",
        "capture_key": "turn",
        "media_type": "image/png",
        "bytes_base64": "AAAA",
        "origin": {"kind": "explicit_upload", "attachment_index": 0},
        "context": {"text": ""},
    }

    with pytest.raises(ValidationError):
        CaptureImageRequest.model_validate(payload | changes)


def test_image_only_transport_does_not_require_a_text_prompt() -> None:
    request = CaptureImageRequest.model_validate({
        "scope_id": "project",
        "capture_key": "image-only",
        "media_type": "image/jpeg",
        "bytes_base64": "AAAA",
        "origin": {"kind": "explicit_upload", "attachment_index": 0},
        "context": {"text": ""},
    })

    assert request.context.text == ""


def test_image_analysis_preserves_uncertainty_and_rejects_injected_authority() -> None:
    payload = {
        "readability": "partial",
        "ocr_text": "连接失败",
        "visual_description": "配置页面显示错误",
        "entities": [],
        "relations": [],
        "uncertainties": ["错误码太小无法确认"],
    }
    parsed = ExperimentalImageParseOutput.model_validate(payload)

    assert ExperimentalImageParseOutput.model_validate_json(parsed.model_dump_json()) == parsed
    assert parsed.model_dump(mode="json")["uncertainties"] == ["错误码太小无法确认"]
    with pytest.raises(ValidationError):
        ExperimentalImageParseOutput.model_validate(payload | {"scope_id": "another-project"})


def test_original_identity_distinguishes_scope_and_attachment_occurrences() -> None:
    first = original_image_source_id("project-a", "turn-1", 0)

    assert first == original_image_source_id("project-a", "turn-1", 0)
    assert first != original_image_source_id("project-b", "turn-1", 0)
    assert first != original_image_source_id("project-a", "turn-1", 1)
    assert first != original_image_source_id("project-a", "turn-2", 0)
    # Identity excludes bytes: changed bytes cannot silently select another occurrence.
    assert len(first) == 68


def test_canonical_profile_digest_is_stable_and_tracks_model_and_context() -> None:
    first = canonical_image_digest({"model": "internal-model", "context": "中文"})

    assert first == canonical_image_digest({"context": "中文", "model": "internal-model"})
    assert first != canonical_image_digest({"context": "中文", "model": "another-model"})
    assert first != canonical_image_digest({"context": "其他文字", "model": "internal-model"})


def test_legacy_original_fingerprint_survives_new_optional_provenance() -> None:
    legacy = {
        "name": "img_" + "1" * 64,
        "definition_version": "1",
        "materialization": "captured",
        "description": None,
        "capture_key": "codex:legacy",
        "asset": {
            "asset_id": "ia_" + "0" * 64,
            "digest": "sha256:" + "0" * 64,
            "media_type": "image/png",
            "byte_length": 10,
            "width": 1,
            "height": 1,
        },
        "origin": {
            "kind": "codex",
            "attachment_index": 0,
            "host_instance_id": "host",
            "session_id": "session",
            "turn_id": "turn",
        },
        "context": {"text": "", "source": None},
    }
    restored = OriginalImageSource.model_validate(legacy)
    assert restored.model_dump(mode="json") == legacy
    assert canonical_image_digest(restored.model_dump(mode="json")) == canonical_image_digest(legacy)
    modern = legacy | {
        "origin": legacy["origin"]
        | {"representation": "host_image", "message_id": "message", "client_variant": "desktop"}
    }
    source = OriginalImageSource.model_validate(modern)
    assert OriginalImageSource.model_validate_json(source.model_dump_json()) == source
    assert source.model_dump(mode="json") == modern


def test_implemented_image_operations_have_resolvable_scoped_contracts() -> None:
    contract = yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))
    experiment = contract["x-powercontext-media"]
    implemented = {
        path: item for path, item in contract["paths"].items() if path.startswith(("/v1/images/", "/v1/media/"))
    }

    assert experiment["active"] is True
    assert experiment["contract_version"] == "1"
    assert len(implemented) == 5
    ids = set()
    for item in implemented.values():
        operation = PathItem.model_validate(item).post
        assert operation is not None and operation.operationId is not None
        assert operation.operationId not in ids
        ids.add(operation.operationId)
        assert isinstance(operation.model_extra, dict)
        access = operation.model_extra["x-powercontext-access"]
        assert access["action"] in {"scope.read", "scope.contribute"}
        assert access["resource"] == {"type": "scope", "scope-id-from": "scope_id"}
        assert isinstance(operation.requestBody, RequestBody)
        request_schema = operation.requestBody.content["application/json"].schema_
        assert isinstance(request_schema, Reference | Schema)
        request_ref = request_schema.ref
        assert isinstance(request_ref, str)
        assert request_ref.removeprefix("#/components/schemas/") in contract["components"]["schemas"]
        assert operation.responses is not None
        assert {"401", "403", "503"} <= operation.responses.keys()

    def check_references(value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key == "$ref":
                    assert isinstance(nested, str)
                    target = contract
                    for part in nested.removeprefix("#/").split("/"):
                        target = target[part]
                else:
                    check_references(nested)
        elif isinstance(value, list):
            for nested in value:
                check_references(nested)

    assert ids == set(experiment["implemented_operations"])
    check_references(experiment)
    check_references(implemented)
