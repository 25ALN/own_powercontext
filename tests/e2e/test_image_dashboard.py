# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Actual HTTP/SQLite evidence pages, exact originals and one-request probe."""

import asyncio
import json
import re
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import SecretStr

from powercontext.builtin.images.config import ImageConfig
from powercontext.builtin.images.probe import PROBE_TEXT, probe_vision
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.client import PowerContextClient
from powercontext.http import FlushMemoryRequest, MemorySearchMode, SearchMemoryRequest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import (
    AccessControlConfig,
    BearerAuthConfig,
    DashboardConfig,
    McpConfig,
    ServerSettings,
)
from tests.e2e.dream_support import atomic_memory_pipeline
from tests.e2e.media_support import (
    VISIBLE,
    Backend,
    _capture,
    _listen,
    _picture,
    _reparse,
    _request,
    _running,
    _scope,
    _terminal,
)
from tests.e2e.test_image_evidence import MemoryGenerator


@pytest.mark.parametrize("mode", ["RGBA", "LA", "P", "RGB"])
def test_png_thumbnail_composites_all_transparency_modes_on_white(tmp_path, mode):
    image = Image.new(mode, (24, 16))
    options = {"transparency": 0} if mode == "P" else {"transparency": (0, 0, 0)} if mode == "RGB" else {}
    with BytesIO() as buffer:
        image.save(buffer, format="PNG", **options)
        original = buffer.getvalue()

    async def scenario():
        backend = Backend()
        async with (
            _listen(backend.app) as endpoint,
            _running(tmp_path, endpoint + "/v1", dashboard=True) as (_, http),
        ):
            scope = await _scope(http)
            accepted = await _capture(http, _request(scope, "transparent", original))
            asset_id = accepted["asset"]["asset_id"]
            response = await http.get("/dashboard/image-thumbnails/" + asset_id, params={"scope": scope})
            assert response.status_code == 200
            with Image.open(BytesIO(response.content)) as thumbnail:
                assert thumbnail.getpixel((12, 8)) == (255, 255, 255)
            saved = await http.get("/dashboard/image-assets/" + asset_id, params={"scope": scope})
            assert saved.content == original

    asyncio.run(scenario())


def test_image_evidence_page_distinguishes_pending_analysis_and_memory_and_escapes_output(tmp_path):
    async def scenario():
        output = VISIBLE | {
            "ocr_text": "<script>private-untrusted</script> E429",
            "uncertainties": ["Cause unconfirmed"],
        }
        backend = Backend({"content": json.dumps(output)}, paused=True)
        async with _listen(backend.app) as endpoint:
            async with _running(tmp_path, endpoint + "/v1", dashboard=True) as (_, http):
                scope = await _scope(http)
                accepted = await _capture(http, _request(scope, "viewer", _picture()))
                await asyncio.wait_for(backend.entered.wait(), 30)
                url = "/dashboard/image-task/" + accepted["task_id"]
                page = await http.get(url, params={"scope": scope, "lang": "en"})
                assert page.status_code == 200, page.text
                assert "Original saved" in page.text and "do not imply Memory" in page.text
                backend.release.set()
                saved = await _terminal(http, scope, accepted["task_id"])
                page = await http.get(url, params={"scope": scope, "lang": "en"})
                assert "Analysis saved" in page.text and "Cause unconfirmed" in page.text
                assert "&lt;script&gt;private-untrusted" in page.text and "<script>private-untrusted" not in page.text
                assert "test-vision" in page.text
                asset_url = "/dashboard/image-assets/" + accepted["asset"]["asset_id"]
                original = await http.get(asset_url, params={"scope": scope})
                assert original.status_code == 200 and original.content == _picture()
                assert (
                    original.headers["Cache-Control"] == "no-store"
                    and original.headers["X-Content-Type-Options"] == "nosniff"
                )
                other = await _scope(http, "other")
                assert (await http.get(asset_url, params={"scope": other})).status_code == 404
            async with _running(tmp_path, None, enabled=False, dashboard=True) as (_, http):
                page = await http.get(url, params={"scope": scope})
                assert page.status_code == 200 and "E429" in page.text
                assert (await _terminal(http, scope, accepted["task_id"])) == saved

    asyncio.run(scenario())


def test_explicit_probe_uses_one_synthetic_request_without_persistence():
    async def scenario():
        backend = Backend({"content": json.dumps(VISIBLE | {"ocr_text": PROBE_TEXT})}, expected_size=(800, 180))
        async with _listen(backend.app) as endpoint:
            inference = InferenceConfig(
                vision_model="probe-model", vision_base_url=endpoint + "/v1", vision_deployment_id="probe"
            )
            result = await probe_vision(inference)
            assert result["ok"] and result["source_saved"] is False and result["requests"] == 1
            assert result["input_tokens"] is None and len(backend.requests) == 1

    asyncio.run(scenario())


def test_probe_rejects_invalid_endpoint_before_any_provider_request():
    inference = InferenceConfig(
        vision_model="probe-model",
        vision_base_url="http://127.0.0.1:invalid/v1",
        vision_deployment_id="probe",
    )
    result = asyncio.run(probe_vision(inference))
    assert result == {
        "ok": False,
        "error_code": "image_vision_configuration_unavailable",
        "requests": 0,
        "source_saved": False,
    }


def test_memory_evidence_link_keeps_exact_original_analysis_after_reparse(tmp_path):
    async def scenario():
        backend = Backend(
            {"content": json.dumps(VISIBLE | {"ocr_text": "VISIBLE-OLD E429"})},
            {"content": json.dumps(VISIBLE | {"ocr_text": "VISIBLE-NEW E429"})},
        )
        pipeline = atomic_memory_pipeline(MemoryGenerator())
        async with (
            _listen(backend.app) as endpoint,
            _running(tmp_path, endpoint + "/v1", dashboard=True, candidate_pipeline=pipeline) as (_, http),
        ):
            scope = await _scope(http)
            accepted = await _capture(http, _request(scope, "memory-viewer", _picture()))
            old = await _terminal(http, scope, accepted["task_id"])
            async with PowerContextClient("http://localhost", http_client=http) as client:
                await client.flush_memory(FlushMemoryRequest(scope_id=scope))
                found = await client.search_memory(
                    SearchMemoryRequest(scope_id=scope, query="IMG-034 E429", mode=MemorySearchMode.FTS)
                )
            citation = found.hits[0].memory.artifact
            query = {
                "scope": scope,
                "artifact": citation.artifact_id,
                "revision": citation.revision,
                "lang": "en",
            }
            reparsed = await _reparse(http, scope, accepted["original"]["source"], "new-view", 1)
            assert reparsed.status_code == 202, reparsed.text
            new = await _terminal(http, scope, reparsed.json()["request_id"])
            source = old["analysis"]["source"]["source_id"]
            url = "/dashboard/evidence/" + source
            page = await http.get("/dashboard/notes", params=query)
            assert page.status_code == 200 and url in page.text, page.text
            material_link = next(tag for tag in re.findall(r"<a\b[^>]*>", page.text) if url in tag)
            for selector in re.findall(r'(?:hx-target|data-bs-target)="(#[^"]+)"', material_link):
                assert f'id="{selector[1:]}"' in page.text, "material link has no actual display target"
            page = await http.get(url, params=query | {"origin": "notes", "source_type": "image.interpretation"})
            assert page.status_code == 200, page.text
            assert "VISIBLE-OLD" in page.text and "VISIBLE-NEW" not in page.text
            assert old["original"]["source"]["source_id"] in page.text
            unrelated = await http.get(
                "/dashboard/evidence/" + new["analysis"]["source"]["source_id"],
                params=query | {"origin": "notes", "source_type": "image.interpretation"},
            )
            assert unrelated.status_code == 404

    asyncio.run(scenario())


def test_dashboard_image_reads_require_authentication_and_fail_closed_without_access(tmp_path):
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}"),
        images=ImageConfig(enabled=True, asset_directory=tmp_path / "assets"),
        inference=InferenceConfig(
            vision_model="offline", vision_base_url="https://offline.example.invalid/v1", vision_deployment_id="test"
        ),
        auth=BearerAuthConfig(token=SecretStr("dashboard-test-token")),
        dashboard=DashboardConfig(enabled=True),
        mcp=McpConfig(enabled=False),
        access=AccessControlConfig(mode="enforced"),
    )
    app = create_server_app(settings=settings, scheduler_path=tmp_path / "scheduler.db")
    with TestClient(app) as client:
        headers = {"Authorization": "Bearer dashboard-test-token"}
        scope_response = client.post(
            "/v1/scopes",
            json={"title": "Image access", "summary": "Isolated image access test", "idempotency_key": "image-access"},
            headers=headers,
        )
        assert scope_response.status_code == 201, scope_response.text
        scope = scope_response.json()["scope_id"]
        accepted = client.post("/v1/images/capture", json=_request(scope, "auth", _picture()), headers=headers)
        assert accepted.status_code == 202, accepted.text
        url = "/dashboard/image-assets/" + accepted.json()["asset"]["asset_id"]
        assert client.get(url, params={"scope": scope}, headers=headers).content == _picture()
        denied = client.get(url, params={"scope": scope}, headers={"Authorization": "Bearer invalid-token"})
        assert denied.status_code == 401 and denied.content != _picture()
        app.state.access_control = None
        unavailable = client.get(url, params={"scope": scope}, headers=headers)
        assert unavailable.status_code == 503 and unavailable.content != _picture()
