# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Measure native deduplicated PNG admission with ledger vs transactional aggregates.

Historical stress assets are synthetic, integrity-valid BLOBs, not decoder/provider fixtures.
The admitted inputs are real PNGs. This script never starts a model worker.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import platform
import sqlite3
from contextvars import ContextVar
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from statistics import median
from time import perf_counter

from PIL import Image
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import OperationalError
from typing_extensions import override

from powercontext.builtin.images.config import ImageConfig
from powercontext.builtin.media.errors import MediaError
from powercontext.builtin.media.models import ImageCaptureInput, ImageTechnicalMetadata, MediaAsset
from powercontext.builtin.persistence.media import MediaRepository, media_json
from powercontext.builtin.persistence.media_tables import MEDIA_ASSETS, MEDIA_REQUESTS
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import SOURCE_JOURNAL_HEADS_TABLE
from powercontext.builtin.runtime import BuiltinConfig, InferenceConfig, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft


@dataclass
class Timing:
    began: float = field(default_factory=perf_counter)
    lock_attempt: float | None = None
    acquired: float | None = None
    sql: int = 0


_TIMING: ContextVar[Timing | None] = ContextVar("media_benchmark_timing", default=None)


class Ledger(MediaRepository):
    @override
    async def lock_capacity(self, connection, scope_id):
        timing = _TIMING.get()
        first = timing is not None and timing.lock_attempt is None
        if first:
            timing.lock_attempt = perf_counter()
        result = await super().lock_capacity(connection, scope_id)
        if first and timing is not None:
            timing.acquired = perf_counter()
        return result


class Aggregates(MediaRepository):
    @override
    async def lock_capacity(self, connection, scope_id):
        timing = _TIMING.get()
        if timing is not None:
            timing.lock_attempt = perf_counter()
        # A preseeded Source journal head reserves SQLite's writer before any read.
        # This uses no capacity row; all SQLite writers serialize at the same DB boundary.
        await connection.execute(
            update(SOURCE_JOURNAL_HEADS_TABLE)
            .where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope_id)
            .values(position=SOURCE_JOURNAL_HEADS_TABLE.c.position)
        )
        if timing is not None:
            timing.acquired = perf_counter()
        return {}

    @override
    async def charge(
        self, connection, scope_id, *, retained_bytes, active_requests, max_bytes, max_requests, max_scope_requests
    ):
        used = await connection.scalar(select(func.coalesce(func.sum(MEDIA_ASSETS.c.byte_size), 0)))
        count = select(func.count()).select_from(MEDIA_REQUESTS).where(MEDIA_REQUESTS.c.active_key == "active")
        total = await connection.scalar(count)
        scoped = await connection.scalar(count.where(MEDIA_REQUESTS.c.scope_id == scope_id))
        if used + retained_bytes > max_bytes:
            raise MediaError(503, "media_capacity_exceeded")
        if total + active_requests > max_requests or scoped + active_requests > max_scope_requests:
            raise MediaError(503, "media_queue_full")


def png():
    stream = BytesIO()
    Image.new("RGB", (8, 8), "red").save(stream, format="PNG")
    return stream.getvalue()


def config(path):
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path.as_posix()}"),
        images=ImageConfig(enabled=True),
        runtime=RuntimeConfig(artifact_processing_families=()),
        inference=InferenceConfig(
            vision_model="benchmark", vision_base_url="http://127.0.0.1:9/v1", vision_deployment_id="benchmark"
        ),
    )


def command(scope, key):
    return ImageCaptureInput.model_validate({
        "scope_id": scope,
        "capture_key": key,
        "media_type": "image/png",
        "bytes_base64": base64.b64encode(png()).decode(),
        "origin": {"kind": "explicit_upload", "attachment_index": 0},
        "context": {"text": "benchmark"},
    })


async def seed(path, requests):
    scopes = []
    async with open_builtin_runtime(config(path)) as runtime:
        if runtime.scopes is None or runtime.images is None:
            raise RuntimeError("runtime-unavailable")
        for index in range(8):
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title=f"Benchmark {index}",
                    summary="Synthetic performance fixture",
                    idempotency_key=f"benchmark-{index}",
                )
            )
            scopes.append(scope.scope_id)
        for index in range(requests):
            await runtime.images.capture(command(scopes[index % 8], f"seed-{index}"))
    return scopes


def fill_history(path, scopes, total_bytes, blob_size):
    with sqlite3.connect(path) as db:
        used = db.execute("SELECT COALESCE(SUM(byte_size),0) FROM pc_media_assets").fetchone()[0]
        remaining = total_bytes - used
        rows = []
        index = 0
        while remaining > 0:
            size = min(blob_size, remaining)
            data = index.to_bytes(8, "little") + b"x" * (size - 8)
            digest = "sha256:" + hashlib.sha256(data).hexdigest()
            asset = MediaAsset(
                asset_id="ma_" + digest[7:],
                digest=digest,
                media_type="image/png",
                byte_size=len(data),
                metadata=ImageTechnicalMetadata(width=1, height=1),
            )
            rows.append((scopes[index % 8], digest, "image/png", len(data), media_json(asset), data, 0))
            remaining -= size
            index += 1
            if len(rows) == 256 or remaining == 0:
                db.executemany("INSERT INTO pc_media_assets VALUES (?,?,?,?,?,?,?)", rows)
                rows.clear()
        db.commit()
        return dict(
            db.execute(
                "SELECT 'assets',COUNT(*) FROM pc_media_assets UNION ALL SELECT 'requests',COUNT(*) FROM pc_media_processing_requests UNION ALL SELECT 'payload_bytes',SUM(byte_size) FROM pc_media_assets"
            )
        )


def clone(source, target):
    with sqlite3.connect(source) as original, sqlite3.connect(target) as copied:
        original.backup(copied)


def distribution(values):
    ordered = sorted(values)
    return {
        "p50": round(median(ordered), 3),
        "p95": round(ordered[int((len(ordered) - 1) * 0.95)], 3),
        "max": round(ordered[-1], 3),
    }


async def measure(path, scopes, mode, concurrency, samples):  # noqa: C901 - measured transaction and outcome accounting
    opened = perf_counter()
    async with open_builtin_runtime(config(path)) as runtime:
        if runtime.images is None:
            raise RuntimeError("images-unavailable")
        service = runtime.images
        restart_ms = (perf_counter() - opened) * 1000
        recovery = service.recovery_cost
        if recovery is None:
            raise RuntimeError("startup-reconciliation-unavailable")
        service.media = Ledger() if mode == "ledger" else Aggregates()

        def before_sql(*args):
            timing = _TIMING.get()
            if timing is not None:
                timing.sql += 1

        event.listen(service.database.engine.sync_engine, "before_cursor_execute", before_sql)
        semaphore = asyncio.Semaphore(concurrency)
        rows = []

        async def admit(index):
            async with semaphore:
                timing = Timing()
                token = _TIMING.set(timing)
                try:
                    await service.capture(command(scopes[index % 8], f"sample-{index}"))
                    status = "accepted"
                except MediaError as error:
                    status = error.code
                except OperationalError as error:
                    if "database is locked" not in str(error.orig):
                        raise
                    status = "database_locked"
                finally:
                    _TIMING.reset(token)
                end = perf_counter()
                if timing.lock_attempt is None:
                    raise RuntimeError("lock-timing-unavailable")
                acquired = timing.acquired or end
                rows.append({
                    "outcome": status,
                    "latency_ms": (end - timing.began) * 1000,
                    "lock_wait_ms": (acquired - timing.lock_attempt) * 1000,
                    "lock_hold_ms": (end - acquired) * 1000,
                    "sql_statements": timing.sql,
                })

        began = perf_counter()
        await asyncio.gather(*(admit(i) for i in range(samples)))
        elapsed_ms = (perf_counter() - began) * 1000
        measured_rows = list(rows)
        # A timed-out comparison admission did not commit. Complete those keys
        # sequentially outside the timed batch, then verify the same hard ceiling.
        repaired = 0
        for index, row in enumerate(rows):
            if row["outcome"] == "database_locked":
                await service.capture(command(scopes[index % 8], f"repair-{index}"))
                repaired += 1
        rows.clear()
        # Eight new keys must now be rejected at the fixed 1024-request ceiling.
        await asyncio.gather(*(admit(samples + i) for i in range(8)))
        event.remove(service.database.engine.sync_engine, "before_cursor_execute", before_sql)
    accepted = [r for r in measured_rows if r["outcome"] == "accepted"]
    failures = [r for r in measured_rows if r["outcome"] != "accepted"]
    rejected = [r for r in rows if r["outcome"] != "accepted"]
    with sqlite3.connect(path) as db:
        actual = db.execute(
            "SELECT COUNT(*),SUM(CASE WHEN active_key='active' THEN 1 ELSE 0 END) FROM pc_media_processing_requests"
        ).fetchone()
        plans = {
            name: [row[3] for row in db.execute("EXPLAIN QUERY PLAN " + sql, parameters)]
            for name, sql, parameters in [
                ("asset_sum", "SELECT SUM(byte_size) FROM pc_media_assets", ()),
                (
                    "pending_scopes",
                    "SELECT DISTINCT scope_id FROM pc_media_processing_requests WHERE processing_kind='image.interpretation' AND active_key='active' ORDER BY scope_id LIMIT 16",
                    (),
                ),
                (
                    "scope_active",
                    "SELECT MIN(due_at) FROM pc_media_processing_requests WHERE processing_kind='image.interpretation' AND active_key='active' AND scope_id=? AND state!='running'",
                    (scopes[0],),
                ),
                ("active_total", "SELECT COUNT(*) FROM pc_media_processing_requests WHERE active_key='active'", ()),
                (
                    "active_scope",
                    "SELECT COUNT(*) FROM pc_media_processing_requests WHERE active_key='active' AND scope_id=?",
                    (scopes[0],),
                ),
            ]
        }
    if len(accepted) + repaired != samples or len(rejected) != 8 or actual != (1024, 1024):
        raise RuntimeError("capacity-outcome-mismatch")
    return {
        "mode": mode,
        "concurrency": concurrency,
        "accepted": len(accepted),
        "rejected": len(rejected),
        "batch_failures": {
            code: sum(r["outcome"] == code for r in failures) for code in sorted({r["outcome"] for r in failures})
        },
        "sequential_repairs_outside_measurement": repaired,
        "failed_latency_ms": distribution([r["latency_ms"] for r in failures]) if failures else None,
        "logical_aggregate_inputs": {
            "asset_sum": recovery["assets"],
            "active_total": [1024 - samples, 1024],
            "active_scope": [(1024 - samples) // 8, 128],
        },
        "rejection_codes": sorted({r["outcome"] for r in rejected}),
        "final_request_rows": actual[0],
        "final_active_requests": actual[1],
        "admission_batch_ms": round(elapsed_ms, 3),
        "restart_with_ledger_reconciliation_ms": round(restart_ms, 3),
        "recovery": recovery,
        "accepted_metrics": {
            key: distribution([r[key] for r in accepted])
            for key in ("latency_ms", "lock_wait_ms", "lock_hold_ms", "sql_statements")
        },
        "rejected_latency_ms": distribution([r["latency_ms"] for r in rejected]),
        "aggregate_query_plans": plans,
    }


async def measure_new_blobs(directory):
    results = []
    for concurrency in (1, 4):
        async with open_builtin_runtime(config(directory / f"new-blobs-{concurrency}.db")) as runtime:
            if runtime.scopes is None or runtime.images is None:
                raise RuntimeError("runtime-unavailable")
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="New BLOBs", summary="Measured real PNG writes", idempotency_key="new-blobs")
                )
            ).scope_id
            service = runtime.images
            service.media = Ledger()
            inputs = []
            for index in range(8):
                stream = BytesIO()
                Image.frombytes("RGB", (1536, 1536), os.urandom(1536 * 1536 * 3)).save(stream, format="PNG")
                inputs.append(
                    command(scope, f"large-{index}").model_copy(
                        update={"bytes_base64": base64.b64encode(stream.getvalue()).decode()}
                    )
                )
            semaphore = asyncio.Semaphore(concurrency)
            rows = []

            async def capture(value, *, semaphore=semaphore, service=service, rows=rows):
                async with semaphore:
                    timing = Timing()
                    token = _TIMING.set(timing)
                    try:
                        receipt = await service.capture(value)
                    finally:
                        _TIMING.reset(token)
                    if timing.acquired is None or timing.lock_attempt is None:
                        raise RuntimeError("lock-timing-unavailable")
                    end = perf_counter()
                    rows.append({
                        "bytes": receipt.asset.byte_size,
                        "latency_ms": (end - timing.began) * 1000,
                        "lock_wait_ms": (timing.acquired - timing.lock_attempt) * 1000,
                        "lock_hold_ms": (end - timing.acquired) * 1000,
                    })

            await asyncio.gather(*(capture(value) for value in inputs))
            page = await service.list_requests(scope)
            if len(page.items) != 8:
                raise RuntimeError("new-blob-admission-mismatch")
            results.append({
                "mode": "ledger",
                "concurrency": concurrency,
                "accepted": len(rows),
                "png_dimensions": [1536, 1536],
                "payload_bytes": [min(r["bytes"] for r in rows), max(r["bytes"] for r in rows)],
                "metrics": {
                    key: distribution([r[key] for r in rows]) for key in ("latency_ms", "lock_wait_ms", "lock_hold_ms")
                },
            })
    return results


async def run(args):
    args.directory.mkdir(parents=True, exist_ok=False)
    result = {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
        },
        "method": "Real PNG capture and durable SQLite commit; historical BLOBs are integrity-only synthetic storage fixtures.",
        "limitations": [
            "Deduplicated history runs exclude new BLOB insertion; new_blob_admission separately measures eight real large PNGs, not provider/host execution.",
            "Lock hold is time from completed first-lock call through returned commit; wait includes that lock call's SQL.",
            "Query plans and logical aggregate input cardinalities do not measure physical disk I/O or exact VM row visits.",
            "Both modes use the production startup ledger integrity scan; no two-table startup claim is made.",
        ],
        "datasets": [],
    }
    for payload in args.payload_bytes:
        baseline = args.directory / f"seed-{payload}.db"
        scopes = await seed(baseline, 1024 - args.samples)
        counts = await asyncio.to_thread(fill_history, baseline, scopes, payload, args.blob_size)
        dataset = {"requested_payload_bytes": payload, "counts": counts, "runs": []}
        result["datasets"].append(dataset)
        for mode in ("ledger", "aggregates"):
            for concurrency in (1, 8):
                target = args.directory / f"{payload}-{mode}-{concurrency}.db"
                await asyncio.to_thread(clone, baseline, target)
                measured = await measure(target, scopes, mode, concurrency, args.samples)
                dataset["runs"].append(measured)
                print(
                    json.dumps({
                        "payload": payload,
                        "mode": mode,
                        "concurrency": concurrency,
                        "p95_ms": measured["accepted_metrics"]["latency_ms"]["p95"],
                        "accepted": measured["accepted"],
                    }),
                    flush=True,
                )
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["new_blob_admission"] = await measure_new_blobs(args.directory)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True, help="New disposable benchmark directory")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--payload-bytes", type=int, nargs="+", default=[0, 1073741824])
    parser.add_argument("--blob-size", type=int, default=8192)
    parser.add_argument("--samples", type=int, default=128)
    args = parser.parse_args()
    if not 8 <= args.samples <= 256 or args.samples % 8 or args.blob_size < 8:
        parser.error("samples must be a multiple of 8 in 8..256; blob size must be at least 8")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
