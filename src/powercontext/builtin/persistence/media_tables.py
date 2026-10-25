# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared media identity, indexed request lifecycle and derived capacity ledger."""

from sqlalchemy import BigInteger, CheckConstraint, Column, Index, Integer, LargeBinary, MetaData, String, Table
from sqlalchemy.dialects.mysql import MEDIUMBLOB

from powercontext.builtin.persistence.tables import identity_string

METADATA = MetaData()
PAYLOAD = LargeBinary().with_variant(MEDIUMBLOB(), "mysql")

MEDIA_ASSETS = Table(
    "pc_media_assets",
    METADATA,
    Column("scope_id", identity_string(256), primary_key=True),
    Column("byte_digest", identity_string(71), primary_key=True),
    Column("media_type", String(128), nullable=False),
    Column("byte_size", BigInteger, nullable=False),
    Column("descriptor", PAYLOAD, nullable=False),
    Column("bytes", PAYLOAD, nullable=False),
    Column("created_at", BigInteger, nullable=False),
    CheckConstraint("byte_size > 0 AND byte_size <= 8388608", name="ck_pc_media_asset_bytes"),
    CheckConstraint("length(bytes) = byte_size", name="ck_pc_media_asset_payload"),
)
MEDIA_REQUESTS = Table(
    "pc_media_processing_requests",
    METADATA,
    Column("scope_id", identity_string(256), primary_key=True),
    Column("request_id", identity_string(67), primary_key=True),
    Column("original_source_type", identity_string(128), nullable=False),
    Column("original_source_id", identity_string(256), nullable=False),
    Column("processing_kind", identity_string(128), nullable=False),
    Column("handler_schema", String(128), nullable=False),
    Column("operation", String(16), nullable=False),
    Column("operation_target", identity_string(256), nullable=False),
    Column("idempotency_key", identity_string(256), nullable=False),
    Column("request_digest", identity_string(71), nullable=False),
    Column("profile_digest", identity_string(71), nullable=False),
    Column("state", String(16), nullable=False),
    Column("state_version", Integer, nullable=False),
    Column("active_key", String(8)),
    Column("due_at", BigInteger, nullable=False),
    Column("claim_token", String(36)),
    Column("claim_until", BigInteger, nullable=False, server_default="0"),
    Column("claim_group", String(256)),
    Column("claim_generation", BigInteger),
    Column("claim_holder", String(128)),
    Column("input", PAYLOAD, nullable=False),
    Column("receipt", PAYLOAD, nullable=False),
    Column("payload", PAYLOAD, nullable=False),
    Column("created_at", BigInteger, nullable=False),
    CheckConstraint("state_version > 0", name="ck_pc_media_request_version"),
    CheckConstraint(
        "(active_key IS NOT NULL AND active_key = 'active' AND state IN ('pending','running','retry_wait','blocked')) OR "
        "(active_key IS NULL AND state IN ('succeeded','failed','superseded'))",
        name="ck_pc_media_request_active",
    ),
)
Index(
    "uq_pc_media_request_replay",
    MEDIA_REQUESTS.c.scope_id,
    MEDIA_REQUESTS.c.processing_kind,
    MEDIA_REQUESTS.c.operation,
    MEDIA_REQUESTS.c.operation_target,
    MEDIA_REQUESTS.c.idempotency_key,
    unique=True,
)
Index(
    "uq_pc_media_request_active",
    MEDIA_REQUESTS.c.scope_id,
    MEDIA_REQUESTS.c.original_source_type,
    MEDIA_REQUESTS.c.original_source_id,
    MEDIA_REQUESTS.c.processing_kind,
    MEDIA_REQUESTS.c.active_key,
    unique=True,
)
Index(
    "ix_pc_media_request_due",
    MEDIA_REQUESTS.c.processing_kind,
    MEDIA_REQUESTS.c.state,
    MEDIA_REQUESTS.c.due_at,
    MEDIA_REQUESTS.c.scope_id,
)
Index(
    "ix_pc_media_request_page",
    MEDIA_REQUESTS.c.scope_id,
    MEDIA_REQUESTS.c.processing_kind,
    MEDIA_REQUESTS.c.created_at,
    MEDIA_REQUESTS.c.request_id,
)
Index(
    "ix_pc_media_request_state_page",
    MEDIA_REQUESTS.c.scope_id,
    MEDIA_REQUESTS.c.processing_kind,
    MEDIA_REQUESTS.c.state,
    MEDIA_REQUESTS.c.created_at,
    MEDIA_REQUESTS.c.request_id,
)
Index(
    "ix_pc_media_active_scope",
    MEDIA_REQUESTS.c.processing_kind,
    MEDIA_REQUESTS.c.active_key,
    MEDIA_REQUESTS.c.scope_id,
    MEDIA_REQUESTS.c.state,
    MEDIA_REQUESTS.c.due_at,
    MEDIA_REQUESTS.c.claim_until,
)
MEDIA_CAPACITY = Table(
    "pc_media_capacity",
    METADATA,
    Column("boundary", String(8), primary_key=True),
    Column("scope_id", identity_string(256), primary_key=True),
    Column("retained_bytes", BigInteger, nullable=False),
    Column("active_requests", Integer, nullable=False),
    CheckConstraint("retained_bytes >= 0 AND active_requests >= 0", name="ck_pc_media_capacity_nonnegative"),
    CheckConstraint(
        "(boundary = 'global' AND scope_id = '') OR (boundary = 'scope' AND scope_id != '')",
        name="ck_pc_media_capacity_boundary",
    ),
)
MEDIA_TABLES = (MEDIA_ASSETS, MEDIA_REQUESTS, MEDIA_CAPACITY)
