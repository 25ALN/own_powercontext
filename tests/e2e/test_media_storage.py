# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Transactional media acceptance and explicit maintenance through the runtime boundary."""

import asyncio
import base64
import hashlib
import shutil
import sqlite3
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from powercontext.builtin.images.config import ImageConfig
from powercontext.builtin.images.identity import image_profile_digest
from powercontext.builtin.media.errors import MediaError
from powercontext.builtin.media.models import ImageAnalysisRequest, ImageCaptureInput
from powercontext.builtin.persistence.migrations.backup import BackupContext, SQLiteBackupProvider
from powercontext.builtin.persistence.migrations.media import MediaMigrationRunner
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.builtin.scope import ScopeDraft


def images_for(runtime):
    from powercontext.builtin.images.application import ImageApplication

    value = runtime.images
    assert isinstance(value, ImageApplication)
    return value


def profile_for(runtime):
    value = images_for(runtime).profile
    assert value is not None
    return value


def picture(color="red"):
    stream = BytesIO()
    Image.new("RGB", (8, 8), color).save(stream, format="PNG")
    return stream.getvalue()


def config_for(path: Path, *, enabled=True, **limits):
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"),
        images=ImageConfig(enabled=enabled, **limits),
        # These storage scenarios deliberately make no background/model claim.
        runtime=RuntimeConfig(artifact_processing_families=()),
        inference=InferenceConfig(
            vision_model="fixture", vision_deployment_id="fixture-v1", vision_base_url="http://127.0.0.1:11434/v1"
        ),
    )


async def scope_for(runtime, key="media"):
    return await runtime.scopes.create(ScopeDraft(title=key, summary="Media fixture", idempotency_key=key))


def capture_input(scope, key="capture", data=None):
    return ImageCaptureInput.model_validate({
        "scope_id": scope,
        "capture_key": key,
        "media_type": "image/png",
        "bytes_base64": base64.b64encode(picture() if data is None else data).decode(),
        "origin": {"kind": "explicit_upload", "attachment_index": 0},
        "context": {"text": ""},
    })


def test_media_replay_dedup_and_exact_read_after_restart(tmp_path):
    async def scenario():
        config = config_for(tmp_path / "media.db")
        async with open_builtin_runtime(config) as runtime:
            scope = await scope_for(runtime)
            first = await images_for(runtime).capture(capture_input(scope.scope_id))
            replay = await images_for(runtime).capture(capture_input(scope.scope_id))
            other = await images_for(runtime).capture(capture_input(scope.scope_id, "different-occurrence"))
            assert replay.replayed and replay.request_id == first.request_id
            assert replay.original == first.original
            assert other.original != first.original and other.asset == first.asset
            with pytest.raises(MediaError, match="media_idempotency_conflict"):
                await images_for(runtime).capture(capture_input(scope.scope_id, data=picture("blue")))
        async with open_builtin_runtime(config) as runtime:
            asset, data = await images_for(runtime).get_asset(scope.scope_id, first.asset.asset_id)
            assert asset == first.asset and data == picture()
            request = await images_for(runtime).get_request(scope.scope_id, first.request_id)
            assert request.original == first.original and request.attempt_count == 0
            page = await images_for(runtime).list_requests(scope.scope_id, limit=1)
            next_page = await images_for(runtime).list_requests(scope.scope_id, limit=1, cursor=page.next_cursor)
            assert page.next_cursor is not None and next_page.next_cursor is None
            assert {r.request_id for r in (*page.items, *next_page.items)} == {first.request_id, other.request_id}
            assert images_for(runtime).recovery_cost == {"assets": 1, "requests": 2, "retained_bytes": len(data)}

    asyncio.run(scenario())


def test_failed_source_commit_rolls_back_blob_and_capacity(tmp_path, monkeypatch):
    async def scenario():
        async with open_builtin_runtime(
            config_for(tmp_path / "rollback.db", max_asset_bytes_total=len(picture()))
        ) as runtime:
            scope = await scope_for(runtime)
            write = images_for(runtime).sources.add_with_status

            async def fail(*args, **kwargs):
                raise OSError("injected Source write failure")  # noqa: TRY003 - transaction failure injection

            monkeypatch.setattr(images_for(runtime).sources, "add_with_status", fail)
            with pytest.raises(OSError):
                await images_for(runtime).capture(capture_input(scope.scope_id))
            asset_id = "ma_" + hashlib.sha256(picture()).hexdigest()
            with pytest.raises(MediaError, match="media_asset_not_found"):
                await images_for(runtime).get_asset(scope.scope_id, asset_id)
            monkeypatch.setattr(images_for(runtime).sources, "add_with_status", write)
            accepted = await images_for(runtime).capture(capture_input(scope.scope_id))
            assert (await images_for(runtime).get_asset(scope.scope_id, accepted.asset.asset_id))[1] == picture()

    asyncio.run(scenario())


def test_concurrent_media_admission_cannot_exceed_shared_capacity(tmp_path):
    async def scenario():
        red, blue = picture(), picture("blue")
        config = config_for(tmp_path / "concurrent.db", max_asset_bytes_total=max(len(red), len(blue)))
        async with open_builtin_runtime(config) as runtime:
            first_scope, second_scope = await scope_for(runtime), await scope_for(runtime, "second")
            outcomes = await asyncio.gather(
                images_for(runtime).capture(capture_input(first_scope.scope_id, data=red)),
                images_for(runtime).capture(capture_input(second_scope.scope_id, data=blue)),
                return_exceptions=True,
            )
            accepted = [r for r in outcomes if not isinstance(r, BaseException)]
            rejected = [r for r in outcomes if isinstance(r, MediaError)]
            assert len(accepted) == len(rejected) == 1
            assert rejected[0].code == "media_capacity_exceeded"
            assert (await images_for(runtime).get_asset(accepted[0].original.scope_id, accepted[0].asset.asset_id))[
                1
            ] in (red, blue)

    asyncio.run(scenario())


def test_active_request_exclusion_requires_exact_replacement_precondition(tmp_path):
    async def scenario():
        async with open_builtin_runtime(config_for(tmp_path / "active.db")) as runtime:
            scope = await scope_for(runtime)
            first = await images_for(runtime).capture(capture_input(scope.scope_id))
            command = ImageAnalysisRequest(
                scope_id=scope.scope_id,
                operation="reparse",
                idempotency_key="new",
                original=first.original.source,
                expected_artifact_revision=0,
                expected_profile_digest=image_profile_digest(profile_for(runtime)),
            )
            with pytest.raises(MediaError, match="media_active_request_conflict"):
                await images_for(runtime).request_analysis(command)
            replacement = await images_for(runtime).request_analysis(
                command.model_copy(
                    update={
                        "request_id": first.request_id,
                        "expected_state_version": 1,
                    }
                )
            )
            assert replacement.request_id != first.request_id
            assert (await images_for(runtime).get_request(scope.scope_id, first.request_id)).state.value == "superseded"
            assert (
                await images_for(runtime).request_analysis(
                    command.model_copy(
                        update={
                            "request_id": first.request_id,
                            "expected_state_version": 1,
                        }
                    )
                )
            ).request_id == replacement.request_id

    asyncio.run(scenario())


def test_media_upgrade_is_explicit_and_preserves_existing_sources(tmp_path):
    path = tmp_path / "upgrade.db"

    async def create_text():
        async with open_builtin_runtime(config_for(path, enabled=False)) as runtime:
            scope = await scope_for(runtime)
            from powercontext.builtin.runtime import CaptureSource

            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="text", content="Existing evidence", metadata={})
            )
            return scope.scope_id

    scope = asyncio.run(create_text())
    with sqlite3.connect(path) as connection:
        original = connection.execute("SELECT * FROM pc_sources").fetchall()

    async def rejected_admission():
        async with open_builtin_runtime(config_for(path)) as runtime:
            assert not images_for(runtime).available
            with pytest.raises(MediaError, match="media_migration_required"):
                await images_for(runtime).capture(capture_input(scope))

    asyncio.run(rejected_admission())
    runner = MediaMigrationRunner(config_for(path).database)
    plan = runner.plan()
    result = runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    assert result.changed and result.backup_state == "completed"
    assert runner.verify().state == "ready"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT * FROM pc_sources").fetchall() == original
        assert (
            connection.execute(
                "SELECT count(*) FROM sqlite_schema WHERE type='table' AND name GLOB 'pc_media_*'"
            ).fetchone()[0]
            == 3
        )

    async def accepted_admission():
        async with open_builtin_runtime(config_for(path)) as runtime:
            assert (await images_for(runtime).capture(capture_input(scope))).sequence == 2

    asyncio.run(accepted_admission())


def test_experimental_database_is_rejected_without_writes(tmp_path):
    path = tmp_path / "experimental.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE pc_experimental_image_state (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO pc_experimental_image_state VALUES (1)")
    before = path.read_bytes()
    runner = MediaMigrationRunner(config_for(path).database)
    with pytest.raises(MigrationError, match="experimental_media_import_unsupported"):
        runner.plan()
    assert path.read_bytes() == before


def test_database_backup_restore_contains_exact_media_bytes(tmp_path):
    path = tmp_path / "before.db"

    async def capture():
        async with open_builtin_runtime(config_for(path)) as runtime:
            scope = await scope_for(runtime)
            return await images_for(runtime).capture(capture_input(scope.scope_id))

    receipt = asyncio.run(capture())
    provider = SQLiteBackupProvider(path)
    backup = provider.create_backup(
        BackupContext(
            database_id="media-test",
            source_revision="media-0001",
            bundle_checksum="test",
            maintenance_window_id="backup",
            objects=(),
            writers_stopped=True,
        )
    )
    assert provider.inspect_backup(backup).state == "completed"
    restored = tmp_path / "restored.db"
    shutil.copyfile(backup.location, restored)

    async def read():
        async with open_builtin_runtime(config_for(restored)) as runtime:
            assert (await images_for(runtime).get_asset(receipt.original.scope_id, receipt.asset.asset_id))[
                1
            ] == picture()
            assert (
                await images_for(runtime).get_request(receipt.original.scope_id, receipt.request_id)
            ).original == receipt.original

    asyncio.run(read())


@pytest.mark.parametrize(
    "column,mutation,code",
    [
        ("descriptor", b'{"schema":"future-media-v9"}', "media_asset_corrupt"),
        ("bytes", b"x" * len(picture()), "media_asset_corrupt"),
    ],
)
def test_corrupt_assets_fail_integrity_without_reinterpreting_or_replacing_evidence(tmp_path, column, mutation, code):
    async def scenario():
        config = config_for(tmp_path / "corrupt.db")
        async with open_builtin_runtime(config) as runtime:
            scope = (await scope_for(runtime)).scope_id
            receipt = await images_for(runtime).capture(capture_input(scope))
        with sqlite3.connect(tmp_path / "corrupt.db") as db:
            db.execute(f"UPDATE pc_media_assets SET {column}=?", (mutation,))  # noqa: S608 - frozen test columns
            before = db.execute("SELECT source_type,source_id,payload,journal_position FROM pc_sources").fetchall()
        with pytest.raises(MigrationError, match=code):
            MediaMigrationRunner(config.database).verify()
        async with open_builtin_runtime(config) as runtime:
            assert not images_for(runtime).available and images_for(runtime).unavailable_code == code
            # Broken media cannot stop ordinary text capture or fabricate replacement records.
            from powercontext.builtin.runtime import CaptureSource

            await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="text-still-works", content="Text", metadata={})
            )
            with pytest.raises(MediaError, match=code):
                await images_for(runtime).get_request(scope, receipt.request_id)
        with sqlite3.connect(tmp_path / "corrupt.db") as db:
            assert (
                db.execute(
                    "SELECT source_type,source_id,payload,journal_position FROM pc_sources WHERE source_type='image.original'"
                ).fetchall()
                == before
            )

    asyncio.run(scenario())


def test_partial_media_schema_requires_explicit_recovery_and_plan_changes_are_rejected(tmp_path):
    database = tmp_path / "partial.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE pc_media_assets (wrong_column TEXT)")
    config = config_for(database)
    with pytest.raises(MigrationError, match="incompatible_schema"):
        MediaMigrationRunner(config.database).plan()
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA table_info(pc_media_assets)").fetchall()[0][1] == "wrong_column"
    database = tmp_path / "changed.db"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE caller_data(value TEXT)")
    config = config_for(database)
    runner = MediaMigrationRunner(config.database)
    plan = runner.plan()
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE unexpected_change(value TEXT)")
    with pytest.raises(MigrationError, match="plan_acceptance_required"):
        runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT name FROM sqlite_schema WHERE name LIKE 'pc_media_%'").fetchall() == []


def test_logical_archive_refuses_media_before_publishing_an_incomplete_backup(tmp_path):
    from powercontext.builtin.portability import BundleCompatibilityError

    async def scenario():
        async with open_builtin_runtime(config_for(tmp_path / "archive.db")) as runtime:
            scope = (await scope_for(runtime)).scope_id
            receipt = await images_for(runtime).capture(capture_input(scope))
            output = tmp_path / "safe.pcb"
            output.write_bytes(b"preserve prior backup")

            async def authorize(scopes):
                assert scopes == (scope,)

            assert runtime.archive is not None
            with pytest.raises(BundleCompatibilityError, match="media bytes"):
                await runtime.archive.export([scope], output, authorize=authorize)
            assert output.read_bytes() == b"preserve prior backup"
            assert (await images_for(runtime).get_asset(scope, receipt.asset.asset_id))[1] == picture()

    asyncio.run(scenario())
