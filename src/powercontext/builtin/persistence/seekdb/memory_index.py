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

"""Single-statement hybrid Memory candidate search for seekdb."""

from __future__ import annotations

from pyobvector import VECTOR
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.memory_index import memory_channel_hits
from powercontext.builtin.persistence.oceanbase.memory_index import OceanBaseMemoryVectorIndex
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql

_HYBRID_SEARCH_SQL = """
WITH fts_candidates AS (
    SELECT
        h.memory_artifact_id,
        h.head_revision,
        h.entry_id,
        h.entry_version_id,
        v.text,
        MATCH (h.searchable_text) AGAINST (:analyzed_query) AS score,
        CAST(NULL AS DOUBLE) AS distance
    FROM pc_memory_entry_heads AS h
    JOIN pc_memory_entry_versions AS v
      ON v.scope_id = h.scope_id
     AND v.memory_artifact_id = h.memory_artifact_id
     AND v.entry_version_id = h.entry_version_id
    WHERE h.scope_id = :scope_id
      AND h.memory_artifact_id IN :memory_artifact_ids
      AND MATCH (h.searchable_text) AGAINST (:analyzed_query)
      {fts_tag_filter}
    ORDER BY score DESC, h.memory_artifact_id, h.entry_id, h.entry_version_id
    LIMIT :candidate_limit
), vector_candidates AS (
    SELECT
        m.memory_artifact_id,
        m.head_revision,
        m.entry_id,
        m.entry_version_id,
        v.text,
        CAST(NULL AS DOUBLE) AS score,
        l2_distance(m.embedding, :query_vector) AS distance
    FROM pc_memory_vector_entries AS m
    JOIN pc_memory_entry_versions AS v
      ON v.scope_id = m.scope_id
     AND v.memory_artifact_id = m.memory_artifact_id
     AND v.entry_version_id = m.entry_version_id
    WHERE m.scope_id = :scope_id
      AND m.memory_artifact_id IN :memory_artifact_ids
      {vector_tag_filter}
    ORDER BY l2_distance(m.embedding, :query_vector) APPROXIMATE
    LIMIT :candidate_limit
)
SELECT 'fts' AS channel, memory_artifact_id, head_revision, entry_id, entry_version_id, text, score, distance
FROM fts_candidates
UNION ALL
SELECT 'vector' AS channel, memory_artifact_id, head_revision, entry_id, entry_version_id, text, score, distance
FROM vector_candidates
ORDER BY channel, score DESC, distance ASC, memory_artifact_id, entry_id, entry_version_id
"""


class SeekDBMemoryHybridSearch:
    """Read FTS and vector candidate rankings in one seekdb statement."""

    def __init__(self, vector_index: OceanBaseMemoryVectorIndex) -> None:
        self._vector_index = vector_index
        self._tagged_sql = self._statement(tagged=True)
        self._sql = self._statement(tagged=False)

    def _statement(self, *, tagged: bool):
        statement = text(
            _HYBRID_SEARCH_SQL.format(
                fts_tag_filter=memory_tag_sql("h") if tagged else "",
                vector_tag_filter=memory_tag_sql("m") if tagged else "",
            )
        ).bindparams(
            bindparam("memory_artifact_ids", expanding=True),
            bindparam("query_vector", type_=VECTOR(self._vector_index.profile.dimension)),
        )
        if tagged:
            statement = statement.bindparams(
                bindparam("tag_keys", expanding=True),
                bindparam("tag_hashes", expanding=True),
            )
        return statement

    async def __call__(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.embedding_profile != self._vector_index.profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        if not await self._vector_index.vector_complete(
            connection, scope_id, request.memories, self._vector_index.profile
        ):
            raise CapabilityNotSupportedError("vector")

        result = await connection.execute(
            self._sql if request.tag_filter is None else self._tagged_sql,
            {
                "scope_id": scope_id,
                "memory_artifact_ids": tuple(memory.artifact_id for memory in request.memories),
                "analyzed_query": request.analyzed_query,
                "query_vector": validate_embedding(
                    request.query_vector,
                    dimension=self._vector_index.profile.dimension,
                ),
                "candidate_limit": request.candidate_limit,
                **memory_tag_parameters(request.tag_filter),
            },
        )
        fts_rows: list[dict[str, object]] = []
        vector_rows: list[dict[str, object]] = []
        for row in result.mappings():
            target = fts_rows if row["channel"] == "fts" else vector_rows
            target.append(dict(row))
        return MemorySearchChannels(
            fts=memory_channel_hits(fts_rows, request.memories),
            vector=memory_channel_hits(vector_rows, request.memories),
        )


__all__ = ["SeekDBMemoryHybridSearch"]
