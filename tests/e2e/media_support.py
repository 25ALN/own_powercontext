# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared native HTTP and controlled provider fixtures; these do not qualify a real provider or host."""

from __future__ import annotations

import asyncio
import base64
import json
import socket
from contextlib import asynccontextmanager
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image

from powercontext.builtin.images.config import ImageConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, DashboardConfig, McpConfig, MetricsConfig, ServerSettings

CAPTURE = "/v1/images/capture"
ASSET_GET = "/v1/media/assets/get"
TASK_GET = "/v1/media/requests/get"
REPARSE = "/v1/images/analysis/request"


def _picture(image_format: str = "PNG", color: str = "red", size: tuple[int, int] = (24, 16)) -> bytes:
    with BytesIO() as buffer:
        Image.new("RGB", size, color).save(buffer, format=image_format)
        return buffer.getvalue()


def _request(scope: str, key: str, data: bytes, media_type: str = "image/png") -> dict[str, object]:
    return {
        "scope_id": scope,
        "capture_key": key,
        "media_type": media_type,
        "bytes_base64": base64.b64encode(data).decode("ascii"),
        "origin": {"kind": "explicit_upload", "attachment_index": 0},
        "context": {"text": "截图中的中文错误"},
    }


@asynccontextmanager
async def _server(
    root: Path,
    *,
    enabled: bool = True,
    model: str | None = "offline-test-model",
    database: SQLiteConfig | None = None,
    **budgets,
):
    settings = ServerSettings(
        database=database or SQLiteConfig(url=f"sqlite+aiosqlite:///{root / 'images.db'}"),
        images=ImageConfig(enabled=enabled, asset_directory=root / "assets", **budgets),
        inference=InferenceConfig(
            vision_model=model, vision_base_url="https://vision.example.invalid/v1", vision_deployment_id="test-v1"
        ),
        runtime=RuntimeConfig(artifact_processing_families=()),
        access=AccessControlConfig(mode="disabled"),
        mcp=McpConfig(enabled=False),
        metrics=MetricsConfig(enabled=False),
    )
    app = create_server_app(settings=settings, scheduler_path=root / "scheduler.db")
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://localhost"
        ) as http,
    ):
        yield app, http


async def _scope(http: httpx.AsyncClient, key: str = "project") -> str:
    response = await http.post("/v1/scopes", json={"title": key, "summary": "图片实验", "idempotency_key": key})
    assert response.status_code == 201, response.text
    return response.json()["scope_id"]


async def _capture(http: httpx.AsyncClient, payload: dict[str, object]) -> dict[str, Any]:
    response = await http.post(CAPTURE, json=payload)
    assert response.status_code == 202, response.text
    value = response.json()
    return {**value, "task_id": value["request_id"]}


VISIBLE = {
    "readability": "readable",
    "ocr_text": "项目 IMG-034 错误 E429",
    "visual_description": "A visible connection error; cause unknown",
    "entities": [],
    "relations": [],
    "uncertainties": [],
}


@asynccontextmanager
async def _listen(app, *, lifespan="auto"):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    url = f"http://127.0.0.1:{listener.getsockname()[1]}"
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", access_log=False, timeout_graceful_shutdown=1, lifespan=lifespan)
    )
    task = asyncio.create_task(server.serve(sockets=[listener]))
    try:
        for _ in range(1000):
            if server.started or task.done():
                break
            await asyncio.sleep(0.01)
        assert server.started
        yield url
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 10)
        listener.close()


class Backend:
    """Respond over TCP and observe the externally promised request budget."""

    def __init__(self, *script, paused=False, expected_size=(24, 16)):
        self.script = list(script)
        self.requests = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not paused:
            self.release.set()
        self.active = 0
        self.max_active = 0
        self.expected_size = expected_size
        self.app = FastAPI()
        self.app.post("/v1/chat/completions")(self.respond)

    async def respond(self, request: Request):
        payload = await request.json()
        content = [
            part
            for message in payload["messages"]
            for part in message["content"]
            if isinstance(message["content"], list)
        ]
        url = next(part["image_url"]["url"] for part in content if part["type"] == "image_url")
        data = base64.b64decode(url.split(",", 1)[1], validate=True)
        with Image.open(BytesIO(data)) as image:
            if self.expected_size is not None:
                assert image.size == self.expected_size
        self.requests.append({
            "model": payload["model"],
            "image": data,
            "max_tokens": payload["max_tokens"],
            "authorization": request.headers.get("Authorization"),
        })
        index = len(self.requests) - 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        self.entered.set()
        try:
            await self.release.wait()
            selected = self.script[index] if index < len(self.script) else {}
            if selected.get("delay"):
                await asyncio.sleep(selected["delay"])
            if selected.get("status", 200) != 200:
                return JSONResponse(
                    {"error": "private-provider-error"},
                    status_code=selected["status"],
                    headers={"Retry-After": str(selected.get("retry_after", 2))},
                )
            response = {
                "choices": [
                    {
                        "message": {"content": selected.get("content", json.dumps(VISIBLE, ensure_ascii=False))},
                        "finish_reason": selected.get("finish_reason", "stop"),
                    }
                ]
            }
            if "usage" in selected:
                response["usage"] = selected["usage"]
            return JSONResponse(response)
        finally:
            self.active -= 1


@asynccontextmanager
async def _running(
    root: Path,
    endpoint: str | None,
    *,
    enabled=True,
    model="test-vision",
    deployment="test-deployment",
    attempts=3,
    vision_timeout=5,
    headers=None,
    dashboard=False,
    inference=None,
    image_config=None,
    **injections,
):
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{root / 'images.db'}"),
        images=image_config or ImageConfig(enabled=enabled, asset_directory=root / "assets"),
        inference=inference
        or InferenceConfig(
            vision_model=model if endpoint else None,
            vision_base_url=endpoint,
            vision_deployment_id=deployment if endpoint else None,
            vision_max_attempts=attempts,
            vision_timeout_seconds=vision_timeout,
            vision_headers=headers or {},
        ),
        runtime=RuntimeConfig(artifact_processing_families=("image-analysis",)),
        access=AccessControlConfig(mode="disabled"),
        mcp=McpConfig(enabled=False),
        metrics=MetricsConfig(enabled=False),
        dashboard=DashboardConfig(enabled=dashboard),
    )
    app = create_server_app(settings=settings, scheduler_path=root / "scheduler.db", **injections)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as http,
    ):
        yield app, http


async def _terminal(http, scope, task_id, *, deadline=45):
    async with asyncio.timeout(deadline):
        while True:
            response = await http.post(TASK_GET, json={"scope_id": scope, "request_id": task_id})
            assert response.status_code == 200, response.text
            task = response.json()
            if task["state"] in {"succeeded", "failed", "superseded"}:
                return await _request_view(http, scope, task)
            await asyncio.sleep(0.05)


async def _request_view(http, scope, task):
    ref = task["original"]["source"]
    original = await http.get(f"/v1/scopes/{scope}/sources/{ref['source_type']}/{ref['source_id']}")
    assert original.status_code == 200, original.text
    analysis = None
    if task["result"] is not None:
        ref = task["result"]["artifact"]
        response = await http.get(
            f"/v1/scopes/{scope}/artifacts/{ref['family']}/{ref['artifact_id']}/revisions/{ref['revision']}"
        )
        assert response.status_code == 200, response.text
        analysis = response.json()["content"]
    return {
        **task,
        "task_id": task["request_id"],
        "analysis": task["publication"],
        "original_payload": original.json()["content"],
        "analysis_payload": analysis,
    }


async def _analysis_view(http, scope, source_id):
    response = await http.get(f"/v1/scopes/{scope}/sources/image.interpretation/{source_id}")
    assert response.status_code == 200, response.text
    value = response.json()["content"]
    original = value["original"]["source"]
    read = await http.get(f"/v1/scopes/{scope}/sources/{original['source_type']}/{original['source_id']}")
    assert read.status_code == 200, read.text
    ref = value["result"]["artifact"]
    artifact = await http.get(
        f"/v1/scopes/{scope}/artifacts/{ref['family']}/{ref['artifact_id']}/revisions/{ref['revision']}"
    )
    assert artifact.status_code == 200, artifact.text
    return {"original_payload": read.json()["content"], "analysis_payload": artifact.json()["content"]}


async def _reparse(http, scope, original, key, revision):
    capabilities = (await http.get("/v1/capabilities")).json()
    return await http.post(
        REPARSE,
        json={
            "scope_id": scope,
            "operation": "reparse",
            "idempotency_key": key,
            "original": original,
            "expected_artifact_revision": revision,
            "expected_profile_digest": capabilities["media"]["profile_digest"],
        },
    )
