# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Statement-specific ordering, conservative unknowns and retained content integrity."""

from datetime import date

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.ordering import (
    MemoryOrderingSupport,
    MemoryStatementSupport,
    StatementEvidence,
    can_replace_statement,
    memory_content_digest,
)
from powercontext.sources import SourceRef
from powercontext.sources.evidence import EvidenceAnchor


def supported(text, position, *, effective_date=None, scope="scope"):
    ref = SourceRef(source_type="content", source_id=f"source-{position}")
    return MemoryStatementSupport(
        statement=text,
        effective_date=effective_date,
        evidence=(
            StatementEvidence(source=ref, anchor=EvidenceAnchor(scope_id=scope, source=ref, journal_position=position)),
        ),
    )


def test_mixed_fact_support_compares_each_statement_instead_of_aggregate_lineage():
    old = supported("Port is 3306.", 5)
    current = supported("Limit is 200.", 20)
    incoming = supported("Limit is 100.", 10)
    content = AtomicMemoryContent(
        kind="fact",
        text=old.statement + " " + current.statement,
        ordering=MemoryOrderingSupport(
            content_digest=memory_content_digest("fact", old.statement + " " + current.statement),
            statements=(old, current),
        ),
    )
    assert content.ordering is not None
    anchor = content.ordering.statements[0].evidence[0].anchor
    assert anchor is not None and anchor.journal_position == 5
    assert can_replace_statement(incoming, old)
    assert not can_replace_statement(incoming, current)
    # A recent unrelated input cannot make an older supporting input current.
    mixed = MemoryStatementSupport(statement=incoming.statement, evidence=(*incoming.evidence, *current.evidence))
    assert not can_replace_statement(mixed, current)


@pytest.mark.parametrize(
    "old,new",
    [
        (MemoryStatementSupport(statement="Manual current."), supported("Late old.", 10)),
        (supported("Known current.", 20), MemoryStatementSupport(statement="Unknown incoming.")),
        (supported("Other scope.", 10, scope="elsewhere"), supported("Incomparable.", 100)),
    ],
)
def test_unknown_or_incomparable_support_never_authorizes_current_replacement(old, new):
    assert not can_replace_statement(new, old)


def test_explicit_fact_dates_precede_source_order():
    existing = supported("Limit is 200 from 2026-10-20.", 20, effective_date=date(2026, 10, 20))
    old = supported("Limit is 100 from 2026-10-01.", 30, effective_date=date(2026, 10, 1))
    future = supported("Limit is 300 from 2026-11-01.", 10, effective_date=date(2026, 11, 1))
    assert not can_replace_statement(old, existing)
    assert can_replace_statement(future, existing)


@pytest.mark.parametrize(
    "change",
    [
        {"content_digest": "sha256:" + "0" * 64},
        {"schema": "powercontext.memory-ordering.v9"},
        {"statements": [{"statement": "Only a fragment."}]},
    ],
)
def test_corrupt_or_unsupported_support_is_not_downgraded_to_unknown(change):
    text = "The retained current statement."
    ordering = {
        "schema": "powercontext.memory-ordering.v1",
        "content_digest": memory_content_digest("fact", text),
        "statements": [{"statement": text}],
    }
    with pytest.raises(ValidationError):
        AtomicMemoryContent.model_validate({"kind": "fact", "text": text, "ordering": ordering | change})


def test_legacy_absence_stays_absent_and_exact_serialization_retains_support():
    assert AtomicMemoryContent(kind="fact", text="Manual.").model_dump(mode="json").get("ordering") is None
    statement = supported("Current.", 20)
    value = AtomicMemoryContent(
        kind="fact",
        text=statement.statement,
        ordering=MemoryOrderingSupport(
            content_digest=memory_content_digest("fact", statement.statement), statements=(statement,)
        ),
    )
    assert AtomicMemoryContent.model_validate_json(value.model_dump_json()).ordering == value.ordering


def test_new_write_normalization_rebinds_digest_and_statement_without_changing_support():
    text = "  Café is open.  "
    statement = supported(text, 10)
    ordering = MemoryOrderingSupport(content_digest=memory_content_digest("fact", text), statements=(statement,))
    value = ordering.normalized("fact", "Café is open.")
    value.validate_content("fact", "Café is open.")
    assert value.statements[0].statement == "Café is open."
    assert value.statements[0].evidence == statement.evidence
    assert ordering.statements[0].statement == text


def test_late_mixed_input_can_publish_independent_clause_without_a_conflicting_copy():
    from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence
    from powercontext.builtin.artifacts.atomic_memory.ordering import (
        MemoryStatementComparison,
        MemoryStatementSelection,
    )
    from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
        AtomicMemoryReconciliationContent,
        AtomicMemoryReconciliationOutput,
        AtomicMemoryWindowWorkset,
        AtomicMemoryWorkingItem,
    )

    old = supported("Limit is 100.", 10)
    current = supported("Limit is 200.", 20)
    evidence = AtomicMemoryEvidence(
        evidence_id="source:30",
        source_ref=old.evidence[0].source,
        journal_position=30,
        content="Limit is 100. Port is 3306.",
        source_metadata={},
        derived=True,
        origin_anchor=old.evidence[0].anchor,
    )
    workset = AtomicMemoryWindowWorkset()
    workset.add(
        AtomicMemoryWorkingItem(
            key="current",
            content=AtomicMemoryContent(
                kind="fact",
                text=current.statement,
                ordering=MemoryOrderingSupport(
                    content_digest=memory_content_digest("fact", current.statement), statements=(current,)
                ),
            ),
        )
    )
    workset.add(
        AtomicMemoryWorkingItem(
            key="late",
            content=AtomicMemoryContent(kind="fact", text=str(evidence.content)),
            evidence=(evidence,),
            changed=True,
        )
    )
    related = workset.related("late", ("current",))
    output = AtomicMemoryReconciliationOutput(
        action="create",
        compared_ids=("current",),
        content=AtomicMemoryReconciliationContent(kind="fact", text="Port is 3306."),
        evidence_ids=(evidence.evidence_id,),
        statements=(MemoryStatementSelection(statement="Port is 3306.", evidence_ids=(evidence.evidence_id,)),),
        statement_comparisons=(
            MemoryStatementComparison(
                item_id="current",
                statement_index=0,
                relation="same_fact",
                incoming_statement=old.statement,
                incoming_evidence_ids=(evidence.evidence_id,),
            ),
        ),
        reason="Retain current limit; publish independent port.",
    )
    workset.apply("late", related, output)
    assert workset.items["current"].content.text == "Limit is 200."
    assert [item.content.text for item in workset.changes()] == ["Port is 3306."]
