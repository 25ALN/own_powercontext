# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Registered SQLite media maintenance using shared plans, locks and native backups."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import make_url

from powercontext.builtin.media.errors import MediaError
from powercontext.builtin.persistence.media import MediaRepository, media_request_from_row
from powercontext.builtin.persistence.media_schema import (
    MEDIA_OBJECTS,
    MEDIA_RESOURCE,
    expected_media_inventory,
    media_inventory,
    media_resource,
    require_media_schema,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig

from .backup import BackupContext, SQLiteBackupProvider
from .locking import local_migration_lock
from .models import MigrationError, MigrationPlan, MigrationResult, digest


class MediaMigrationRunner:
    """The media bundle does not certify unrelated tables or rewrite experimental evidence."""

    def __init__(self, config: SQLiteConfig, *, evidence_directory: Path | None = None):
        url = make_url(config.url)
        if not config.is_persistent or url.query or url.host or url.username or url.password or not url.database:
            raise MigrationError("unsupported_target", "Media maintenance requires a regular file SQLite database.")
        self.database = Path(url.database).absolute().resolve()
        self.directory = evidence_directory
        self.revision = media_resource()["revision"]
        self.checksum = hashlib.sha256(MEDIA_RESOURCE.read_bytes()).hexdigest()
        self.configuration = digest(config.model_dump(mode="json", exclude={"url", "echo"}))

    def _engine(self, *, writable=False):
        uri = self.database.as_uri() + ("?mode=rwc" if writable else "?mode=ro")
        return create_engine("sqlite://", creator=lambda: sqlite3.connect(uri, uri=True))

    def _inspect(self, connection, **options):
        if hashlib.sha256(MEDIA_RESOURCE.read_bytes()).hexdigest() != self.checksum:
            raise MigrationError("plan_changed", "The registered media migration changed after planning.")
        names = set() if connection is None else set(inspect(connection).get_table_names())
        if any(name.startswith("pc_experimental_image_") for name in names):
            raise MigrationError(
                "experimental_media_import_unsupported",
                "Retain the experimental database and assets. This bundle never rewrites experimental Source identities or imports seven-table storage.",
            )
        inventory = [] if connection is None else media_inventory(connection)
        if inventory and inventory != expected_media_inventory():
            raise MigrationError(
                "incompatible_schema", "The media schema is partial or unsupported; restore its recovery point."
            )
        schema = (
            []
            if connection is None
            else [
                list(row)
                for row in connection.exec_driver_sql(
                    "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE substr(name,1,7)!='sqlite_' ORDER BY type,name"
                )
            ]
        )
        state = "ready" if inventory else "migration_required" if names else "uninitialized"
        fields = {
            "database_id": digest({"kind": "sqlite", "path": str(self.database)}),
            "source_revision": self.revision if inventory else None,
            "target_revision": self.revision,
            "schema_fingerprint": digest(schema),
            "bundle_checksum": self.checksum,
            "configuration_digest": self.configuration,
            "state": state,
            "revisions": () if inventory else (self.revision,),
            "backup_policy": options.get("backup_policy", "auto"),
            "backup_available": True,
            "affected_objects": MEDIA_OBJECTS,
            "shared_database": options.get("shared_database", False),
            "service": options.get("service"),
        }
        return MigrationPlan.model_validate({"plan_id": digest(fields), **fields})

    def plan(self, **options):
        if not self.database.exists():
            return self._inspect(None, **options)
        engine = self._engine()
        try:
            with engine.connect() as connection:
                return self._inspect(connection, **options)
        finally:
            engine.dispose()

    def verify(self):
        plan = self.plan()
        if plan.state != "ready":
            raise MigrationError("media_migration_required", "Apply the registered media bundle before admission.")
        engine = self._engine()
        try:
            with engine.connect() as connection:
                for row in connection.exec_driver_sql("SELECT * FROM pc_media_assets").mappings():
                    MediaRepository.checked_asset(row)
                for row in connection.exec_driver_sql("SELECT * FROM pc_media_processing_requests").mappings():
                    media_request_from_row(row)
        except MediaError as error:
            raise MigrationError(error.code, "Retained media did not pass typed integrity verification.") from error
        finally:
            engine.dispose()
        return MigrationResult(revision=self.revision, changed=False)

    def _backup(self, planned, policy, reference):
        if planned.state == "uninitialized":
            return reference, "not_required"
        if policy == "manual":
            return reference, "user_confirmed"
        if policy == "skip":
            return reference, "skipped"
        provider = SQLiteBackupProvider(self.database, directory=self.directory)
        context = BackupContext(
            database_id=planned.database_id,
            source_revision=None,
            bundle_checksum=self.checksum,
            maintenance_window_id=uuid4().hex,
            objects=MEDIA_OBJECTS,
            writers_stopped=True,
        )
        backup = provider.create_backup(context)
        if provider.inspect_backup(backup).state != "completed":
            raise MigrationError("backup_failed", "The media recovery point failed integrity inspection.")
        return backup.location, "completed"

    def apply(
        self,
        *,
        plan_id=None,
        accepted=False,
        maintenance_confirmed=False,
        backup_policy="auto",
        backup_confirmed=False,
        accept_no_backup=False,
        backup_ref=None,
        **options,
    ):
        planned = self.plan(backup_policy=backup_policy, **options)
        if planned.state == "ready":
            return self.verify()
        if plan_id != planned.plan_id or not accepted:
            raise MigrationError("plan_acceptance_required", "Accept the exact media migration plan.")
        if not maintenance_confirmed:
            raise MigrationError("maintenance_required", "Stop all writers for the accepted maintenance window.")
        if backup_policy == "manual" and not backup_confirmed:
            raise MigrationError("backup_confirmation_required", "Confirm the independent database backup.")
        if backup_policy == "skip" and not accept_no_backup:
            raise MigrationError("backup_required", "Explicitly accept running without a recovery point.")
        with local_migration_lock(self.database):
            engine = self._engine(writable=True)
            reference = backup_ref
            backup_state = "not_required"
            try:
                with engine.connect() as connection:
                    current = self._inspect(connection, backup_policy=backup_policy, **options)
                    if current.plan_id != planned.plan_id:
                        raise MigrationError("plan_changed", "The target changed after media migration planning.")
                    connection.rollback()
                    reference, backup_state = self._backup(planned, backup_policy, backup_ref)
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                    if self._inspect(connection, backup_policy=backup_policy, **options).plan_id != planned.plan_id:
                        raise MigrationError("plan_changed", "The target changed before media DDL.")
                    for statement in media_resource()["sqlite"]:
                        connection.exec_driver_sql(statement)
                    require_media_schema(connection)
                    connection.commit()
            finally:
                engine.dispose()
        self.verify()
        return MigrationResult(revision=self.revision, changed=True, backup_ref=reference, backup_state=backup_state)
