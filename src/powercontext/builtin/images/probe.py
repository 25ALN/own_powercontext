# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Explicit one-request synthetic Vision probe; no Source or asset persistence."""

from io import BytesIO
from typing import Any

from powercontext.builtin.images.config import parse_profile
from powercontext.builtin.images.vision import VisionFailure, VisionParser, prepare_image
from powercontext.http import ExperimentalImageContext

PROBE_TEXT = "POWERCONTEXT-PROBE-7351"


async def probe_vision(inference: object) -> dict[str, Any]:
    """Only an explicit caller invokes inference; settings checks never do."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        profile = parse_profile(inference)
        if profile is None:
            raise VisionFailure("image_vision_configuration_unavailable")  # noqa: TRY301
        parser = VisionParser(inference)
        headers = parser.headers_for(profile)
        picture = Image.new("RGB", (800, 180), "white")
        ImageDraw.Draw(picture).text((20, 60), PROBE_TEXT, fill="black", font=ImageFont.load_default(size=36))
        with BytesIO() as buffer:
            picture.save(buffer, format="PNG")
            prepared = prepare_image(buffer.getvalue(), "image/png")
        result = await parser.parse(profile, ExperimentalImageContext(text=""), prepared, headers)
    except ImportError:
        return {"ok": False, "error_code": "image_decoder_unavailable", "requests": 0, "source_saved": False}
    except VisionFailure as error:
        return {"ok": False, "error_code": error.code, "requests": error.usage.requests, "source_saved": False}
    except ValueError:
        return {
            "ok": False,
            "error_code": "image_vision_configuration_unavailable",
            "requests": 0,
            "source_saved": False,
        }
    recognized = PROBE_TEXT in result.output.ocr_text
    return {
        "ok": recognized,
        "error_code": None if recognized else "image_probe_text_not_recognized",
        "model": profile.model_id,
        "schema_valid": True,
        "recognized_probe_text": recognized,
        "latency_ms": result.latency_ms,
        "requests": result.usage.requests,
        "input_tokens": result.usage.input_tokens,
        "output_tokens": result.usage.output_tokens,
        "source_saved": False,
    }
