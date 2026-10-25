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

"""Image inspection commands print their result without requiring JSON mode."""

import json

import pytest
from typer.testing import CliRunner

import powercontext.client.cli as client_cli
from powercontext.cli.app import create_cli
from powercontext.http import Capabilities, MediaRequestPage


@pytest.mark.parametrize("command", ["status", "list"])
def test_image_inspection_prints_result_in_default_mode(command, monkeypatch):
    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get_capabilities(self):
            return Capabilities.model_validate({
                "source_types": ["image.original"],
                "artifact_families": ["image-analysis"],
                "memory_extraction": False,
                "handoff_generation": False,
                "search_modes": [],
                "context_versions": [],
                "media": {
                    "enabled": True,
                    "available": True,
                    "vision_configured": True,
                    "processing_enabled": True,
                    "profile_digest": "sha256:" + "1" * 64,
                    "error_code": None,
                },
            })

        async def list_media_requests(self, request):
            return MediaRequestPage(items=[], next_cursor=None)

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *args, **kwargs: Client())
    result = CliRunner().invoke(
        create_cli([]), ["image", command] + (["--scope-id", "test-scope"] if command == "list" else [])
    )
    assert result.exit_code == 0, result.output
    if command == "status":
        assert "Images:" in result.output and '"enabled":true' in result.output
    else:
        assert json.loads(result.output)["items"] == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["request", "invalid"],
        [
            "retry",
            "invalid",
            "--retry-key",
            "retry",
            "--original-source-id",
            "original",
            "--expected-state-version",
            "1",
            "--expected-artifact-revision",
            "0",
            "--expected-profile-digest",
            "invalid",
        ],
        [
            "reparse",
            "original",
            "--reparse-key",
            "retry",
            "--expected-artifact-revision",
            "0",
            "--expected-profile-digest",
            "invalid",
        ],
        ["download", "invalid", "--output", "unused.png"],
    ],
)
def test_invalid_image_identity_reports_usage_error(arguments):
    result = CliRunner().invoke(create_cli([]), ["image", *arguments, "--scope-id", "test-scope"])
    assert result.exit_code == 2, result.output
    assert "Invalid" in result.output
    assert "Traceback" not in result.output
