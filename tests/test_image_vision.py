# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Strict external output and visible-pixel preprocessing contracts."""

import hashlib
import json
from io import BytesIO

import pytest
from PIL import Image, PngImagePlugin

from powercontext.builtin.images.vision import VisionFailure, parse_vision_output, prepare_image


def _output(**changes):
    return {
        "readability": "readable",
        "ocr_text": "中文 E429",
        "visual_description": "Visible error",
        "entities": [],
        "relations": [],
        "uncertainties": [],
        **changes,
    }


@pytest.mark.parametrize(
    "invalid",
    [
        "```json\n" + json.dumps(_output()) + "\n```",
        json.dumps(_output())[:-1] + ',"ocr_text":"duplicate"}',
        json.dumps(_output(entities=[{"entity_id": "same", "label": "A"}, {"entity_id": "same", "label": "B"}])),
        json.dumps(_output(relations=[{"from_id": "missing", "to_id": "also-missing", "label": "arrow"}])),
        json.dumps(_output(scope_id="model-controlled-scope")),
        json.dumps(_output(ocr_text="", visual_description="")),
        json.dumps(_output(ocr_text="字" * 16385)),
        "[" * 30000 + "]" * 30000,
        '{"readability":"readable","ocr_text":"line one\nline two","visual_description":"", "entities":[],"relations":[],"uncertainties":[]}',
    ],
    ids=[
        "fenced",
        "duplicate-key",
        "duplicate-entity",
        "dangling-relation",
        "model-scope",
        "empty-visible",
        "oversized-ocr",
        "deep-invalid-json",
        "unescaped-newline",
    ],
)
def test_invalid_output_is_rejected_instead_of_repaired(invalid):
    with pytest.raises(VisionFailure):
        parse_vision_output(invalid)


def test_valid_chinese_relations_and_unreadable_audit_output_roundtrip():
    output = parse_vision_output(
        json.dumps(
            _output(
                entities=[{"entity_id": "a", "label": "客户端"}, {"entity_id": "b", "label": "服务"}],
                relations=[{"from_id": "a", "to_id": "b", "label": "箭头"}],
            ),
            ensure_ascii=False,
        )
    )
    assert output.ocr_text == "中文 E429" and output.relations[0].to_id == "b"
    assert (
        parse_vision_output(
            json.dumps(
                _output(readability="unreadable", ocr_text="", visual_description="", uncertainties=["Too blurred"])
            )
        ).readability.value
        == "unreadable"
    )


def test_preprocessing_orients_exif_preserves_png_alpha_and_removes_metadata():
    image = Image.new("RGBA", (24, 16), (120, 30, 180, 60))
    exif = Image.Exif()
    exif[274] = 6
    exif[270] = "private metadata"
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Private", "local secret marker")
    with BytesIO() as buffer:
        image.save(buffer, format="PNG", exif=exif, pnginfo=metadata)
        original = buffer.getvalue()
    prepared = prepare_image(original, "image/png")
    with Image.open(BytesIO(prepared.data)) as restored:
        assert restored.size == (16, 24)
        assert restored.getpixel((0, 0)) == (120, 30, 180, 60)
        assert not restored.getexif() and "Private" not in restored.info
    assert prepared.digest == "sha256:" + hashlib.sha256(prepared.data).hexdigest()
    assert prepared.data != original and prepare_image(original, "image/png") == prepared
