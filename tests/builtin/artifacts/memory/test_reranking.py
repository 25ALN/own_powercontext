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

"""Behavior tests for PowerContext Memory reranking."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import (
    HTTPMemoryReranker,
    LLMMemoryReranker,
    MemoryHit,
    MemoryRerankCandidate,
    MemoryRerankInput,
    MemoryRerankOutput,
)
from powercontext.builtin.inference import GenerationResult, InferenceUsage


class _RecordingGenerator:
    def __init__(self, selected_ranks: tuple[int, ...]) -> None:
        self._selected_ranks = selected_ranks
        self.inputs: list[MemoryRerankInput] = []

    async def generate(self, value: MemoryRerankInput, /) -> GenerationResult[MemoryRerankOutput]:
        self.inputs.append(value)
        return GenerationResult(
            output=MemoryRerankOutput(selected_ranks=self._selected_ranks),
            usage=InferenceUsage(requests=1, input_tokens=30, output_tokens=5),
        )


def _hit(rank: int) -> MemoryHit:
    return MemoryHit(
        memory_ref=ArtifactRef(family="memory", artifact_id="memory", revision=1),
        entry_id=f"entry-{rank}",
        entry_version_id=f"entry-{rank}-v1",
        text=f"Candidate {rank}",
        score=1 / rank,
        matched_by=("fts",),
    )


def test_llm_reranker_preserves_identity_and_normalizes_selected_ranks() -> None:
    async def scenario() -> None:
        generator = _RecordingGenerator((3, 3, 99, 1))
        reranker = LLMMemoryReranker(generator)
        candidates = tuple(_hit(rank) for rank in range(1, 5))

        decision = await reranker.rerank("Which candidate matters?", candidates, 2)

        assert decision.selected_ranks == (3, 1)
        assert decision.discarded_rank_count == 2
        assert decision.used_fallback is False
        assert decision.usage.requests == 1
        assert generator.inputs == [
            MemoryRerankInput(
                query="Which candidate matters?",
                max_results=2,
                candidates=tuple(MemoryRerankCandidate(rank=rank, text=f"Candidate {rank}") for rank in range(1, 5)),
            )
        ]

    asyncio.run(scenario())


def test_llm_reranker_falls_back_to_coarse_order_when_no_rank_is_valid() -> None:
    async def scenario() -> None:
        reranker = LLMMemoryReranker(_RecordingGenerator((0, 8, 8)))

        decision = await reranker.rerank("query", tuple(_hit(rank) for rank in range(1, 4)), 2)

        assert decision.selected_ranks == (1, 2)
        assert decision.discarded_rank_count == 3
        assert decision.used_fallback is True

    asyncio.run(scenario())


def test_http_reranker_batches_and_sorts_scores_globally_with_stable_ties() -> None:
    async def scenario() -> None:
        requests: list[dict[str, Any]] = []
        responses = iter(
            (
                {"scores": [{"rank": 2, "score": 0.9}, {"rank": 1, "score": 0.1}]},
                {"scores": [{"rank": 4, "score": 0.9}, {"rank": 3, "score": 0.95}]},
            )
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(200, json=next(responses))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client, batch_size=2)
            decision = await reranker.rerank("query", tuple(_hit(rank) for rank in range(1, 5)), 2)

        assert [item["rank"] for request in requests for item in request["candidates"]] == [1, 2, 3, 4]
        assert [item["text"] for request in requests for item in request["candidates"]] == [
            "Candidate 1", "Candidate 2", "Candidate 3", "Candidate 4"
        ]
        assert decision.selected_ranks == (3, 2)
        assert decision.usage.requests == 2

    asyncio.run(scenario())


def test_http_reranker_rejects_invalid_scores_and_propagates_http_errors() -> None:
    async def scenario() -> None:
        async def invalid_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"scores": [{"rank": 1, "score": float("nan")}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(invalid_handler)) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client)
            with pytest.raises(ValueError):
                await reranker.rerank("query", (_hit(1),), 1)

        async def error_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        async with httpx.AsyncClient(transport=httpx.MockTransport(error_handler)) as client:
            reranker = HTTPMemoryReranker("https://reranker.test/score", client=client)
            with pytest.raises(httpx.HTTPStatusError):
                await reranker.rerank("query", (_hit(1),), 1)

    asyncio.run(scenario())
