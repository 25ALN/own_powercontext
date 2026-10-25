# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Opt-in real Codex CLI attachment/Hook execution, isolated from account data."""

import asyncio
import base64
import json
import os
import shutil
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont

from powercontext.client import PowerContextClient
from powercontext.http import FlushMemoryRequest, MemorySearchMode, SearchMemoryRequest
from powercontext.server.configuration import server_settings_context
from tests.e2e.dream_support import atomic_memory_pipeline
from tests.e2e.media_support import ASSET_GET, Backend, _analysis_view, _listen, _running, _scope, _terminal
from tests.e2e.test_image_evidence import MemoryGenerator

ROOT = Path(__file__).resolve().parents[2]


async def _accepted_receipts(home, count):
    deadline = time.monotonic() + 20
    result = []
    while time.monotonic() < deadline:
        database = home / "powercontext/image-capture/outbox.sqlite3"
        if database.exists():
            with sqlite3.connect(database) as connection:
                rows = connection.execute(
                    "SELECT job_id,request,task_id FROM attachments WHERE state='accepted' ORDER BY created"
                ).fetchall()
            grouped = {}
            for job_id, request, task_id in rows:
                item = json.loads(request)
                grouped.setdefault(job_id, []).append({
                    "index": item["origin"]["attachment_index"],
                    "task_id": task_id,
                })
            result = [{"accepted": value} for value in grouped.values()]
            if any(len(item["accepted"]) == count for item in result):
                return result
        await asyncio.sleep(0.2)
    raise AssertionError  # Inspect the isolated outbox when host delivery did not complete within its budget.


def _assert_image_bytes(asset, task, source_file, provider_request, index):
    saved = base64.b64decode(asset["bytes_base64"])
    if task["original_payload"]["origin"].get("representation") == "host_image":
        images = [
            part["image_url"]
            for item in provider_request["input"]
            if item.get("role") == "user"
            for part in item.get("content", [])
            if part.get("type") == "input_image"
        ]
        assert saved == base64.b64decode(images[index].partition(",")[2])
    else:
        assert saved == source_file.read_bytes()


def test_real_codex_cli_capture_restart_recall_and_exact_image_readback(tmp_path):
    binary = os.environ.get("POWERCONTEXT_TEST_CODEX_BIN")
    if not binary:
        pytest.skip("set POWERCONTEXT_TEST_CODEX_BIN for opt-in isolated real CLI acceptance")

    async def scenario():
        home = tmp_path / "codex-home"
        home.mkdir()
        plugin = tmp_path / "plugin"
        shutil.copytree(
            ROOT / "integrations/codex/plugins/powercontext",
            plugin,
            ignore=shutil.ignore_patterns(".venv", "__pycache__"),
        )
        uv = shutil.which("uv")
        assert uv, "uv is required to exercise the shipped plugin commands"
        prepared = await asyncio.to_thread(
            subprocess.run,
            [uv, "sync", "--frozen", "--quiet", "--project", str(plugin)],
            cwd=tmp_path,
            capture_output=True,
            timeout=45,
            check=False,
        )
        assert prepared.returncode == 0, "cannot prepare the isolated plugin runtime"
        requests = []
        agent = FastAPI()

        @agent.post("/v1/responses")
        async def respond(request: Request):
            content = await request.json()
            requests.append(content)
            message = {
                "id": "msg_mock",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "ok", "annotations": []}],
            }
            response = {
                "id": "resp_mock",
                "object": "response",
                "created_at": int(time.time()),
                "status": "completed",
                "model": "mock",
                "output": [message],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
            events = [
                {"type": "response.created", "response": response | {"status": "in_progress", "output": []}},
                {
                    "type": "response.output_item.added",
                    "output_index": 0,
                    "item": message | {"status": "in_progress", "content": []},
                },
                {
                    "type": "response.output_text.delta",
                    "item_id": message["id"],
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "ok",
                },
                {"type": "response.output_item.done", "output_index": 0, "item": message},
                {"type": "response.completed", "response": response},
            ]
            body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
            return Response(body, media_type="text/event-stream")

        backend = Backend(expected_size=None)
        async with _listen(agent) as agent_url, _listen(backend.app) as local_vision:
            model, deployment, vision, headers = "test-vision", "test-deployment", local_vision + "/v1", {}
            if env_file := os.environ.get("POWERCONTEXT_TEST_VISION_ENV_FILE"):
                with server_settings_context(env_file=Path(env_file)) as configured:
                    model = configured.inference.vision_model
                    deployment = configured.inference.vision_deployment_id
                    vision = str(configured.inference.vision_base_url)
                    headers = configured.inference.vision_headers
            pipeline = atomic_memory_pipeline(MemoryGenerator())
            async with _running(
                tmp_path,
                vision,
                model=model,
                deployment=deployment,
                headers=headers,
                candidate_pipeline=pipeline,
                dashboard=True,
                vision_timeout=120,
            ) as (app, http):
                scope = await _scope(http)
                async with _listen(app, lifespan="off") as server_url:
                    (plugin / ".mcp.json").write_text(
                        json.dumps({
                            "mcpServers": {
                                "powercontext": {
                                    "type": "http",
                                    "url": server_url + "/mcp",
                                    "required": False,
                                    "env_http_headers": {"Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"},
                                }
                            }
                        })
                    )
                    (home / "config.toml").write_text(
                        f'''model = "mock"
model_provider = "fixture"
approval_policy = "never"
sandbox_mode = "read-only"
web_search = "disabled"
check_for_update_on_startup = false
[analytics]
enabled = false
[feedback]
enabled = false
[features]
hooks = true
apps = false
remote_plugin = false
memories = false
shell_tool = false
responses_websockets = false
enable_request_compression = false
[model_providers.fixture]
name = "isolated test provider"
base_url = "{agent_url}/v1"
wire_api = "responses"
requires_openai_auth = false
supports_websockets = false
request_max_retries = 0
stream_max_retries = 0
[projects."{tmp_path.as_posix()}"]
trust_level = "trusted"
''',
                        encoding="utf-8",
                    )
                    hooks = json.loads((plugin / "hooks/hooks.json").read_text())["hooks"]["UserPromptSubmit"][0][
                        "hooks"
                    ]
                    recall, command = [hook["command"].replace("${PLUGIN_ROOT}", plugin.as_posix()) for hook in hooks]
                    (home / "hooks.json").write_text(
                        json.dumps({
                            "hooks": {
                                "UserPromptSubmit": [
                                    {
                                        "hooks": [
                                            {"type": "command", "command": recall, "timeout": 10},
                                            {"type": "command", "command": command, "timeout": 10},
                                        ]
                                    }
                                ],
                                "Stop": [{"hooks": [{"type": "command", "command": command, "timeout": 10}]}],
                            }
                        })
                    )
                    env = {
                        key: value
                        for key, value in os.environ.items()
                        if key.upper()
                        in {
                            "PATH",
                            "SYSTEMROOT",
                            "WINDIR",
                            "TEMP",
                            "TMP",
                            "USERPROFILE",
                            "APPDATA",
                            "LOCALAPPDATA",
                            "PATHEXT",
                            "COMSPEC",
                        }
                    }
                    env.update({
                        "CODEX_HOME": str(home),
                        "CODEX_SQLITE_HOME": str(home / "sqlite"),
                        "POWERCONTEXT_CODEX_SCOPE_ID": scope,
                        "POWERCONTEXT_CODEX_CAPTURE_IMAGES": "true",
                        "POWERCONTEXT_CODEX_CAPTURE_PROMPTS": "false",
                        "POWERCONTEXT_DIAGNOSTIC_STATE_FILE": str(tmp_path / "diagnostics.json"),
                        "NO_PROXY": "127.0.0.1,localhost",
                    })
                    image = tmp_path / "attachment.png"
                    picture = Image.new("RGB", (800, 300), "white")
                    ImageDraw.Draw(picture).text(
                        (25, 70), "IMG-034 E429 connection failed", fill="black", font=ImageFont.load_default(size=36)
                    )
                    picture.save(image)

                    async def run(*args):
                        with (
                            (tmp_path / "codex-stdout.txt").open("wb") as stdout_file,
                            (tmp_path / "codex-stderr.txt").open("wb") as stderr_file,
                        ):
                            proc = await asyncio.create_subprocess_exec(
                                binary,
                                "--dangerously-bypass-hook-trust",
                                "exec",
                                "--skip-git-repo-check",
                                "--json",
                                "--cd",
                                str(tmp_path),
                                *args,
                                env=env,
                                stdin=asyncio.subprocess.DEVNULL,
                                stdout=stdout_file,
                                stderr=stderr_file,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                            )
                            try:
                                await asyncio.wait_for(proc.wait(), 45)
                            except TimeoutError:
                                proc.kill()
                                await proc.wait()
                                raise
                        assert proc.returncode == 0, "isolated Codex command failed; inspect local codex-stderr.txt"
                        return (tmp_path / "codex-stdout.txt").read_bytes()

                    await run("--image", str(image), "--", "Describe the image.")
                    captured = await _accepted_receipts(home, 1)
                    assert len(captured) == 1
                    state = captured[0]
                    task = await _terminal(http, scope, state["accepted"][0]["task_id"], deadline=130)
                    assert task["state"] == "succeeded" and "E429" in task["analysis_payload"]["output"]["ocr_text"]
                    original = await http.post(
                        ASSET_GET, json={"scope_id": scope, "asset_id": task["original_payload"]["asset"]["asset_id"]}
                    )
                    _assert_image_bytes(original.json(), task, image, requests[-1], 0)
                    async with PowerContextClient(server_url, http_client=http) as client:
                        assert (
                            await client.flush_memory(FlushMemoryRequest(scope_id=scope))
                        ).processed_source_count == 1
                    jpeg = tmp_path / "attachment.jpg"
                    picture.save(jpeg, format="JPEG", quality=90)
                    await run("--image", str(image), "--image", str(jpeg), "--", "Describe the image.")
                    captured = await _accepted_receipts(home, 2)
                    ordered = next(receipt for receipt in captured if len(receipt.get("accepted", [])) == 2)
                    for index, original_path in enumerate((image, jpeg)):
                        next_task = await _terminal(http, scope, ordered["accepted"][index]["task_id"], deadline=130)
                        assert next_task["state"] == "succeeded"
                        assert next_task["original_payload"]["origin"]["attachment_index"] == index
                        original = await http.post(
                            ASSET_GET,
                            json={"scope_id": scope, "asset_id": next_task["original_payload"]["asset"]["asset_id"]},
                        )
                        _assert_image_bytes(original.json(), next_task, original_path, requests[-1], index)
                image.unlink()
            async with (
                _running(tmp_path, None, enabled=False) as (app, http),
                _listen(app, lifespan="off") as server_url,
            ):
                data = json.loads((plugin / ".mcp.json").read_text())
                data["mcpServers"]["powercontext"]["url"] = server_url + "/mcp"
                (plugin / ".mcp.json").write_text(json.dumps(data))
                await run("--", "Recall IMG-034 E429 from saved memory.")
                assert "截图显示 E429" in json.dumps(requests[-1]["input"], ensure_ascii=False), (
                    "new real CLI request did not receive recalled evidence"
                )
                async with PowerContextClient(server_url, http_client=http) as client:
                    found = await client.search_memory(
                        SearchMemoryRequest(scope_id=scope, query="IMG-034 E429", mode=MemorySearchMode.FTS)
                    )
                    ref = found.hits[0].memory.artifact
                    entry = await client.get_artifact_revision(scope, "atomic-memory", ref.artifact_id, ref.revision)
                exact = await _analysis_view(http, scope, entry.sources[0].source_id)
                assert exact["analysis_payload"] == task["analysis_payload"]
                assert exact["original_payload"] == task["original_payload"]

    asyncio.run(scenario())
