# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Opt-in storage budgets and a non-secret, pinned Vision profile."""

import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from powercontext.http import ExperimentalImageParseProfile


class ImageConfig(BaseModel):
    """Budgets for opt-in SQLite media and the existing Supervisor image binding."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    asset_directory: Path | None = Field(
        default=None,
        deprecated=True,
        description="Legacy file-store setting; native media bytes are retained in SQLite.",
    )
    worker_concurrency: int = Field(default=1, ge=1, le=1)
    max_pending_per_scope: int = Field(default=256, ge=1, le=256)
    max_pending_total: int = Field(default=1024, ge=1, le=1024)
    max_asset_bytes_total: int = Field(default=1_073_741_824, ge=1, le=1_073_741_824)


def validate_vision_endpoint(value: str) -> str:
    """Require an explicit service address without embedded credentials."""

    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
    ):
        raise ValueError("invalid Vision endpoint")  # noqa: TRY003
    return value


def parse_profile(inference: object) -> ExperimentalImageParseProfile | None:
    """Snapshot declared settings without connecting or resolving secrets."""

    model = getattr(inference, "vision_model", None)
    endpoint = getattr(inference, "vision_base_url", None)
    deployment = getattr(inference, "vision_deployment_id", None)
    if getattr(inference, "vision_use_generation", False):
        declared = getattr(inference, "generation_model", None)
        if not isinstance(declared, str) or not declared.startswith("openai-chat:"):
            raise ValueError("Vision reuse requires an explicitly configured openai-chat generation model")  # noqa: TRY003
        model = declared.removeprefix("openai-chat:")
        endpoint = getattr(inference, "generation_base_url", None)
        deployment = "generation:" + model
    if not model or not endpoint or not deployment:
        return None
    return ExperimentalImageParseProfile.model_validate({
        "model_id": model,
        "deployment_id": deployment,
        "base_url": validate_vision_endpoint(str(endpoint)),
        "prompt_version": "image-evidence-v1",
        "preprocessing_version": "exif-strip-v1",
        "output_mode": "prompted_json",
        "timeout_seconds": getattr(inference, "vision_timeout_seconds", 120),
        "max_attempts": getattr(inference, "vision_max_attempts", 3),
        "max_output_tokens": getattr(inference, "vision_max_output_tokens", 4096),
    })


def vision_headers(inference: object) -> dict[str, SecretStr]:
    """Use the explicitly selected connection; never fall back to another role."""
    reuse = getattr(inference, "vision_use_generation", False)
    role = "generation_headers" if reuse else "vision_headers"
    headers = dict(getattr(inference, role, {}))
    # The existing OpenAI Chat generation transport resolves OPENAI_API_KEY
    # when explicit headers do not override Authorization. Dedicated Vision
    # never borrows that credential.
    if (
        reuse
        and not any(name.casefold() == "authorization" for name in headers)
        and (key := os.getenv("OPENAI_API_KEY"))
    ):
        headers["Authorization"] = SecretStr("Bearer " + key)
    return headers
