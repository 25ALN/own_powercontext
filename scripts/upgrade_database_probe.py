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

"""Inspect a disposable database using the installed release's own driver.

Run only after the Server has exited. --add-revision creates a deliberately
incomplete control table for a negative fixture; it never represents a migration.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

REVISION_DDL = "CREATE TABLE pc_schema_revision (version_num VARCHAR(32) PRIMARY KEY)"


async def inspect(backend: str, database: Path, *, add_revision: bool) -> dict[str, Any]:
    if backend == "sqlite":
        with sqlite3.connect(database) as connection:
            if add_revision:
                connection.execute(REVISION_DDL)
            objects = {
                plural: dict(
                    connection.execute("SELECT name, sql FROM sqlite_schema WHERE type=? ORDER BY name", (kind,))
                )
                for kind, plural in (
                    ("table", "tables"),
                    ("index", "indexes"),
                    ("view", "views"),
                    ("trigger", "triggers"),
                )
            }
        return {"engine_version": sqlite3.sqlite_version, "driver_version": version("aiosqlite"), **objects}

    # Use the driver's public API, not a current-checkout Profile that could run
    # startup guards or mutate an old schema before the target CLI is tested.
    pymysql = import_module("pymysql")
    instance = None
    details = {}
    if backend == "seekdb":
        binding = import_module("pylibseekdb")
        instance = await binding.aopen(str(database))
        options = {**instance.connection_options(), "database": "test"}
        details["embedded_binding_version"] = version("pylibseekdb")
    else:
        url = urlsplit(os.environ["PC_UPGRADE_OCEANBASE_URL"])
        options = {
            "host": url.hostname,
            "port": url.port or 2881,
            "user": unquote(url.username or ""),
            "password": unquote(url.password or ""),
            "database": url.path.removeprefix("/"),
            "charset": "utf8mb4",
            "connect_timeout": 15,
        }
    try:
        with pymysql.connect(**options, autocommit=True) as connection, connection.cursor() as cursor:
            if add_revision:
                cursor.execute(REVISION_DDL)
            cursor.execute("SELECT VERSION()")
            engine = str(cursor.fetchone()[0])
            if backend == "oceanbase":
                cursor.execute("SHOW VARIABLES LIKE 'ob_compatibility_mode'")
                details["tenant_mode"] = str(cursor.fetchone()[1]).lower()
                details["proxy_version"] = "direct"
            cursor.execute("SHOW FULL TABLES WHERE Table_type = 'BASE TABLE'")
            names = sorted(row[0] for row in cursor.fetchall())
            tables = {}
            for name in names:
                cursor.execute("SHOW CREATE TABLE `" + name.replace("`", "``") + "`")
                tables[name] = cursor.fetchone()[1]
            cursor.execute("SHOW FULL TABLES WHERE Table_type = 'VIEW'")
            views = {}
            for name in sorted(row[0] for row in cursor.fetchall()):
                cursor.execute("SHOW CREATE VIEW `" + name.replace("`", "``") + "`")
                views[name] = cursor.fetchone()[1]
        return {
            "engine_version": engine,
            "driver_version": version("pymysql"),
            **details,
            "tables": tables,
            "views": views,
        }
    finally:
        if instance is not None:
            instance.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("sqlite", "seekdb", "oceanbase"), required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--add-revision", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(inspect(args.backend, args.database, add_revision=args.add_revision))
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
