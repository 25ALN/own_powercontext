# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Native shared-media API round trips through the public Python client."""

import asyncio
import base64
from contextlib import asynccontextmanager

import httpx
import pytest

from powercontext.client import PowerContextClient
from powercontext.http import (
    CaptureImageRequest,
    GetMediaAssetRequest,
    GetMediaRequestRequest,
    ListMediaRequestsRequest,
    MediaRequestState,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings

from .test_media_storage import capture_input, config_for, picture


@asynccontextmanager
async def media_server(root, *, enabled=True):
    config = config_for(root / "http.db", enabled=enabled)
    settings = ServerSettings(
        database=config.database,
        images=config.images,
        inference=config.inference,
        runtime=config.runtime,
        access=AccessControlConfig(mode="disabled"),
        mcp=McpConfig(enabled=False),
        metrics=MetricsConfig(enabled=False),
    )
    app = create_server_app(settings=settings, scheduler_path=root / "scheduler.db")
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as http,
    ):
        yield app, http


async def create_scope(http):
    response = await http.post("/v1/scopes", json={"title": "Media", "summary": "Evidence", "idempotency_key": "media"})
    assert response.status_code == 201, response.text
    return response.json()["scope_id"]


def test_five_operations_native_client_and_generic_source_read_after_disable(tmp_path):
    async def scenario():
        async with media_server(tmp_path) as (_, http):
            scope = await create_scope(http)
            async with PowerContextClient("http://localhost", http_client=http) as client:
                command = CaptureImageRequest.model_validate(capture_input(scope).model_dump(mode="json"))
                receipt = await client.capture_image(command)
                assert receipt.original.source.source_type == "image.original"
                progress = await client.get_media_request(
                    GetMediaRequestRequest(scope_id=scope, request_id=receipt.request_id)
                )
                assert progress.state.value == "pending" and progress.attempt_count == 0
                page = await client.list_media_requests(
                    ListMediaRequestsRequest(scope_id=scope, states=[MediaRequestState.PENDING])
                )
                assert page.items == [progress]
                source = await client.get_source(scope, "image.original", receipt.original.source.source_id)
                assert source.content["asset"]["asset_id"] == receipt.asset.asset_id
                sources = await client.list_sources(scope)
                assert any(item.source_id == receipt.original.source.source_id for item in sources.items)
                asset = await client.get_media_asset(
                    GetMediaAssetRequest(scope_id=scope, asset_id=receipt.asset.asset_id)
                )
                assert base64.b64decode(asset.bytes_base64) == picture()
                assert (await client.capture_image(command)).request_id == receipt.request_id
            old = await http.post("/v1/experimental/images/capture", json=command.model_dump(mode="json"))
            assert old.status_code == 404
        async with media_server(tmp_path, enabled=False) as (_, http):
            read = await http.post("/v1/media/assets/get", json={"scope_id": scope, "asset_id": receipt.asset.asset_id})
            assert read.status_code == 200 and base64.b64decode(read.json()["bytes_base64"]) == picture()
            assert (
                await http.post("/v1/images/capture", json=capture_input(scope, "off").model_dump(mode="json"))
            ).status_code == 503
            capability = (await http.get("/v1/capabilities")).json()["media"]
            assert capability["available"] and not capability["enabled"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "change,expected",
    [
        ({"processing_kind": "audio.transcribe"}, 422),
        ({"bytes_base64": "bad="}, 422),
        ({"media_type": "image/gif"}, 422),
    ],
)
def test_unsupported_input_is_rejected_before_acceptance(tmp_path, change, expected):
    async def scenario():
        async with media_server(tmp_path) as (_, http):
            scope = await create_scope(http)
            value = capture_input(scope).model_dump(mode="json") | change
            response = await http.post("/v1/images/capture", json=value)
            assert response.status_code == expected, response.text
            page = await http.post("/v1/media/requests/list", json={"scope_id": scope})
            assert page.json()["items"] == []

    asyncio.run(scenario())


class Authentication:
    async def authenticate(self, request):
        from powercontext.server.authentication import AuthenticationResult
        from powercontext.server.authz import PrincipalRef

        name = request.headers.get("authorization", "Bearer admin").removeprefix("Bearer ")
        return AuthenticationResult(subject=PrincipalRef(type="service" if name == "admin" else "user", id=name))

    async def readiness(self):
        from powercontext.server.authentication import ProviderReadiness

        return ProviderReadiness(ready=True)


def test_media_scope_authorization_and_read_only_family(tmp_path):
    from powercontext.server.authz import PrincipalRef
    from powercontext.server.authz.composition import open_builtin_access_control

    async def scenario():
        config = config_for(tmp_path / "authorized.db")
        async with open_builtin_access_control(
            config.database, bootstrap_administrators=(PrincipalRef(type="service", id="admin"),), deployment_id="media"
        ) as access:
            settings = ServerSettings(
                database=config.database,
                images=config.images,
                inference=config.inference,
                runtime=config.runtime,
                access=AccessControlConfig(mode="enforced", deployment_id="media"),
                mcp=McpConfig(enabled=False),
                metrics=MetricsConfig(enabled=False),
            )
            app = create_server_app(
                settings=settings,
                authentication_provider=Authentication(),
                access_control=access,
                scheduler_path=tmp_path / "scheduler.db",
            )
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as http,
            ):
                scope = await create_scope(http)
                accepted = await http.post("/v1/images/capture", json=capture_input(scope).model_dump(mode="json"))
                assert accepted.status_code == 202, accepted.text
                receipt = accepted.json()
                for name, role in (
                    ("viewer", "scope.viewer"),
                    ("writer", "scope.contributor"),
                    ("manager", "scope.admin"),
                ):
                    response = await http.post(
                        "/v1/access/bindings/create",
                        json={
                            "subject": {"type": "user", "id": name},
                            "resource": {"type": "scope", "scope_id": scope},
                            "role": role,
                            "idempotency_key": name,
                        },
                    )
                    assert response.status_code == 201, response.text
                for name, code in (("outsider", 403), ("manager", 403), ("viewer", 200), ("writer", 200)):
                    headers = {"Authorization": "Bearer " + name}
                    response = await http.post(
                        "/v1/media/assets/get",
                        headers=headers,
                        json={"scope_id": scope, "asset_id": receipt["asset"]["asset_id"]},
                    )
                    assert response.status_code == code, response.text
                    response = await http.post(
                        "/v1/media/requests/get",
                        headers=headers,
                        json={"scope_id": scope, "request_id": receipt["request_id"]},
                    )
                    assert response.status_code == code, response.text
                writer = {"Authorization": "Bearer writer"}
                response = await http.post(
                    "/v1/images/capture", headers=writer, json=capture_input(scope, "writer").model_dump(mode="json")
                )
                assert response.status_code == 202, response.text
                for name in ("outsider", "manager", "viewer"):
                    response = await http.post(
                        "/v1/images/capture",
                        headers={"Authorization": "Bearer " + name},
                        json=capture_input(scope, "denied").model_dump(mode="json"),
                    )
                    assert response.status_code == 403, response.text
                # The dedicated result family cannot be created through generic mutation.
                response = await http.post(
                    f"/v1/scopes/{scope}/artifacts", json={"family": "image-analysis", "content": {}}
                )
                assert response.status_code == 422, response.text
                response = await http.post(
                    f"/v1/scopes/{scope}/sources", json={"source_type": "image.original", "content": {}}
                )
                assert response.status_code == 422, response.text

    asyncio.run(scenario())


@pytest.mark.parametrize("image_format,media_type", [("PNG", "image/png"), ("JPEG", "image/jpeg")])
def test_exact_png_and_jpeg_assets_and_cross_scope_isolation(tmp_path, image_format, media_type):
    from io import BytesIO

    from PIL import Image

    image = BytesIO()
    Image.new("RGB", (12, 10), "blue").save(image, format=image_format)
    data = image.getvalue()

    async def scenario():
        async with media_server(tmp_path) as (_, http):
            scope = await create_scope(http)
            payload = capture_input(scope).model_dump(mode="json") | {
                "media_type": media_type,
                "bytes_base64": base64.b64encode(data).decode(),
            }
            result = await http.post("/v1/images/capture", json=payload)
            assert result.status_code == 202, result.text
            asset = result.json()["asset"]
            response = await http.post("/v1/media/assets/get", json={"scope_id": scope, "asset_id": asset["asset_id"]})
            assert base64.b64decode(response.json()["bytes_base64"]) == data
            response = await http.post(
                "/v1/media/assets/get", json={"scope_id": "another-scope", "asset_id": asset["asset_id"]}
            )
            assert response.status_code == 404

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"capture_key": " "}, 422),
        ({"scope_id": " "}, 422),
        ({"origin": {"kind": "codex", "attachment_index": 0}}, 422),
        ({"media_type": "image/jpeg"}, 415),
        ({"bytes_base64": base64.b64encode(b"invalid image").decode()}, 422),
        ({"bytes_base64": base64.b64encode(b"x" * (8 * 1024 * 1024 + 1)).decode()}, 413),
    ],
)
def test_image_admission_failures_leave_no_request_or_original(tmp_path, changes, code):
    async def scenario():
        async with media_server(tmp_path) as (_, http):
            scope = await create_scope(http)
            response = await http.post(
                "/v1/images/capture", json=capture_input(scope).model_dump(mode="json") | changes
            )
            assert response.status_code == code, response.text
            requests = await http.post("/v1/media/requests/list", json={"scope_id": scope})
            sources = await http.get(f"/v1/scopes/{scope}/sources")
            assert requests.json()["items"] == [] and sources.json()["items"] == []

    asyncio.run(scenario())


def test_analysis_replacement_requires_exact_state_and_replay_returns_the_original_receipt(tmp_path):
    async def scenario():
        async with media_server(tmp_path) as (_, http):
            scope = await create_scope(http)
            receipt = (await http.post("/v1/images/capture", json=capture_input(scope).model_dump(mode="json"))).json()
            profile = (await http.get("/v1/capabilities")).json()["media"]["profile_digest"]
            command = {
                "scope_id": scope,
                "operation": "reparse",
                "idempotency_key": "replace",
                "original": receipt["original"]["source"],
                "expected_artifact_revision": 0,
                "expected_profile_digest": profile,
            }
            assert (await http.post("/v1/images/analysis/request", json=command)).status_code == 409
            command |= {"request_id": receipt["request_id"], "expected_state_version": 2}
            assert (await http.post("/v1/images/analysis/request", json=command)).status_code == 409
            command["expected_state_version"] = 1
            initial = await http.post("/v1/images/analysis/request", json=command)
            assert initial.status_code == 202, initial.text
            replay = await http.post("/v1/images/analysis/request", json=command)
            assert replay.status_code == 202 and replay.json() == initial.json()
            old = await http.post(
                "/v1/media/requests/get", json={"scope_id": scope, "request_id": receipt["request_id"]}
            )
            assert old.json()["state"] == "superseded"

    asyncio.run(scenario())


def test_sharing_analysis_never_grants_original_assets_or_scope_sources(tmp_path):
    from powercontext.server.authz import PrincipalRef
    from powercontext.server.authz.composition import open_builtin_access_control

    from .media_support import Backend, _listen, _terminal

    async def scenario():
        config = config_for(tmp_path / "shared.db")
        config.runtime.artifact_processing_families = ("image-analysis",)
        # Bootstrap the real builtin policy store; Server and child reconstruct their own adapters.
        async with open_builtin_access_control(
            config.database,
            bootstrap_administrators=(PrincipalRef(type="service", id="admin"),),
            deployment_id="shared-media",
        ):
            pass
        backend = Backend(expected_size=(8, 8))
        async with _listen(backend.app) as endpoint:
            config.inference.vision_base_url = endpoint + "/v1"
            settings = ServerSettings(
                database=config.database,
                images=config.images,
                inference=config.inference,
                runtime=config.runtime,
                access=AccessControlConfig(
                    mode="enforced", deployment_id="shared-media", background_principal_id="admin"
                ),
                mcp=McpConfig(enabled=False),
                metrics=MetricsConfig(enabled=False),
            )
            app = create_server_app(
                settings=settings, authentication_provider=Authentication(), scheduler_path=tmp_path / "scheduler.db"
            )
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as http,
            ):
                scope = await create_scope(http)
                contributor = await http.post(
                    "/v1/access/bindings/create",
                    json={
                        "subject": {"type": "service", "id": "admin"},
                        "resource": {"type": "scope", "scope_id": scope},
                        "role": "scope.contributor",
                        "idempotency_key": "scope-content",
                    },
                )
                assert contributor.status_code == 201, contributor.text
                accepted = await http.post("/v1/images/capture", json=capture_input(scope).model_dump(mode="json"))
                assert accepted.status_code == 202, accepted.text
                receipt = accepted.json()
                complete = await _terminal(http, scope, receipt["request_id"])
                assert complete["state"] == "succeeded", complete
                ref = complete["result"]["artifact"]
                grant = await http.post(
                    "/v1/access/bindings/create",
                    json={
                        "subject": {"type": "user", "id": "shared-reader"},
                        "resource": {
                            "type": "artifact",
                            "scope_id": scope,
                            "identity": {"family": "image-analysis", "artifact_id": ref["artifact_id"]},
                        },
                        "role": "artifact.viewer",
                        "idempotency_key": "share-analysis",
                    },
                )
                assert grant.status_code == 201, grant.text
                headers = {"Authorization": "Bearer shared-reader"}
                artifact = await http.get(
                    f"/v1/scopes/{scope}/artifacts/image-analysis/{ref['artifact_id']}/revisions/{ref['revision']}",
                    headers=headers,
                )
                assert artifact.status_code == 200, artifact.text
                image = await http.post(
                    "/v1/media/assets/get",
                    headers=headers,
                    json={"scope_id": scope, "asset_id": receipt["asset"]["asset_id"]},
                )
                assert image.status_code == 403, image.text
                source = receipt["original"]["source"]
                response = await http.get(
                    f"/v1/scopes/{scope}/sources/{source['source_type']}/{source['source_id']}", headers=headers
                )
                assert response.status_code == 403, response.text
                response = await http.post(
                    "/v1/media/requests/get",
                    headers=headers,
                    json={"scope_id": scope, "request_id": receipt["request_id"]},
                )
                assert response.status_code == 403, response.text

    asyncio.run(scenario())
