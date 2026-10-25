# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Immutable, statement-specific ordering support for exact Memory content."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.sources import SourceRef
from powercontext.sources.evidence import EvidenceAnchor

MAX_ORDERING_BYTES = 32_768
MAX_ORDERING_STATEMENTS = 32
MAX_STATEMENT_SUPPORTS = 16
MAX_ORDERING_NODES = 128


def memory_content_digest(kind: str, text: str) -> str:
    encoded = json.dumps({"kind": kind, "text": text}, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class OrderingValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class StatementEvidence(OrderingValue):
    """An exact supporting input, with either a verified origin or unknown basis."""

    source: SourceRef | None = None
    artifact: ArtifactRef | None = None
    via_artifact: ArtifactRef | None = None
    anchor: EvidenceAnchor | None = None

    @model_validator(mode="after")
    def named_input(self):
        if self.source is None and self.artifact is None:
            raise ValueError("statement evidence requires an exact input")  # noqa: TRY003
        return self


class MemoryStatementSupport(OrderingValue):
    """Exact retained text and applicability, associated with its own evidence."""

    statement: str = Field(min_length=1, max_length=8192)
    applicability: str | None = Field(default=None, max_length=1024)
    effective_date: date | None = None
    evidence: tuple[StatementEvidence, ...] = Field(default=(), max_length=MAX_STATEMENT_SUPPORTS)


class MemoryOrderingSupport(OrderingValue):
    schema_: Literal["powercontext.memory-ordering.v1"] = Field(
        default="powercontext.memory-ordering.v1", alias="schema"
    )
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    statements: tuple[MemoryStatementSupport, ...] = Field(min_length=1, max_length=MAX_ORDERING_STATEMENTS)

    @model_validator(mode="after")
    def bounded(self):
        if len(self.model_dump_json(by_alias=True).encode("utf-8")) > MAX_ORDERING_BYTES:
            raise ValueError("Memory ordering support exceeds 32 KiB")  # noqa: TRY003
        return self

    def normalized(self, kind, text):
        """Bind new-write canonicalization while retaining each exact evidence association."""
        from powercontext.builtin.artifacts.memory.canonical import normalize_text

        value = self.model_copy(
            update={
                "content_digest": memory_content_digest(kind, text),
                "statements": tuple(
                    statement.model_copy(update={"statement": normalize_text(statement.statement)})
                    for statement in self.statements
                ),
            }
        )
        value.validate_content(kind, text)
        return value

    def validate_content(self, kind: str, text: str) -> None:
        if self.content_digest != memory_content_digest(kind, text):
            raise ValueError("Memory ordering support does not match its exact content")  # noqa: TRY003
        covered = [False] * len(text)
        for item in self.statements:
            if item.statement not in text:
                raise ValueError("statement support must name retained text verbatim")  # noqa: TRY003
            start = text.index(item.statement)
            covered[start : start + len(item.statement)] = [True] * len(item.statement)
        if any(character.isalnum() and not covered[index] for index, character in enumerate(text)):
            raise ValueError("ordering support must account for all retained statements")  # noqa: TRY003


def unknown_memory_ordering(kind: str, text: str) -> MemoryOrderingSupport:
    """Manual or historical content receives no fabricated Source anchor."""
    return MemoryOrderingSupport(
        content_digest=memory_content_digest(kind, text),
        statements=(MemoryStatementSupport(statement=text),),
    )


class MemoryStatementSelection(OrderingValue):
    """The model selects supplied support IDs; it never constructs an anchor."""

    statement: str = Field(min_length=1, max_length=8192)
    applicability: str | None = Field(default=None, max_length=1024)
    effective_date: date | None = None
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=MAX_STATEMENT_SUPPORTS)


class MemoryStatementComparison(OrderingValue):
    item_id: str
    statement_index: int = Field(ge=0, lt=MAX_ORDERING_STATEMENTS)
    relation: Literal["independent", "same_fact"]
    incoming_statement: str = Field(min_length=1, max_length=8192)
    incoming_evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=MAX_STATEMENT_SUPPORTS)
    incoming_effective_date: date | None = None


def can_replace_statement(incoming: MemoryStatementSupport, existing: MemoryStatementSupport) -> bool:
    """Compare only this statement's support, never a whole-lineage extremum."""
    if incoming.effective_date is not None and existing.effective_date is not None:
        return incoming.effective_date >= existing.effective_date
    if (incoming.effective_date is None) != (existing.effective_date is None):
        return False
    if not incoming.evidence or not existing.evidence:
        return False
    for old in existing.evidence:
        for new in incoming.evidence:
            if old.anchor is None or new.anchor is None:
                return False
            if old.anchor.recorded_at is not None and new.anchor.recorded_at is not None:
                if new.anchor.recorded_at < old.anchor.recorded_at:
                    return False
            elif (
                old.anchor.scope_id != new.anchor.scope_id or new.anchor.journal_position < old.anchor.journal_position
            ):
                return False
    return True


async def resolve_memory_ordering(connection, scope_id, artifact, sources, *, authorize_source=None):
    """Recover only one unambiguous exact legacy input; never traverse current heads or re-extract."""
    if artifact.content.ordering is not None:
        return artifact.content.ordering
    unknown = unknown_memory_ordering(artifact.content.kind, artifact.content.text)
    if artifact.lineage.artifacts or len(artifact.lineage.sources) != 1:
        return unknown
    ref = artifact.lineage.sources[0]
    if authorize_source is not None and not await authorize_source(ref):
        return unknown
    from powercontext.builtin.persistence.errors import RepositoryNotFoundError
    from powercontext.builtin.sources.content import ContentSource

    try:
        stored = await sources.get(connection, scope_id, ref)
    except RepositoryNotFoundError:
        return unknown
    value = stored.value
    if (
        not isinstance(value, ContentSource)
        or value.internal is not None
        or value.working_note
        or len(value.content.encode("utf-8")) > 32_768
        or value.content != artifact.content.text
    ):
        return unknown
    anchor = EvidenceAnchor(scope_id=scope_id, source=stored.ref, journal_position=stored.journal_position)
    return MemoryOrderingSupport(
        content_digest=memory_content_digest(artifact.content.kind, artifact.content.text),
        statements=(
            MemoryStatementSupport(
                statement=artifact.content.text, evidence=(StatementEvidence(source=stored.ref, anchor=anchor),)
            ),
        ),
    )


def _invalid_ordering(reason):
    raise ValueError(reason)


def _support_references(supports, scope_id):
    source_refs = {}
    artifact_refs = {}
    for support in supports:
        if support.anchor is not None:
            if support.source is None or support.anchor.scope_id != scope_id or support.anchor.recorded_at is not None:
                _invalid_ordering("ordering anchor is outside the validated provenance contract")
            ref = support.anchor.source
            source_refs[(ref.source_type, ref.source_id)] = ref
        if support.source is not None:
            ref = support.source
            source_refs[(ref.source_type, ref.source_id)] = ref
        for ref in (support.artifact, support.via_artifact):
            if ref is not None:
                artifact_refs[(ref.family, ref.artifact_id, ref.revision)] = ref
    if len(source_refs) + len(artifact_refs) > MAX_ORDERING_NODES:
        _invalid_ordering("ordering validation exceeds its exact-reference budget")
    return tuple(source_refs.values()), tuple(artifact_refs.values())


def _validate_support_lineage(support, lineage):
    if support.via_artifact is not None:
        if support.via_artifact not in lineage.artifacts:
            _invalid_ordering("inherited statement support requires its exact Memory in direct lineage")
    elif (support.source is not None and support.source not in lineage.sources) or (
        support.artifact is not None and support.artifact not in lineage.artifacts
    ):
        _invalid_ordering("statement support requires its exact input in direct lineage")


def _validate_source_support(support, scope_id, stored_sources, stored_artifacts):
    from powercontext.builtin.images.models import ImageInterpretationSource, interpretation_snapshot

    anchor = support.anchor
    if anchor is None:
        return
    original = stored_sources[(anchor.source.source_type, anchor.source.source_id)]
    if original.journal_position != anchor.journal_position:
        _invalid_ordering("ordering anchor does not match the retained Source")
    stored = stored_sources[(support.source.source_type, support.source.source_id)]
    if not isinstance(stored.value, ImageInterpretationSource):
        if stored.ref != anchor.source:
            _invalid_ordering("ordinary Source support cannot choose another Source's order")
        return
    source = stored.value
    ref = source.result.artifact
    exact = stored_artifacts.get((ref.family, ref.artifact_id, ref.revision))
    if (
        source.original.scope_id != scope_id
        or source.result.scope_id != scope_id
        or source.origin_anchor != anchor
        or support.artifact != ref
        or exact is None
        or exact.content.origin_anchor != anchor
        or exact.content.original != source.original
        or interpretation_snapshot(exact.content.output) != source.content
        or exact.content.output.readability.value != source.readability
    ):
        _invalid_ordering("ordering support does not match its exact interpretation")


async def _validate_inherited_support(connection, scope_id, support, stored_artifacts, sources):
    ref = support.via_artifact
    if ref is None:
        return
    if ref.family != "atomic-memory":
        _invalid_ordering("statement support via Artifact requires an exact Memory revision")
    exact = stored_artifacts[(ref.family, ref.artifact_id, ref.revision)]
    basis = await resolve_memory_ordering(connection, scope_id, exact, sources)
    if not any(s.model_copy(update={"via_artifact": ref}) == support for old in basis.statements for s in old.evidence):
        _invalid_ordering("statement support was not retained by its exact Memory revision")


async def validate_memory_ordering(
    connection, scope_id, draft, sources, artifacts, *, authorize_sources=None, authorize_artifact=None
):
    """Validate bounded exact associations in the publication transaction using batched reads."""
    ordering = draft.content.ordering
    if ordering is None:
        return
    ordering.validate_content(draft.content.kind, draft.content.text)
    supports = tuple(support for statement in ordering.statements for support in statement.evidence)
    source_refs, artifact_refs = _support_references(supports, scope_id)
    for support in supports:
        _validate_support_lineage(support, draft)
    if authorize_sources is not None and source_refs:
        await authorize_sources(source_refs)
    if authorize_artifact is not None:
        for ref in artifact_refs:
            await authorize_artifact(ref)
    stored_sources = {
        (s.ref.source_type, s.ref.source_id): s for s in await sources.get_many(connection, scope_id, source_refs)
    }
    stored_artifacts = {
        (a.family, a.artifact_id, a.revision): a for a in await artifacts.get_many(connection, scope_id, artifact_refs)
    }
    for support in supports:
        _validate_source_support(support, scope_id, stored_sources, stored_artifacts)
        await _validate_inherited_support(connection, scope_id, support, stored_artifacts, sources)
