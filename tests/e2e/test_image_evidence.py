# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Image evidence -> actual Memory/Topic storage and recall; generation is controlled."""

from __future__ import annotations

import asyncio
import json

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.artifacts.handoff.generation import (
    DefaultHandoffEvidenceProjector,
    HandoffGenerationOutput,
    HandoffGenerationStatement,
    LLMHandoffGenerationPipeline,
)
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING, TopicMemoryContent
from powercontext.builtin.artifacts.topic_memory.generation import (
    TopicMemoryGlobalOutput,
    TopicMemoryProbe,
    TopicMemoryProbeOutput,
    TopicMemoryProposal,
)
from powercontext.builtin.evidence.resolver import EvidenceResolver
from powercontext.builtin.images.models import IMAGE_INTERPRETATION_TYPE as IMAGE_ANALYSIS_TYPE
from powercontext.builtin.images.models import IMAGE_ORIGINAL_TYPE as ORIGINAL_IMAGE_TYPE
from powercontext.builtin.inference import GenerationResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.topic_memory_processing import (
    ArtifactProcessingWaveKind,
    TopicMemoryAtomicPublisher,
    TopicMemoryProcessor,
    TopicMemoryStageSet,
    TopicMemoryWindowAssignment,
)
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.client import PowerContextClient
from powercontext.http import FlushMemoryRequest, MemorySearchMode, SearchMemoryRequest
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, SourceRef
from tests.e2e.dream_support import atomic_memory_pipeline
from tests.e2e.media_support import (
    VISIBLE,
    Backend,
    _analysis_view,
    _capture,
    _listen,
    _picture,
    _reparse,
    _request,
    _running,
    _scope,
    _terminal,
)


class TextProjector(DefaultHandoffEvidenceProjector):
    def project_source(self, source, /):
        return BUILTIN_SOURCE_REGISTRY.project(source, TEXT_EVIDENCE_PROJECTION_KEY)


class MemoryGenerator:
    def __init__(self):
        self.inputs = []

    async def generate(self, value, /):
        self.inputs.append(value)
        assert value.evidence
        for evidence in value.evidence:
            projected = evidence.content
            assert projected["source_type"] == IMAGE_ANALYSIS_TYPE
            assert "Machine interpretation" in projected["content"]
            assert "E429" in projected["content"]
            assert "bytes_base64" not in json.dumps(projected) and "base_url" not in json.dumps(projected)
            assert evidence.source_metadata == projected["metadata"]
            assert "base_url" not in json.dumps(evidence.source_metadata)
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=(
                    AtomicMemoryCandidate(
                        kind="fact",
                        text="项目 IMG-034 截图显示 E429 连接失败,根因尚未确认。",
                        evidence_ids=(value.evidence[0].evidence_id,),
                    ),
                )
            )
        )


class HandoffGenerator:
    async def generate(self, value, /):
        assert value.evidence[0].content["source_type"] == IMAGE_ANALYSIS_TYPE
        assert "E429" in value.evidence[0].content["content"]
        return GenerationResult(
            output=HandoffGenerationOutput(
                state=(
                    HandoffGenerationStatement(
                        text="IMG-034 显示 E429;需要核对原图。",
                        evidence_ids=(value.evidence[0].evidence_id,),
                    ),
                ),
                disposition="continuable",
            )
        )


def test_original_only_cursor_late_analysis_memory_handoff_and_new_session_recall(tmp_path):
    async def scenario():
        backend = Backend(paused=True)
        generator = MemoryGenerator()
        pipeline = atomic_memory_pipeline(generator)
        handoff = LLMHandoffGenerationPipeline(HandoffGenerator(), evidence_projector=TextProjector())
        async with _listen(backend.app) as endpoint:
            async with _running(tmp_path, endpoint + "/v1", candidate_pipeline=pipeline, handoff_pipeline=handoff) as (
                _,
                http,
            ):
                scope = await _scope(http)
                accepted = await _capture(http, _request(scope, "late", _picture()))
                await asyncio.wait_for(backend.entered.wait(), 30)
                async with PowerContextClient("http://localhost", http_client=http) as client:
                    original_only = await client.flush_memory(FlushMemoryRequest(scope_id=scope))
                    assert (
                        original_only.processed_source_count == 0
                        and original_only.current_cursor == accepted["sequence"]
                    )
                    assert not generator.inputs
                    backend.release.set()
                    task = await _terminal(http, scope, accepted["task_id"])
                    flushed = await client.flush_memory(FlushMemoryRequest(scope_id=scope))
                    assert flushed.processed_source_count == 1 and flushed.previous_cursor == accepted["sequence"]
                    assert flushed.current_cursor > flushed.previous_cursor and flushed.status.value == "processed"
                response = await http.post(
                    "/v1/handoff/prepare",
                    json={
                        "scope_id": scope,
                        "objective": "继续排查截图",
                        "evidence": [
                            {
                                "kind": "source",
                                "source_ref": {
                                    "name": IMAGE_ANALYSIS_TYPE,
                                    "source_id": task["analysis"]["source"]["source_id"],
                                },
                            }
                        ],
                    },
                )
                assert response.status_code == 200, response.text
                assert (
                    response.json()["state"][0]["citations"][0]["source_ref"]["source_id"]
                    == task["analysis"]["source"]["source_id"]
                )
            async with (
                _running(tmp_path, None, enabled=False) as (_, http),
                PowerContextClient("http://localhost", http_client=http) as client,
            ):
                found = await client.search_memory(
                    SearchMemoryRequest(scope_id=scope, query="IMG-034 E429", mode=MemorySearchMode.FTS)
                )
                assert found.hits and "E429" in found.hits[0].memory.text
                hit = found.hits[0]
                entry = await client.get_artifact_revision(
                    scope, "atomic-memory", hit.memory.artifact.artifact_id, hit.memory.artifact.revision
                )
                assert entry.sources[0].source_id == task["analysis"]["source"]["source_id"]
                exact = await _analysis_view(http, scope, entry.sources[0].source_id)
                assert exact["analysis_payload"] == task["analysis_payload"]
                assert exact["original_payload"]["asset"] == accepted["asset"]

    asyncio.run(scenario())


def test_lineage_original_is_excluded_and_same_asset_is_one_attested_group(tmp_path):
    async def scenario():
        backend = Backend()
        async with _listen(backend.app) as endpoint, _running(tmp_path, endpoint + "/v1") as (_, http):
            scope = await _scope(http)
            first = await _capture(http, _request(scope, "occurrence-one", _picture()))
            one = await _terminal(http, scope, first["task_id"])
            second = await _capture(http, _request(scope, "occurrence-two", _picture()))
            two = await _terminal(http, scope, second["task_id"])
            reparse = await _reparse(http, scope, first["original"]["source"], "same-asset", 1)
            three = await _terminal(http, scope, reparse.json()["request_id"])
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'images.db'}"),
            runtime=RuntimeConfig(artifact_processing_families=("image-analysis",)),
        )
        async with open_builtin_contexts(config) as contexts:
            authorized = []

            async def authorize(ref):
                authorized.append(ref)

            resolver = EvidenceResolver(
                scope_id=scope,
                sources=contexts.repositories.sources,
                artifacts=contexts.repositories.artifacts,
                authorize=authorize,
                source_projector=lambda source: json.dumps(
                    BUILTIN_SOURCE_REGISTRY.project(source, TEXT_EVIDENCE_PROJECTION_KEY), ensure_ascii=False
                ),
            )
            refs = tuple(SourceRef.model_validate(task["analysis"]["source"]) for task in (one, two, three))
            async with contexts.database.transaction() as connection:
                resolved = await resolver.resolve(connection, sources=refs)
            assert len(resolved.projection.root_groups) == 1
            assert resolved.projection.root_groups[0].independence == "attested"
            assert {ref.source_id for ref in resolved.projection.root_groups[0].sources} == {
                ref.source_id for ref in refs
            }
            originals = [
                node
                for node in resolved.manifest.nodes
                if node.source is not None and node.source.source_type == ORIGINAL_IMAGE_TYPE
            ]
            assert len(originals) == 2 and all(node.role == "lineage_only" for node in originals)
            assert len(resolved.projection.evidence) == 3
            assert all(task["result"]["artifact"]["revision"] >= 1 for task in (one, two, three))
            assert all(
                item.root_group_ids == (resolved.projection.root_groups[0].group_id,)
                for item in resolved.projection.evidence
            )
            assert all(node.source in authorized for node in originals)
            assert all("asset_id" not in item.text for item in resolved.projection.evidence)

    asyncio.run(scenario())


def test_unreadable_analysis_is_audit_only_for_automatic_and_explicit_generation(tmp_path):
    async def scenario():
        output = VISIBLE | {
            "readability": "unreadable",
            "ocr_text": "",
            "visual_description": "",
            "uncertainties": ["Blurred"],
        }
        backend = Backend({"content": json.dumps(output)})
        generator = MemoryGenerator()
        async with (
            _listen(backend.app) as endpoint,
            _running(
                tmp_path,
                endpoint + "/v1",
                candidate_pipeline=atomic_memory_pipeline(generator),
                handoff_pipeline=LLMHandoffGenerationPipeline(HandoffGenerator(), evidence_projector=TextProjector()),
            ) as (_, http),
        ):
            scope = await _scope(http)
            captured = await _capture(http, _request(scope, "unreadable", _picture()))
            task = await _terminal(http, scope, captured["task_id"])
            assert task["state"] == "succeeded" and task["analysis_payload"]["output"]["readability"] == "unreadable"
            async with PowerContextClient("http://localhost", http_client=http) as client:
                flush = await client.flush_memory(FlushMemoryRequest(scope_id=scope))
                assert flush.current_cursor > captured["sequence"] and flush.processed_source_count == 0
            assert not generator.inputs
            request = {
                "scope_id": scope,
                "objective": "Check the image",
                "evidence": [
                    {
                        "kind": "source",
                        "source_ref": {
                            "name": IMAGE_ANALYSIS_TYPE,
                            "source_id": task["analysis"]["source"]["source_id"],
                        },
                    }
                ],
            }
            response = await http.post("/v1/handoff/prepare", json=request)
            assert response.status_code == 422, response.text

    asyncio.run(scenario())


class TopicGenerator:
    def __init__(self, output):
        self.output = output
        self.inputs = []

    async def generate(self, value, /):
        self.inputs.append(value)
        return GenerationResult(output=self.output)


def test_topic_memory_advances_original_window_and_consumes_notified_late_analysis(tmp_path):
    async def scenario():
        backend = Backend(paused=True)
        async with _listen(backend.app) as endpoint, _running(tmp_path, endpoint + "/v1") as (_, http):
            scope = await _scope(http)
            captured = await _capture(http, _request(scope, "topic-late", _picture()))
            await asyncio.wait_for(backend.entered.wait(), 30)
            config = BuiltinConfig(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'images.db'}"),
                runtime=RuntimeConfig(artifact_processing_families=("image-analysis",)),
            )
            async with open_builtin_contexts(config) as contexts:
                repos = contexts.repositories
                async with contexts.database.transaction() as connection:
                    term = await repos.processing_leases.start_single_process_term(
                        connection, "image-topic-test", supervisor_group="artifact:topic-memory"
                    )
                probe = TopicGenerator(
                    TopicMemoryProbeOutput(probes=(TopicMemoryProbe(query="E429", evidence_ids=("evidence-0001",)),))
                )
                global_evolver = TopicGenerator(
                    TopicMemoryGlobalOutput(
                        proposals=(
                            TopicMemoryProposal(
                                content=TopicMemoryContent(
                                    title="IMG-034 E429",
                                    summary="截图显示连接失败",
                                    detail="# IMG-034\n\n错误码 E429,根因未确认。",
                                ),
                                evidence_ids=("evidence-0001",),
                            ),
                        )
                    )
                )
                unused = TopicGenerator(None)
                processor = TopicMemoryProcessor(
                    database=contexts.database,
                    sources=repos.sources,
                    topics=repos.topic_memories,
                    stages=TopicMemoryStageSet(
                        probe=probe,
                        global_evolver=global_evolver,
                        planner=unused,
                        evolver=unused,
                        temporary=unused,
                        reconciler=unused,
                        estimator=contexts.token_estimator,
                        input_tokens_limit=100000,
                    ),
                    publisher=TopicMemoryAtomicPublisher(
                        contexts.database, repos.sources, repos.topic_memories, leases=repos.processing_leases
                    ),
                    id_factory=lambda: "image-topic",
                )

                async def process(after, through):
                    async with contexts.database.transaction() as connection:
                        pending = await repos.processing_pending.request_flush(
                            connection, scope, TOPIC_MEMORY_SOURCE_WINDOW_BINDING
                        )
                        cursor = await repos.cursors.load(connection, scope, TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                    assert pending is not None
                    return await processor.process(
                        TopicMemoryWindowAssignment(
                            binding_name=TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
                            scope_id=scope,
                            source_after=after,
                            source_through=through,
                            wave_target=through,
                            claimed_flush_generation=pending.flush_generation,
                            cursor_generation=None if cursor is None else cursor.generation,
                            wave_kind=ArtifactProcessingWaveKind.EXPLICIT,
                            fence=term.fence("single-process"),
                            worker_id="image-worker-test",
                        )
                    )

                assert (await process(0, captured["sequence"])).outcome.value == "succeeded"
                assert not probe.inputs
                backend.release.set()
                task = await _terminal(http, scope, captured["task_id"])
                async with contexts.database.transaction() as connection:
                    pending = await repos.processing_pending.load(connection, scope, TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                assert pending is not None and pending.source_through > captured["sequence"]
                assert (await process(captured["sequence"], pending.source_through)).outcome.value == "succeeded"
                assert probe.inputs[0].evidence[0].source_type == IMAGE_ANALYSIS_TYPE
                assert (
                    "E429" in probe.inputs[0].evidence[0].content
                    and "base_url" not in probe.inputs[0].evidence[0].content
                )
                async with contexts.database.transaction() as connection:
                    topic = await repos.artifacts.latest(connection, scope, "topic-memory", "image-topic")
                    assert topic.lineage.sources == (SourceRef.model_validate(task["analysis"]["source"]),)
                    assert topic.content.title == "IMG-034 E429"

    asyncio.run(scenario())
