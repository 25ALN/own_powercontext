# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Bounded PNG/JPEG admission; decoding is independent of persistence."""

from __future__ import annotations

import base64
import binascii
import hashlib
from io import BytesIO

from powercontext.builtin.images.errors import ImageError
from powercontext.http import ExperimentalImageAsset

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_ENCODED_IMAGE_BYTES = 4 * ((MAX_IMAGE_BYTES + 2) // 3)


def decode_image_bytes(encoded: str) -> bytes:
    """Decode bounded transport bytes; paths, URLs and data URIs are invalid."""

    if len(encoded) > MAX_ENCODED_IMAGE_BYTES:
        raise ImageError(413, "image_too_large")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ImageError(422, "image_invalid_base64") from error
    if not data:
        raise ImageError(422, "image_empty")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError(413, "image_too_large")
    return data


def validate_image(data: bytes, media_type: str) -> ExperimentalImageAsset:
    """Check format, dimensions and a fully decoded single frame."""

    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as error:
        raise ImageError(503, "image_decoder_unavailable") from error
    expected_format = {"image/png": "PNG", "image/jpeg": "JPEG"}[media_type]
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != expected_format or getattr(image, "n_frames", 1) != 1:
                raise ImageError(415, "image_format_unsupported")
            width, height = image.size
            if min(width, height) < 1 or max(width, height) > 16_384 or width * height > 20_000_000:
                raise ImageError(413, "image_pixel_budget")
            image.verify()
        with Image.open(BytesIO(data), formats=[expected_format]) as image:
            image.load()
    except Image.DecompressionBombError as error:
        raise ImageError(413, "image_pixel_budget") from error
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as error:
        raise ImageError(422, "image_invalid_data") from error
    digest = hashlib.sha256(data).hexdigest()
    return ExperimentalImageAsset.model_validate({
        "asset_id": "ia_" + digest,
        "digest": "sha256:" + digest,
        "media_type": media_type,
        "byte_length": len(data),
        "width": width,
        "height": height,
    })
