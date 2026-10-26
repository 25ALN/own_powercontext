"""Dependency driven Source, Memory, and Topic Memory lifecycle."""

from __future__ import annotations

import json
from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, field_validator
from sqlalchemy import and_, insert, select, update

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError, StoredPayloadConflictError
from powercontext.builtin.persistence.source_lifecycle import SOURCE_LIFECYCLE_EVENTS, SOURCE_LIFECYCLE_NODES
from powercontext.builtin.sources import validate_scope_id
from powercontext.sources import SourceRef


class SourceLifecycleError(ValueError):
    """Raised when a lifecycle graph or event violates its contract."""


class SourceLifecycleEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: StrictStr
    reason: StrictStr
    revision: StrictInt
    sequence: StrictInt
    available: StrictBool

    @field_validator("node_id", "reason")
    @classmethod
    def _text(cls, value: str) -> str:
        if not value.strip():
            raise SourceLifecycleError("node_id and reason must be non-empty")  # noqa: TRY003
        return value

    @field_validator("revision", "sequence")
    @classmethod
    def _positive(cls, value: int) -> int:
        if value <= 0:
            raise SourceLifecycleError("revision and sequence must be positive")  # noqa: TRY003
        return value


class ScopedSourceLifecycle:
    def __init__(
        self, database: AsyncDatabase, scope_id: str, *, sources=None, artifacts=None, memory_service=None
    ) -> None:
        self._database = database
        self.scope_id = validate_scope_id(scope_id)
        self._sources = sources
        self._artifacts = artifacts
        self._memory_service = memory_service

    async def _memory(self):
        if self._memory_service is not None and not hasattr(self._memory_service, "revision"):
            context = await self._memory_service.get(self.scope_id)
            return context.artifacts.memory
        return self._memory_service

    @staticmethod
    def _raise_not_found(message: str, error: Exception) -> None:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise error
        raise SourceLifecycleError(message) from error

    async def register_source(self, node_id: str, source_ref: SourceRef, *, revision: int) -> None:
        if self._sources is not None:
            try:
                await self._sources.get_ref(source_ref)
            except RepositoryNotFoundError as error:
                self._raise_not_found("Source reference is not readable in this scope", error)
        if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
            raise SourceLifecycleError("Source lifecycle revision must be a positive integer")  # noqa: TRY003
        await self._register(node_id, "source", source_ref.model_dump_json().encode(), (), "all", revision, True)

    async def register_memory(
        self, node_id: str, citation: MemoryCitation, *, parents: tuple[str, ...], policy: Literal["all", "any"]
    ) -> None:
        memory_service = await self._memory()
        if memory_service is not None:
            try:
                await memory_service.revision(citation.memory_ref)
                await memory_service.validate_citation(citation)
            except (LookupError, StoredPayloadConflictError) as error:
                self._raise_not_found("Memory citation is not readable", error)
        await self._register(node_id, "memory", citation.model_dump_json().encode(), parents, policy, None, None)
        if memory_service is not None:
            row = await self._node(node_id)
            if row is not None and not row["available"]:
                memory = await memory_service.revision(citation.memory_ref)
                entry = await memory_service.validate_citation(citation)
                await memory_service.forget(memory, entries=(entry,), reason="Source lifecycle withdrawn")

    async def register_topic(
        self, node_id: str, artifact_ref: ArtifactRef, *, parents: tuple[str, ...], policy: Literal["all", "any"]
    ) -> None:
        if artifact_ref.family != "topic-memory":
            raise SourceLifecycleError("Topic Memory reference must use topic-memory family")  # noqa: TRY003
        if self._artifacts is not None:
            try:
                await self._artifacts.get_ref(artifact_ref)
            except RepositoryNotFoundError as error:
                self._raise_not_found("Topic Memory reference is not readable in this scope", error)
        await self._register(node_id, "topic", artifact_ref.model_dump_json().encode(), parents, policy, None, None)

    async def _register(
        self,
        node_id: str,
        kind: str,
        payload: bytes,
        parents: tuple[str, ...],
        policy: str,
        revision: int | None,
        available: bool | None,
    ) -> None:
        if (
            not isinstance(node_id, str)
            or not node_id.strip()
            or policy not in ("all", "any")
            or not isinstance(parents, tuple)
            or len(set(parents)) != len(parents)
            or (kind != "source" and not parents)
        ):
            raise SourceLifecycleError("invalid lifecycle registration")  # noqa: TRY003
        async with self._database.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES).where(SOURCE_LIFECYCLE_NODES.c.scope_id == self.scope_id)
                    )
                )
                .mappings()
                .all()
            )
            by_id: dict[str, Any] = {row["node_id"]: row for row in rows}
            existing = by_id.get(node_id)
            if existing:
                if (
                    existing["kind"] == kind
                    and bytes(existing["ref_payload"]) == payload
                    and tuple(json.loads(bytes(existing["parents_payload"]))) == parents
                    and existing["policy"] == policy
                ):
                    return
                raise SourceLifecycleError("node identity is already bound")  # noqa: TRY003
            if any(parent not in by_id for parent in parents):
                raise SourceLifecycleError("parent must be registered first")  # noqa: TRY003
            if node_id in parents or self._would_cycle(node_id, parents, by_id):
                raise SourceLifecycleError("lifecycle graph must be acyclic")  # noqa: TRY003
            state = True if kind == "source" else self._derive(parents, policy, by_id)
            await connection.execute(
                insert(SOURCE_LIFECYCLE_NODES).values(
                    scope_id=self.scope_id,
                    node_id=node_id,
                    kind=kind,
                    ref_payload=payload,
                    parents_payload=json.dumps(parents).encode(),
                    policy=policy,
                    available=state,
                    revision=revision,
                    sequence=0,
                )
            )

    @staticmethod
    def _would_cycle(node_id: str, parents: tuple[str, ...], by_id: dict[str, Any]) -> bool:
        seen = set()
        todo = list(parents)
        while todo:
            current = todo.pop()
            if current == node_id:
                return True
            if current in seen:
                continue
            seen.add(current)
            row = by_id.get(current)
            if row:
                todo.extend(json.loads(bytes(row["parents_payload"])))
        return False

    @staticmethod
    def _derive(parents: tuple[str, ...], policy: str, by_id: dict[str, Any]) -> bool:
        values = [bool(by_id[parent]["available"]) for parent in parents]
        return all(values) if policy == "all" else any(values)

    async def apply(self, event: SourceLifecycleEvent) -> list[str]:
        async with self._database.connection() as connection:
            rows = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES).where(SOURCE_LIFECYCLE_NODES.c.scope_id == self.scope_id)
                    )
                )
                .mappings()
                .all()
            )
            by_id: dict[str, Any] = {row["node_id"]: row for row in rows}
            source = by_id.get(event.node_id)
            if source is None or source["kind"] != "source" or source["revision"] != event.revision:
                raise SourceLifecycleError("event must target a registered Source revision")  # noqa: TRY003
            prior = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_EVENTS).where(
                            and_(
                                SOURCE_LIFECYCLE_EVENTS.c.scope_id == self.scope_id,
                                SOURCE_LIFECYCLE_EVENTS.c.node_id == event.node_id,
                                SOURCE_LIFECYCLE_EVENTS.c.sequence == event.sequence,
                            )
                        )
                    )
                )
                .mappings()
                .first()
            )
            if prior and (prior["revision"], prior["available"], prior["reason"]) != (
                event.revision,
                event.available,
                event.reason,
            ):
                raise SourceLifecycleError("same sequence has different event content")  # noqa: TRY003
            if not prior and event.sequence <= int(source["sequence"] or 0):
                raise SourceLifecycleError("event sequence must increase")  # noqa: TRY003
        if prior:
            await self._synchronize_memory_states()
            return await self.active_ids()
        async with self._database.transaction() as connection:
            rows = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES).where(SOURCE_LIFECYCLE_NODES.c.scope_id == self.scope_id)
                    )
                )
                .mappings()
                .all()
            )
            by_id = {row["node_id"]: row for row in rows}
            source = by_id.get(event.node_id)
            if source is None or source["kind"] != "source" or source["revision"] != event.revision:
                raise SourceLifecycleError("event must target a registered Source revision")  # noqa: TRY003
            prior = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_EVENTS).where(
                            and_(
                                SOURCE_LIFECYCLE_EVENTS.c.scope_id == self.scope_id,
                                SOURCE_LIFECYCLE_EVENTS.c.node_id == event.node_id,
                                SOURCE_LIFECYCLE_EVENTS.c.sequence == event.sequence,
                            )
                        )
                    )
                )
                .mappings()
                .first()
            )
            if prior:
                raise SourceLifecycleError("event was concurrently applied")  # noqa: TRY003
            await connection.execute(
                insert(SOURCE_LIFECYCLE_EVENTS).values(
                    scope_id=self.scope_id,
                    node_id=event.node_id,
                    sequence=event.sequence,
                    revision=event.revision,
                    available=event.available,
                    reason=event.reason,
                )
            )
            await connection.execute(
                update(SOURCE_LIFECYCLE_NODES)
                .where(
                    and_(
                        SOURCE_LIFECYCLE_NODES.c.scope_id == self.scope_id,
                        SOURCE_LIFECYCLE_NODES.c.node_id == event.node_id,
                    )
                )
                .values(available=event.available, sequence=event.sequence)
            )
            source_state = dict(source)
            source_state["available"] = event.available
            by_id[event.node_id] = source_state
            await self._propagate(connection, by_id, event.node_id)
            rows = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES).where(SOURCE_LIFECYCLE_NODES.c.scope_id == self.scope_id)
                    )
                )
                .mappings()
                .all()
            )
            active = sorted(row["node_id"] for row in rows if row["available"])
        await self._synchronize_memory_states()
        return active

    async def _synchronize_memory_states(self) -> None:
        memory_service = await self._memory()
        if memory_service is None:
            return
        async with self._database.connection() as connection:
            rows = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES).where(
                            and_(
                                SOURCE_LIFECYCLE_NODES.c.scope_id == self.scope_id,
                                SOURCE_LIFECYCLE_NODES.c.kind == "memory",
                            )
                        )
                    )
                )
                .mappings()
                .all()
            )
        grouped: dict[tuple[str, str, int], list[tuple[MemoryCitation, bool]]] = {}
        for row in rows:
            citation = MemoryCitation.model_validate(json.loads(bytes(row["ref_payload"])))
            key = (citation.memory_ref.family, citation.memory_ref.artifact_id, citation.memory_ref.revision)
            grouped.setdefault(key, []).append((citation, bool(row["available"])))
        for bindings in grouped.values():
            memory_ref = bindings[0][0].memory_ref
            memory = await memory_service.revision(memory_ref)
            current = await memory_service.latest(memory)
            desired: dict[str, bool] = {}
            for citation, available in bindings:
                if citation.entry_id in desired and desired[citation.entry_id] != available:
                    raise SourceLifecycleError("one Memory entry has conflicting lifecycle bindings")  # noqa: TRY003
                desired[citation.entry_id] = available
            manifest = {item.entry_id: item for item in current.content.manifest.entries}
            changing = [
                entry_id
                for entry_id, available in desired.items()
                if entry_id in manifest and (manifest[entry_id].state == "active") != available
            ]
            if not changing:
                continue
            citations = {citation.entry_id: citation for citation, _available in bindings}
            resolved_entries = []
            for entry_id in changing:
                resolved_entries.append(await memory_service.validate_citation(citations[entry_id]))
            entries = tuple(resolved_entries)
            if all(desired[entry_id] for entry_id in changing):
                await memory_service.reactivate(current, entries=entries, reason="Source lifecycle restored")
            else:
                await memory_service.forget(current, entries=entries, reason="Source lifecycle withdrawn")

    async def _propagate(self, connection, by_id: dict[str, Any], changed: str) -> None:
        states = {node_id: bool(row["available"]) for node_id, row in by_id.items()}
        children: dict[str, list[str]] = {node_id: [] for node_id in by_id}
        for node_id, row in by_id.items():
            for parent in json.loads(bytes(row["parents_payload"])):
                children[parent].append(node_id)
        pending = deque(children[changed])
        visited: set[str] = set()
        while pending:
            node_id = pending.popleft()
            if node_id in visited:
                continue
            visited.add(node_id)
            row = by_id[node_id]
            parents = tuple(json.loads(bytes(row["parents_payload"])))
            values = [states[parent] for parent in parents]
            state = all(values) if row["policy"] == "all" else any(values)
            if states[node_id] != state:
                states[node_id] = state
                await connection.execute(
                    update(SOURCE_LIFECYCLE_NODES)
                    .where(
                        and_(
                            SOURCE_LIFECYCLE_NODES.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES.c.node_id == node_id,
                        )
                    )
                    .values(available=state)
                )
            pending.extend(children[node_id])

    async def active_ids(self) -> list[str]:
        async with self._database.connection() as connection:
            rows = (
                await connection.execute(
                    select(SOURCE_LIFECYCLE_NODES.c.node_id).where(
                        and_(
                            SOURCE_LIFECYCLE_NODES.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES.c.available.is_(True),
                        )
                    )
                )
            ).all()
            return sorted(row[0] for row in rows)

    async def _node(self, node_id: str):
        async with self._database.connection() as connection:
            return (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES).where(
                            and_(
                                SOURCE_LIFECYCLE_NODES.c.scope_id == self.scope_id,
                                SOURCE_LIFECYCLE_NODES.c.node_id == node_id,
                            )
                        )
                    )
                )
                .mappings()
                .first()
            )

    async def is_memory_active(self, entry_id: str) -> bool:
        async with self._database.connection() as connection:
            node_rows = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES).where(
                            and_(
                                SOURCE_LIFECYCLE_NODES.c.scope_id == self.scope_id,
                                SOURCE_LIFECYCLE_NODES.c.kind == "memory",
                            )
                        )
                    )
                )
                .mappings()
                .all()
            )
        for row in node_rows:
            citation = MemoryCitation.model_validate(json.loads(bytes(row["ref_payload"])))
            if citation.entry_id == entry_id:
                return bool(row["available"])
        return True

    async def active_topic_refs(self) -> set[tuple[str, str, int]]:
        async with self._database.connection() as connection:
            rows = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES).where(
                            and_(
                                SOURCE_LIFECYCLE_NODES.c.scope_id == self.scope_id,
                                SOURCE_LIFECYCLE_NODES.c.kind == "topic",
                                SOURCE_LIFECYCLE_NODES.c.available.is_(True),
                            )
                        )
                    )
                )
                .mappings()
                .all()
            )
        refs = (ArtifactRef.model_validate(json.loads(bytes(row["ref_payload"]))) for row in rows)
        return {(ref.family, ref.artifact_id, ref.revision) for ref in refs}

    async def has_topic_nodes(self) -> bool:
        async with self._database.connection() as connection:
            return bool(
                await connection.scalar(
                    select(SOURCE_LIFECYCLE_NODES.c.node_id)
                    .where(
                        and_(
                            SOURCE_LIFECYCLE_NODES.c.scope_id == self.scope_id,
                            SOURCE_LIFECYCLE_NODES.c.kind == "topic",
                        )
                    )
                    .limit(1)
                )
            )

    async def resolve(self, node_id: str, *, purpose: Literal["generation", "audit"]):
        if purpose not in ("generation", "audit"):
            raise SourceLifecycleError("unknown resolve purpose")  # noqa: TRY003
        async with self._database.connection() as connection:
            row = (
                (
                    await connection.execute(
                        select(SOURCE_LIFECYCLE_NODES).where(
                            and_(
                                SOURCE_LIFECYCLE_NODES.c.scope_id == self.scope_id,
                                SOURCE_LIFECYCLE_NODES.c.node_id == node_id,
                            )
                        )
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            raise SourceLifecycleError("unknown lifecycle node")  # noqa: TRY003
        if purpose == "generation" and not row["available"]:
            raise SourceLifecycleError("node is unavailable for generation")  # noqa: TRY003
        payload = json.loads(bytes(row["ref_payload"]))
        if row["kind"] == "source":
            return SourceRef.model_validate(payload)
        if row["kind"] == "memory":
            return MemoryCitation.model_validate(payload)
        return ArtifactRef.model_validate(payload)


class SourceLifecycleApplication:
    def __init__(
        self, database: AsyncDatabase, *, source_repository=None, artifact_repository=None, memory_factory=None
    ) -> None:
        self._database = database
        self._source_repository = source_repository
        self._artifact_repository = artifact_repository
        self._memory_factory = memory_factory

    def for_scope(self, scope_id: str) -> ScopedSourceLifecycle:
        memory_service = self._memory_factory
        sources = (
            None
            if self._source_repository is None
            else _ScopeSourceReader(self._database, scope_id, self._source_repository)
        )
        artifacts = (
            None
            if self._artifact_repository is None
            else _ScopeArtifactReader(self._database, scope_id, self._artifact_repository)
        )
        return ScopedSourceLifecycle(
            self._database, scope_id, sources=sources, artifacts=artifacts, memory_service=memory_service
        )


class _ScopeSourceReader:
    def __init__(self, database, scope_id, repository):
        self.database, self.scope_id, self.repository = database, scope_id, repository

    async def get_ref(self, ref):
        async with self.database.transaction() as connection:
            return await self.repository.get(connection, self.scope_id, ref)


class _ScopeArtifactReader:
    def __init__(self, database, scope_id, repository):
        self.database, self.scope_id, self.repository = database, scope_id, repository

    async def get_ref(self, ref):
        async with self.database.transaction() as connection:
            return await self.repository.get(connection, self.scope_id, ref)
