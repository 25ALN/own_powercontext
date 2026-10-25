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

"""The MiniCPM chat template must retain instructions in one leading message."""

import asyncio
import json
from contextlib import AsyncExitStack

import httpx
from openai import AsyncOpenAI
from pydantic import AnyHttpUrl, BaseModel
from pydantic_ai.providers.openai import OpenAIProvider

from powercontext.builtin.inference.pydantic_ai import PydanticAIStructuredGenerator
from powercontext.builtin.runtime import composition


class Input(BaseModel):
    text: str


class Output(BaseModel):
    answer: str


def test_minicpm_prompted_output_keeps_instructions_and_schema_in_one_system_message(monkeypatch):
    requests = []

    async def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if [item["role"] for item in body["messages"]].count("system") != 1 or body["messages"][0]["role"] != "system":
            return httpx.Response(400, json={"error": {"message": "System message must be at the beginning."}})
        return httpx.Response(
            200,
            json={
                "id": "chat",
                "object": "chat.completion",
                "created": 0,
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"answer":"ok"}'},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    async def scenario():
        async with AsyncExitStack() as resources:
            http = await resources.enter_async_context(httpx.AsyncClient(transport=httpx.MockTransport(respond)))
            client = AsyncOpenAI(api_key="fixture-only", base_url="https://api.modelbest.cn/v1", http_client=http)
            monkeypatch.setattr(
                composition, "_provider_factory", lambda *a, **kw: lambda provider: OpenAIProvider(openai_client=client)
            )
            _, model = await composition._open_pydantic_ai_model(
                "openai-chat:MiniCPM-V-4.7-35B-A3B",
                base_url=AnyHttpUrl("https://api.modelbest.cn/v1"),
                headers={},
                resources=resources,
                instrumentation=None,
            )
            generator = PydanticAIStructuredGenerator(
                model=model, instructions="retain this task instruction", input_type=Input, output_type=Output
            )
            result = await generator.generate(Input(text="visible image evidence"))
            assert result.output.answer == "ok"

    asyncio.run(scenario())
    system = requests[0]["messages"][0]["content"]
    assert "retain this task instruction" in system and '"answer"' in system
