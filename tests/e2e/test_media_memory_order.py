# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Late interpretation and reparse preserve current facts across actual persisted Memory windows."""

import asyncio
import re

import pytest

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.ordering import MemoryStatementComparison, MemoryStatementSelection
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.images.identity import image_profile_digest
from powercontext.builtin.inference import GenerationResult
from powercontext.builtin.inference.tokens import character_token_estimator
from powercontext.builtin.media.models import ImageAnalysisRequest
from powercontext.builtin.runtime import CaptureSource, open_builtin_runtime

from .test_media_processing import vision_endpoint, wait_request
from .test_media_storage import capture_input, config_for, images_for, profile_for, scope_for


class FactExtractor:
    async def generate(self, value):
        candidates = []
        for item in value.evidence:
            match = re.search(r"Deployment limit is [0-9]+[.]", str(item.content))
            if match:
                candidates.append(AtomicMemoryCandidate(kind="fact", text=match[0], evidence_ids=(item.evidence_id,)))
        return GenerationResult(output=AtomicMemoryExtractionOutput(candidates=tuple(candidates)))


class SameFactReconciler:
    """Deliberately asks to make every incoming value current; runtime must enforce ordering."""

    async def generate(self, request):
        proposal = request.proposal
        related = request.related
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="create" if not related else "revise" if related[0].original_refs else "merge",
                compared_ids=tuple(item.item_id for item in related),
                target_ids=tuple(item.item_id for item in related),
                content=AtomicMemoryReconciliationContent(kind=proposal.kind, text=proposal.text),
                evidence_ids=proposal.evidence_ids,
                statements=(MemoryStatementSelection(statement=proposal.text, evidence_ids=proposal.evidence_ids),),
                statement_comparisons=tuple(
                    MemoryStatementComparison(
                        item_id=item.item_id,
                        statement_index=index,
                        relation="same_fact",
                        incoming_statement=proposal.text,
                        incoming_evidence_ids=proposal.evidence_ids,
                    )
                    for item in related
                    for index in range(len(item.ordering.statements) if item.ordering else 1)
                ),
                reason="The incoming candidate describes the same deployment limit.",
            )
        )


@pytest.mark.parametrize("same_window", [False, True])
def test_late_image_and_reparse_do_not_replace_newer_text(tmp_path, same_window):
    with vision_endpoint(hold_first=True) as (endpoint, calls, release):

        async def scenario():
            config = config_for(tmp_path / "order.db")
            config.runtime.artifact_processing_families = ("image-analysis",)
            config.inference.vision_base_url = endpoint
            pipeline = AtomicMemoryGenerationPipeline(
                extractor=FactExtractor(), reconciler=SameFactReconciler(), estimator=character_token_estimator()
            )
            async with open_builtin_runtime(config, candidate_pipeline=pipeline) as runtime:
                assert images_for(runtime) is not None and runtime.atomic_memory is not None
                scope = (await scope_for(runtime)).scope_id
                accepted = await images_for(runtime).capture(capture_input(scope))
                await runtime.sources.for_scope(scope).capture(
                    CaptureSource(source_id="newer-text", content="Deployment limit is 200.", metadata={})
                )
                memory = runtime.atomic_memory.for_scope(scope)
                if not same_window:
                    await memory.flush()
                release.set()
                await wait_request(runtime, scope, accepted.request_id)
                await memory.flush()
                records = (await memory.list()).items
                assert len(records) == 1 and records[0].artifact.content.text == "Deployment limit is 200."
                ordering = records[0].artifact.content.ordering
                assert ordering is not None
                assert ordering.statements[0].evidence[0].anchor.source.source_id == "newer-text"
                initial = records[0].ref
                replay = await images_for(runtime).request_analysis(
                    ImageAnalysisRequest(
                        scope_id=scope,
                        operation="reparse",
                        idempotency_key="reparse-old-picture",
                        original=accepted.original.source,
                        expected_artifact_revision=1,
                        expected_profile_digest=image_profile_digest(profile_for(runtime)),
                    )
                )
                second = await wait_request(runtime, scope, replay.request_id)
                assert second.result.artifact.revision == 2
                await memory.flush()
                kept = (await memory.list()).items
                assert len(kept) == 1 and kept[0].ref == initial
                assert kept[0].artifact.content.ordering == ordering
                publication = await runtime.records.for_scope(scope).get_source(
                    second.publication.source.source_type, second.publication.source.source_id
                )
                assert (await memory.cursor()).sequence == publication.position
                assert len(calls) == 2
            # Reopening with the processor disabled must preserve the exact ordering payload.
            config.images.enabled = False
            async with open_builtin_runtime(config, candidate_pipeline=pipeline) as runtime:
                assert runtime.atomic_memory is not None
                kept = (await runtime.atomic_memory.for_scope(scope).list()).items
                assert kept[0].ref == initial and kept[0].artifact.content.ordering == ordering

        asyncio.run(scenario())


def test_ordering_survives_portable_copy_and_corrupt_saved_anchors_fail_before_reconciliation(tmp_path):
    import json
    import sqlite3

    from powercontext.builtin.persistence.errors import InvalidStoredPayloadError

    class RecordingReconciler(SameFactReconciler):
        def __init__(self):
            self.calls = 0

        async def generate(self, request):
            self.calls += 1
            return await super().generate(request)

    async def scenario():
        reconciler = RecordingReconciler()
        pipeline = AtomicMemoryGenerationPipeline(
            extractor=FactExtractor(), reconciler=reconciler, estimator=character_token_estimator()
        )
        config = config_for(tmp_path / "source.db", enabled=False)
        async with open_builtin_runtime(config, candidate_pipeline=pipeline) as runtime:
            scope = (await scope_for(runtime)).scope_id
            await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="original-text", content="Deployment limit is 200.", metadata={})
            )
            assert runtime.atomic_memory is not None and runtime.archive is not None
            memory = runtime.atomic_memory.for_scope(scope)
            await memory.flush()
            saved = (await memory.list()).items[0].artifact
            archive = tmp_path / "ordering.pcb"

            async def authorize(scopes):
                assert scopes == (scope,)

            await runtime.archive.export([scope], archive, authorize=authorize)
        async with open_builtin_runtime(
            config_for(tmp_path / "copy.db", enabled=False), candidate_pipeline=pipeline
        ) as runtime:
            assert runtime.archive is not None and runtime.atomic_memory is not None
            await runtime.archive.restore(archive)
            memory = runtime.atomic_memory.for_scope(scope)
            copied = await memory.get(saved.artifact_id)
            assert copied.artifact.content.ordering == saved.content.ordering
            assert copied.artifact.lineage == saved.lineage
            cursor_before = (await memory.cursor()).sequence
            await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="new-text", content="Deployment limit is 300.", metadata={})
            )
            with sqlite3.connect(tmp_path / "copy.db") as db:
                row = db.execute("SELECT content FROM pc_artifacts WHERE family='atomic-memory'").fetchone()
                value = json.loads(row[0])
                value["ordering"]["statements"][0]["evidence"][0]["anchor"]["journal_position"] = 999
                db.execute(
                    "UPDATE pc_artifacts SET content=? WHERE family='atomic-memory'",
                    (json.dumps(value, ensure_ascii=False).encode(),),
                )
            before = reconciler.calls
            with pytest.raises(InvalidStoredPayloadError, match="ordering support"):
                await memory.flush()
            assert reconciler.calls == before
            assert (await memory.cursor()).sequence == cursor_before
            from powercontext.builtin.portability import BundleFormatError

            output = tmp_path / "corrupt.pcb"
            output.write_bytes(b"prior complete backup")
            assert runtime.archive is not None
            with pytest.raises(BundleFormatError, match="ordering anchor"):
                await runtime.archive.export([scope], output, authorize=authorize)
            assert output.read_bytes() == b"prior complete backup"

    asyncio.run(scenario())


def test_first_memory_retains_distinct_statement_support_without_related_items(tmp_path):
    class MixedExtractor:
        async def generate(self, value):
            ids = tuple(item.evidence_id for item in value.evidence)
            assert len(ids) == 2
            return GenerationResult(
                output=AtomicMemoryExtractionOutput(
                    candidates=(
                        AtomicMemoryCandidate(
                            kind="fact",
                            text="Port is 3306. Limit is 200.",
                            evidence_ids=ids,
                            statements=(
                                MemoryStatementSelection(statement="Port is 3306.", evidence_ids=(ids[0],)),
                                MemoryStatementSelection(statement="Limit is 200.", evidence_ids=(ids[1],)),
                            ),
                        ),
                    )
                )
            )

    class KeepMixedReconciler:
        async def generate(self, request):
            ids = request.proposal.evidence_ids
            return GenerationResult(
                output=AtomicMemoryReconciliationOutput(
                    action="create",
                    compared_ids=(),
                    content=AtomicMemoryReconciliationContent(kind=request.proposal.kind, text=request.proposal.text),
                    evidence_ids=ids,
                    statements=(
                        MemoryStatementSelection(statement="Port is 3306.", evidence_ids=(ids[0],)),
                        MemoryStatementSelection(statement="Limit is 200.", evidence_ids=(ids[1],)),
                    ),
                    reason="Keep the two supported clauses.",
                )
            )

    async def scenario():
        pipeline = AtomicMemoryGenerationPipeline(
            extractor=MixedExtractor(), reconciler=KeepMixedReconciler(), estimator=character_token_estimator()
        )
        async with open_builtin_runtime(
            config_for(tmp_path / "mixed.db", enabled=False), candidate_pipeline=pipeline
        ) as runtime:
            scope = (await scope_for(runtime)).scope_id
            for index, body in enumerate(("Port is 3306.", "Limit is 200.")):
                await runtime.sources.for_scope(scope).capture(
                    CaptureSource(source_id=f"input-{index}", content=body, metadata={})
                )
            assert runtime.atomic_memory is not None
            memory = runtime.atomic_memory.for_scope(scope)
            await memory.flush()
            record = (await memory.list()).items[0]
            support = record.artifact.content.ordering
            assert support is not None
            assert [(s.statement, s.evidence[0].anchor.journal_position) for s in support.statements] == [
                ("Port is 3306.", 1),
                ("Limit is 200.", 2),
            ]

    asyncio.run(scenario())
