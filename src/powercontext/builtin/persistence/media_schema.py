# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Frozen media migration shape; existing deployments never run startup DDL."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Connection, create_engine, inspect
from sqlalchemy.ext.asyncio import AsyncEngine

from powercontext.builtin.persistence.migrations.models import MigrationError

MEDIA_RESOURCE = Path(__file__).parent / "migration_resources/media/schema.json"
MEDIA_OBJECTS = ("pc_media_assets", "pc_media_processing_requests", "pc_media_capacity")
_INITIAL_AUTH_TABLES = frozenset({
    "pc_access_relationship_heads",
    "pc_access_relationships",
    "pc_access_owners",
    "pc_access_idempotency",
    "pc_access_audit",
})


@lru_cache
def media_resource():
    return json.loads(MEDIA_RESOURCE.read_text(encoding="utf-8"))


def media_inventory(connection: Connection):
    if connection.dialect.name != "sqlite":
        raise MigrationError("unsupported_backend", "Media storage is qualified only for file SQLite.")
    rows = connection.exec_driver_sql(
        "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE "
        "substr(name,1,9)='pc_media_' OR substr(tbl_name,1,9)='pc_media_' ORDER BY type,name"
    )
    result = []
    for kind, name, table, sql in rows:
        if table not in MEDIA_OBJECTS or kind not in {"table", "index"}:
            raise MigrationError("incompatible_schema", "Unregistered objects occupy the media namespace.")
        result.append([kind, name, table, re.sub(r'[\s`"\[\]]', "", sql or "").lower()])
    return result


@lru_cache
def expected_media_inventory():
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            for statement in media_resource()["sqlite"]:
                connection.exec_driver_sql(statement)
            return media_inventory(connection)
    finally:
        engine.dispose()


def require_media_schema(connection: Connection):
    if media_inventory(connection) != expected_media_inventory():
        raise MigrationError("media_migration_required", "Run the registered media migration before enabling images.")


async def prepare_media_storage(engine: AsyncEngine) -> bool:
    """Initialize a new database only; return false for a stopped deployment needing maintenance."""
    async with engine.begin() as connection:
        names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        if any(name.startswith("pc_experimental_image_") for name in names):
            return False
        if names.intersection(MEDIA_OBJECTS):
            try:
                await connection.run_sync(require_media_schema)
            except MigrationError:
                return False
            else:
                return True
        # Server identity bootstraps before business storage on a new deployment.
        # Any business table still requires the registered offline migration.
        if names - _INITIAL_AUTH_TABLES:
            return False
        # New file initialization shares the same frozen DDL as offline maintenance.
        await connection.exec_driver_sql("BEGIN IMMEDIATE")
        for statement in media_resource()["sqlite"]:
            await connection.exec_driver_sql(statement)
        await connection.run_sync(require_media_schema)
        return True
