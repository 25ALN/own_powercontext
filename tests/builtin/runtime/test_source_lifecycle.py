from pathlib import Path

import pytest

from powercontext.artifacts import ArtifactRef, MemoryCitation
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.runtime.source_lifecycle import (
    SourceLifecycleApplication,
    SourceLifecycleError,
    SourceLifecycleEvent,
)
from powercontext.sources import SourceRef


def test_source_lifecycle_propagates_and_reopens(tmp_path: Path) -> None:
    import asyncio

    asyncio.run(_test_source_lifecycle_propagates_and_reopens(tmp_path))


async def _test_source_lifecycle_propagates_and_reopens(tmp_path: Path) -> None:
    config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'lifecycle.db'}")
    async with SQLiteProfile.open(config, tables=()) as profile:
        from powercontext.builtin.persistence.source_lifecycle import LIFECYCLE_TABLES

        async with profile.database.transaction() as connection:
            await connection.run_sync(lambda sync: LIFECYCLE_TABLES[0].metadata.create_all(sync))
        service = SourceLifecycleApplication(profile.database).for_scope("scope")
        await service.register_source("s", SourceRef(source_type="content", source_id="s"), revision=1)
        citation = MemoryCitation(
            memory_ref=ArtifactRef(family="memory", artifact_id="memory", revision=1),
            entry_id="e",
            entry_version_id="v",
        )
        await service.register_memory("m", citation, parents=("s",), policy="all")
        await service.register_topic(
            "t", ArtifactRef(family="topic-memory", artifact_id="t", revision=1), parents=("m",), policy="any"
        )
        assert await service.active_ids() == ["m", "s", "t"]
        event = SourceLifecycleEvent(node_id="s", reason="withdrawn", revision=1, sequence=1, available=False)
        assert await service.apply(event) == []
        assert await service.resolve("m", purpose="audit") == citation
        with pytest.raises(SourceLifecycleError):
            await service.resolve("m", purpose="generation")
        assert await service.apply(event) == []
        with pytest.raises(SourceLifecycleError):
            await service.apply(event.model_copy(update={"reason": "different"}))
        assert await service.apply(
            SourceLifecycleEvent(node_id="s", reason="restored", revision=1, sequence=2, available=True)
        ) == ["m", "s", "t"]


def test_source_lifecycle_event_is_strict() -> None:
    with pytest.raises(ValueError):
        SourceLifecycleEvent(node_id=" ", reason="x", revision=1, sequence=1, available=True)
    with pytest.raises(ValueError):
        SourceLifecycleEvent(node_id="s", reason="x", revision=0, sequence=1, available=True)
    with pytest.raises(ValueError):
        SourceLifecycleEvent(node_id="s", reason="x", revision=1, sequence=1, available=True, extra="no")
