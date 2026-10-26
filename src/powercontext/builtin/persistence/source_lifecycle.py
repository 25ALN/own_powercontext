"""Tables for Source lifecycle dependency state."""

from sqlalchemy import Boolean, Column, Integer, LargeBinary, MetaData, String, Table, Text

METADATA = MetaData()
SOURCE_LIFECYCLE_NODES = Table(
    "pc_source_lifecycle_nodes",
    METADATA,
    Column("scope_id", String(256), primary_key=True),
    Column("node_id", String(256), primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("ref_payload", LargeBinary, nullable=False),
    Column("parents_payload", LargeBinary, nullable=False),
    Column("policy", String(8), nullable=False),
    Column("available", Boolean, nullable=False),
    Column("revision", Integer),
    Column("sequence", Integer),
)
SOURCE_LIFECYCLE_EVENTS = Table(
    "pc_source_lifecycle_events",
    METADATA,
    Column("scope_id", String(256), primary_key=True),
    Column("node_id", String(256), primary_key=True),
    Column("sequence", Integer, primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("available", Boolean, nullable=False),
    Column("reason", Text, nullable=False),
)

LIFECYCLE_TABLES = (SOURCE_LIFECYCLE_NODES, SOURCE_LIFECYCLE_EVENTS)
