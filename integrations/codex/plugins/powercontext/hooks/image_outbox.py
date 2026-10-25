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

"""Durable client delivery state; server capture keys survive crashes and retries."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from time import time
from typing import Any
from urllib.parse import urlencode

from image_capture_config import codex_home

MAX_PENDING_BYTES = 128 * 1024 * 1024
MAX_JOBS = 128
MAX_RECEIPTS = 512


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def outbox_path() -> Path:
    return codex_home() / "powercontext" / "image-capture" / "outbox.sqlite3"


class Outbox:
    """Short SQLite transactions serialize cursor, staged bytes and upload receipts."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path = path
        with self.connect() as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {0, 2}:
                raise ValueError("image_outbox_unsupported")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, payload TEXT NOT NULL, cursor INTEGER NOT NULL,
                    state TEXT NOT NULL DEFAULT 'watching', error TEXT, reader TEXT NOT NULL DEFAULT '{}',
                    created REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS attachments (
                    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, request TEXT NOT NULL, input_digest TEXT NOT NULL, bytes BLOB,
                    state TEXT NOT NULL DEFAULT 'pending', task_id TEXT, error TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                    created REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS attachment_delivery ON attachments(state, next_attempt);
                CREATE TABLE IF NOT EXISTS watches (
                    path TEXT PRIMARY KEY, payload TEXT NOT NULL, cursor INTEGER NOT NULL,
                    skip_partial INTEGER NOT NULL DEFAULT 0
                );
                PRAGMA user_version=2;
            """)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=0.5)
        try:
            connection.row_factory = sqlite3.Row
            # DELETE journal avoids retaining uploaded private blobs in WAL files.
            connection.execute("PRAGMA secure_delete=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def register(self, payload: dict[str, Any], cursor: int | Callable[[], int]) -> str:
        identity = digest([payload["host_instance_id"], payload["session_id"], payload["turn_id"]])
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT payload FROM jobs WHERE id=?", (identity,)).fetchone()
            if previous:
                saved = json.loads(previous[0])
                if any(
                    saved.get(k) != payload.get(k)
                    for k in ("scope_id", "server_url", "file_identity", "workspace", "epoch")
                ):
                    raise ValueError("image_capture_conflict")
                connection.execute("UPDATE jobs SET state='watching' WHERE id=? AND state='idle'", (identity,))
                return identity
            # Make room against the total budget, including live readers and
            # undelivered jobs. Only completed work is eligible for eviction.
            count = connection.execute("SELECT count(*) FROM jobs").fetchone()[0]
            connection.execute(
                """
                DELETE FROM jobs WHERE id IN (
                    SELECT id FROM jobs WHERE
                        (state IN ('done','idle') OR (state='error' AND coalesce(error,'')!='image_outbox_full'))
                    AND NOT EXISTS (
                        SELECT 1 FROM attachments WHERE job_id=jobs.id AND state!='accepted'
                    ) ORDER BY created LIMIT ?
                )
            """,
                (max(0, count - MAX_JOBS + 1),),
            )
            if connection.execute("SELECT count(*) FROM jobs").fetchone()[0] >= MAX_JOBS:
                raise ValueError("image_outbox_full")
            connection.execute(
                "INSERT INTO jobs(id,payload,cursor,created) VALUES(?,?,?,?)",
                (identity, json.dumps(payload), cursor if isinstance(cursor, int) else cursor(), time()),
            )
        return identity

    def jobs(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM jobs WHERE state='watching' OR "
                    "(state='error' AND error='image_outbox_full') ORDER BY created"
                )
            ]

    def advance(
        self,
        job_id: str,
        cursor: int,
        images: list[tuple[dict[str, Any], bytes]],
        *,
        reader: dict[str, Any],
        state: str = "watching",
        error: str | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            size = connection.execute("SELECT coalesce(sum(length(bytes)),0) FROM attachments").fetchone()[0]
            for request, data in images:
                identity = digest([job_id, request["capture_key"], request["origin"]["attachment_index"]])
                existing = connection.execute(
                    "SELECT request,input_digest FROM attachments WHERE id=?", (identity,)
                ).fetchone()
                encoded = json.dumps(request, sort_keys=True, separators=(",", ":"))
                fingerprint = hashlib.sha256(data).hexdigest()
                if existing:
                    if existing[0] != encoded or existing[1] != fingerprint:
                        raise ValueError("image_capture_conflict")
                    continue
                if size + len(data) > MAX_PENDING_BYTES:
                    raise ValueError("image_outbox_full")
                size += len(data)
                connection.execute(
                    "INSERT INTO attachments(id,job_id,request,input_digest,bytes,created) VALUES(?,?,?,?,?,?)",
                    (identity, job_id, encoded, fingerprint, data, time()),
                )
            connection.execute(
                "UPDATE jobs SET cursor=?,state=?,error=?,reader=? WHERE id=?",
                (cursor, state, error, json.dumps(reader), job_id),
            )

    def finish(self, job_id: str, *, state: str, error: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute("UPDATE jobs SET state=?,error=? WHERE id=?", (state, error, job_id))

    def pending(self, *, permitted: Callable[[dict[str, Any]], bool] | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            query = (
                "SELECT attachments.*,jobs.payload AS job FROM attachments JOIN jobs ON jobs.id=job_id "
                "WHERE attachments.state='pending' AND next_attempt<=? "
            )
            arguments: list[Any] = [time()]
            if permitted is not None:
                # Job metadata is bounded by MAX_JOBS. Filter before paging blobs
                # so a disabled workspace cannot occupy every delivery slot.
                eligible = []
                for row in connection.execute("SELECT id,payload FROM jobs"):
                    try:
                        if permitted(json.loads(row["payload"])):
                            eligible.append(row["id"])
                    except (ValueError, TypeError, KeyError, OSError):
                        continue
                if not eligible:
                    return []
                query += "AND job_id IN (" + ",".join("?" for _ in eligible) + ") "
                arguments.extend(eligible)
            return [
                dict(row)
                for row in connection.execute(
                    query + "ORDER BY attachments.created LIMIT 8",
                    arguments,
                )
            ]

    def delivered(self, identity: str, task_id: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE attachments SET state='accepted',task_id=?,bytes=NULL,error=NULL WHERE id=?",
                (task_id, identity),
            )
            connection.execute(
                """
                DELETE FROM attachments WHERE id IN (
                    SELECT id FROM attachments WHERE state='accepted' ORDER BY created DESC LIMIT -1 OFFSET ?
                )
            """,
                (MAX_RECEIPTS,),
            )

    def retry(self, identity: str, *, error: str, permanent: bool = False) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE attachments SET attempts=attempts+1,error=?,state=?,next_attempt=? WHERE id=?",
                (error, "error" if permanent else "pending", time() + 5, identity),
            )

    def status(self, workspace: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT * FROM jobs ORDER BY created DESC")
            result = []
            for row in rows:
                payload = json.loads(row["payload"])
                if payload["workspace"] != workspace:
                    continue
                result.append({
                    "turn_id": payload["turn_id"],
                    "state": row["state"],
                    "error": row["error"],
                    "attachments": [
                        {
                            **{key: item[key] for key in ("state", "task_id", "error", "attempts")},
                            "dashboard_url": (
                                payload["server_url"]
                                + "/dashboard/image-task/"
                                + item["task_id"]
                                + "?"
                                + urlencode({"scope": payload["scope_id"], "lang": "zh"})
                            )
                            if item["task_id"]
                            else None,
                        }
                        for item in connection.execute(
                            "SELECT * FROM attachments WHERE job_id=? ORDER BY created", (row["id"],)
                        )
                    ],
                })
            return result

    def reset_delivery(self, workspace: str) -> None:
        """Explicit recovery after fixing an endpoint, credential or service config."""
        with self.connect() as connection:
            for row in connection.execute("SELECT id,payload FROM jobs"):
                if json.loads(row["payload"])["workspace"] == workspace:
                    connection.execute(
                        "UPDATE attachments SET state='pending',next_attempt=0,error=NULL "
                        "WHERE job_id=? AND bytes IS NOT NULL",
                        (row["id"],),
                    )

    def enroll(self, payload: dict[str, Any], cursor: int, *, skip_partial: bool) -> None:
        path = payload["transcript_path"]
        with self.connect() as connection:
            previous = connection.execute("SELECT payload FROM watches WHERE path=?", (path,)).fetchone()
            if previous:
                saved = json.loads(previous[0])
                # Creation time is derived host metadata added to existing v2
                # watches. Learning it must not reset the accepted live cursor.
                if {k: v for k, v in saved.items() if k != "created_at"} == {
                    k: v for k, v in payload.items() if k != "created_at"
                }:
                    connection.execute("UPDATE watches SET payload=? WHERE path=?", (json.dumps(payload), path))
                    return
            if not previous and connection.execute("SELECT count(*) FROM watches").fetchone()[0] >= MAX_JOBS:
                raise ValueError("image_watcher_limit")
            connection.execute(
                "INSERT INTO watches(path,payload,cursor,skip_partial) VALUES(?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET payload=excluded.payload,cursor=excluded.cursor,skip_partial=excluded.skip_partial",
                (path, json.dumps(payload), cursor, int(skip_partial)),
            )

    def watches(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute("SELECT * FROM watches")]

    def retire_watch(self, path: str) -> None:
        """Forget a removed transcript without discarding staged deliveries."""
        with self.connect() as connection:
            connection.execute("DELETE FROM watches WHERE path=?", (path,))

    def advance_watch(self, path: str, cursor: int, *, skip_partial: bool = False) -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE watches SET cursor=?,skip_partial=? WHERE path=?", (cursor, int(skip_partial), path)
            )
