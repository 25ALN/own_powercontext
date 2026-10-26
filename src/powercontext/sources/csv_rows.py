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

"""Captured, row-level Sources for offline CSV documents."""

from __future__ import annotations

import csv
import io
import json
from urllib.parse import quote

from pydantic import BaseModel, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

CSV_ROW_SOURCE_NAME = "csv-row"


class CSVDocumentInput(BaseModel):
    """One complete offline CSV document and the column containing its identity."""

    document_id: str
    primary_key: str
    content: bytes


class CSVRowInput(BaseModel):
    """One validated CSV row represented without type conversion."""

    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]

    @model_validator(mode="after")
    def validate_row(self) -> CSVRowInput:
        _validate_document_id(self.document_id)
        _validate_primary_key(self.primary_key)
        if not self.primary_key_value or not self.primary_key_value.strip():
            raise ValueError("primary_key_value must not be empty or blank")
        if len(self.columns) != len(self.values):
            raise ValueError("columns and values must have the same length")
        _validate_header(self.columns)
        if self.primary_key not in self.columns:
            raise ValueError("primary_key must match a column exactly")
        if self.values[self.columns.index(self.primary_key)] != self.primary_key_value:
            raise ValueError("primary_key_value must match the primary-key column value")
        return self


class CSVRowSource(Source):
    """A captured Source containing one complete CSV record."""

    source_id: str
    document_id: str
    primary_key: str
    primary_key_value: str
    columns: tuple[str, ...]
    values: tuple[str, ...]


def parse_csv_document(value: CSVDocumentInput) -> tuple[CSVRowInput, ...]:
    """Parse and validate a complete CSV document atomically."""

    _validate_document_id(value.document_id)
    _validate_primary_key(value.primary_key)

    try:
        text = value.content.decode("utf-8-sig")
    except (AttributeError, UnicodeDecodeError) as error:
        raise ValueError("CSV content must be valid UTF-8") from error

    try:
        records = tuple(
            csv.reader(
                io.StringIO(text, newline=""),
                delimiter=",",
                quotechar='"',
                strict=True,
            )
        )
    except (csv.Error, TypeError, ValueError) as error:
        raise ValueError("invalid CSV document") from error

    if not records or not records[0]:
        raise ValueError("CSV document must contain a non-empty header")

    columns = tuple(records[0])
    _validate_header(columns)
    try:
        primary_key_index = columns.index(value.primary_key)
    except ValueError as error:
        raise ValueError("primary_key must match a column exactly") from error

    rows: list[CSVRowInput] = []
    seen_primary_keys: set[str] = set()
    for record in records[1:]:
        if not record:
            continue
        if len(record) != len(columns):
            raise ValueError("CSV row width must match the header width")
        primary_key_value = record[primary_key_index]
        if not primary_key_value or not primary_key_value.strip():
            raise ValueError("primary-key values must not be empty or blank")
        if primary_key_value in seen_primary_keys:
            raise ValueError("primary-key values must be unique within a document")
        seen_primary_keys.add(primary_key_value)
        rows.append(
            CSVRowInput(
                document_id=value.document_id,
                primary_key=value.primary_key,
                primary_key_value=primary_key_value,
                columns=columns,
                values=tuple(record),
            )
        )
    return tuple(rows)


def csv_row_source_id(document_id: str, primary_key_value: str) -> str:
    """Build the delimiter-safe stable identity for one CSV row."""

    _validate_document_id(document_id)
    if not isinstance(primary_key_value, str) or not primary_key_value or not primary_key_value.strip():
        raise ValueError("primary_key_value must not be empty or blank")
    source_id = f"csv:{quote(document_id, safe='')}:{quote(primary_key_value, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"source_id must not exceed {MAX_SOURCE_ID_LENGTH} characters")
    return source_id


class CSVRowSourceAdapter:
    """Resolve and read captured CSV row Sources."""

    input_class = CSVRowInput
    name = CSV_ROW_SOURCE_NAME
    source_class = CSVRowSource

    async def resolve(self, value: CSVRowInput, /) -> CSVRowSource:
        source_id = csv_row_source_id(value.document_id, value.primary_key_value)
        return CSVRowSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            primary_key=value.primary_key,
            primary_key_value=value.primary_key_value,
            columns=value.columns,
            values=value.values,
        )

    async def read(self, source: CSVRowSource, /) -> CSVRowInput:
        return CSVRowInput(
            document_id=source.document_id,
            primary_key=source.primary_key,
            primary_key_value=source.primary_key_value,
            columns=source.columns,
            values=source.values,
        )


class CSVRowTextEvidenceProjection:
    """Project one CSV row as the standard textual evidence shape."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = CSVRowSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: CSVRowSource, /) -> TextEvidence:
        row = dict(zip(source.columns, source.values, strict=True))
        return TextEvidence(
            source_type=CSV_ROW_SOURCE_NAME,
            source_id=source.source_id,
            content=json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            metadata={
                "document_id": source.document_id,
                "primary_key": source.primary_key,
                "primary_key_value": source.primary_key_value,
                "columns": list(source.columns),
                "row": row,
            },
        )


CSV_ROW_SOURCE_ADAPTER = CSVRowSourceAdapter()
CSV_ROW_SOURCE_DEFINITION: SourceDefinition[CSVRowInput, CSVRowSource, CSVRowInput] = AdapterSourceDefinition(
    CSV_ROW_SOURCE_ADAPTER,
    projections=(CSVRowTextEvidenceProjection(),),
)


def _validate_document_id(document_id: str) -> None:
    if not isinstance(document_id, str) or not document_id or document_id != document_id.strip():
        raise ValueError("document_id must be non-empty and must not contain surrounding whitespace")


def _validate_primary_key(primary_key: str) -> None:
    if not isinstance(primary_key, str) or not primary_key.strip():
        raise ValueError("primary_key must be non-empty")


def _validate_header(columns: tuple[str, ...]) -> None:
    if any(not column or not column.strip() for column in columns):
        raise ValueError("CSV header fields must be non-empty and non-blank")
    if len(set(columns)) != len(columns):
        raise ValueError("CSV header fields must be unique")


__all__ = [
    "CSVDocumentInput",
    "CSVRowInput",
    "CSVRowSource",
    "CSVRowSourceAdapter",
    "CSVRowTextEvidenceProjection",
    "CSV_ROW_SOURCE_ADAPTER",
    "CSV_ROW_SOURCE_DEFINITION",
    "CSV_ROW_SOURCE_NAME",
    "csv_row_source_id",
    "parse_csv_document",
]
