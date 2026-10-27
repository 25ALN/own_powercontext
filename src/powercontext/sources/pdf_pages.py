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

"""Page-level captured Sources for complete PDF documents."""

from __future__ import annotations

import hashlib
import importlib
import io
import re
from collections import Counter
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field, model_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources.definitions import AdapterSourceDefinition, SourceDefinition
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence

if TYPE_CHECKING:
    from powercontext.artifacts import MemoryCitation

PDF_PAGE_SOURCE_NAME = "pdf-page"
_TOKEN_PATTERN = re.compile(r"[a-z]+|\d+", re.IGNORECASE)
_QUERY_STOP_WORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "been", "by", "can", "define", "did", "do", "does", "for", "from", "give", "has", "have", "how", "in", "into", "is", "it", "its", "less", "of", "on", "or", "primarily", "reported", "should", "state", "that", "the", "their", "then", "these", "this", "to", "use", "what", "when", "where", "which", "who", "with", "within"]
)
_QUERY_VARIANTS = 8
_CANDIDATE_MULTIPLIER = 8


class _PDFPasswordRequiredError(Exception):
    pass


class PDFDocumentInput(BaseModel):
    """One complete PDF document supplied as its original bytes."""

    document_id: str
    content: bytes

    @model_validator(mode="after")
    def validate_document_id(self) -> PDFDocumentInput:
        _validate_document_id(self.document_id)
        return self


class PDFPageInput(BaseModel):
    """The extractable text and durable identity of one physical PDF page."""

    document_id: str
    document_sha256: str
    page_number: int = Field(strict=True, ge=1)
    text: str

    @model_validator(mode="after")
    def validate_page(self) -> PDFPageInput:
        _validate_document_id(self.document_id)
        if not re.fullmatch(r"[0-9a-f]{64}", self.document_sha256):
            raise ValueError("document_sha256 must be a lowercase SHA-256 digest")  # noqa: TRY003
        return self


class PDFPageSource(Source):
    """A captured page whose identity changes when its document changes."""

    source_id: str
    document_id: str
    document_sha256: str
    page_number: int
    text: str


def parse_pdf_document(value: PDFDocumentInput) -> tuple[PDFPageInput, ...]:
    """Extract every physical page, returning no partial result on failure."""

    _validate_document_id(value.document_id)
    if not value.content:
        raise ValueError("PDF content must not be empty")  # noqa: TRY003

    digest = hashlib.sha256(value.content).hexdigest()
    try:
        PdfReader = importlib.import_module("pypdf").PdfReader
        reader = PdfReader(io.BytesIO(value.content), strict=False)
        _require_empty_password_access(reader)
        pages = tuple(
            PDFPageInput(
                document_id=value.document_id,
                document_sha256=digest,
                page_number=page_number,
                text="" if "/Contents" not in page else (page.extract_text() or ""),
            )
            for page_number, page in enumerate(reader.pages, start=1)
        )
    except _PDFPasswordRequiredError as error:
        raise ValueError("PDF requires a password") from error  # noqa: TRY003
    except Exception as error:
        raise ValueError("PDF could not be parsed completely") from error  # noqa: TRY003
    if not pages:
        raise ValueError("PDF must contain at least one physical page")  # noqa: TRY003
    return pages


class PDFPageSourceAdapter:
    """Resolve and read captured PDF page inputs."""

    input_class = PDFPageInput
    name = PDF_PAGE_SOURCE_NAME
    source_class = PDFPageSource

    async def resolve(self, value: PDFPageInput, /) -> PDFPageSource:
        source_id = pdf_page_source_id(value.document_id, value.document_sha256, value.page_number)
        return PDFPageSource(
            name=source_id,
            source_id=source_id,
            materialization=SourceMaterialization.CAPTURED,
            document_id=value.document_id,
            document_sha256=value.document_sha256,
            page_number=value.page_number,
            text=value.text,
        )

    async def read(self, source: PDFPageSource, /) -> PDFPageInput:
        return PDFPageInput(
            document_id=source.document_id,
            document_sha256=source.document_sha256,
            page_number=source.page_number,
            text=source.text,
        )


class PDFPageTextEvidenceProjection:
    """Project a PDF page as standard text evidence with exact source metadata."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PDFPageSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PDFPageSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PDF_PAGE_SOURCE_NAME,
            source_id=source.name,
            content=f"{source.document_id}\n{source.text}",
            metadata={
                "document_id": source.document_id,
                "document_sha256": source.document_sha256,
                "page_number": source.page_number,
            },
        )


PDF_PAGE_SOURCE_ADAPTER = PDFPageSourceAdapter()
PDF_PAGE_SOURCE_DEFINITION: SourceDefinition[PDFPageInput, PDFPageSource, PDFPageInput] = AdapterSourceDefinition(
    PDF_PAGE_SOURCE_ADAPTER,
    projections=(PDFPageTextEvidenceProjection(),),
)


def pdf_page_source_id(document_id: str, document_sha256: str, page_number: int) -> str:
    """Return a bounded deterministic identity for a document revision and page."""

    _validate_document_id(document_id)
    if not re.fullmatch(r"[0-9a-f]{64}", document_sha256):
        raise ValueError("document_sha256 must be a lowercase SHA-256 digest")  # noqa: TRY003
    if isinstance(page_number, bool) or not isinstance(page_number, int) or page_number < 1:
        raise ValueError("page_number must be a positive integer")  # noqa: TRY003
    identity = f"{len(document_id)}:{document_id}:{document_sha256}:{page_number}".encode()
    source_id = f"pdf:{hashlib.sha256(identity).hexdigest()}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError("PDF page Source identity exceeds the configured maximum")  # noqa: TRY003
    return source_id


async def search_pdf_pages(memory, question: str, limit: int = 10) -> tuple[MemoryCitation, ...]:
    """Search persisted page evidence and return distinct exact Memory citations."""

    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")  # noqa: TRY003
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be non-empty text")  # noqa: TRY003

    from powercontext.artifacts import MemoryCitation
    from powercontext.builtin.runtime import SearchMemoryRequest

    variants = _query_variants(question)
    candidate_limit = max(limit, min(160, limit * _CANDIDATE_MULTIPLIER))
    candidates: dict[tuple[str, str, str, int], _RankedCitation] = {}
    for query_rank, query in enumerate(variants):
        page = await memory.search(SearchMemoryRequest(query=query, limit=candidate_limit, mode="fts"))
        for rank, hit in enumerate(page.hits, start=1):
            key = (
                hit.memory_ref.artifact_id,
                hit.entry_id,
                hit.entry_version_id,
                hit.memory_ref.revision,
            )
            item = candidates.get(key)
            if item is None:
                item = _RankedCitation(
                    citation=MemoryCitation(
                        memory_ref=hit.memory_ref,
                        entry_id=hit.entry_id,
                        entry_version_id=hit.entry_version_id,
                    ),
                    text=hit.text,
                )
                candidates[key] = item
            item.add(query_rank, rank, question)

    ranked = sorted(candidates.values(), key=lambda item: item.sort_key)
    return tuple(item.citation for item in ranked[:limit])


class _RankedCitation:
    __slots__ = ("citation", "lexical", "phrase", "rrf", "text")

    def __init__(self, *, citation: MemoryCitation, text: str) -> None:
        self.citation = citation
        self.text = text
        self.rrf = 0.0
        self.lexical = 0.0
        self.phrase = 0.0

    def add(self, query_rank: int, rank: int, question: str) -> None:
        self.rrf += 1 / (60 + rank + query_rank * 4)
        query_terms = _query_terms(question)
        page_terms = Counter(_TOKEN_PATTERN.findall(self.text.casefold()))
        if query_terms:
            matched = sum(min(page_terms[term], count) for term, count in query_terms.items())
            self.lexical = max(self.lexical, matched / sum(query_terms.values()))
            query_tokens = tuple(query_terms)
            page_text = f" {' '.join(_TOKEN_PATTERN.findall(self.text.casefold()))} "
            for size in (2, 3):
                phrases = tuple(
                    query_tokens[index : index + size] for index in range(len(query_tokens) - size + 1)
                )
                self.phrase = max(
                    self.phrase,
                    max((float(f" {' '.join(tokens)} " in page_text) for tokens in phrases), default=0.0),
                )

    @property
    def sort_key(self) -> tuple[float, float, float, str, str]:
        score = self.rrf + self.lexical * 0.02 + self.phrase * 0.005
        return (-score, -self.lexical, -self.phrase, self.citation.entry_id, self.citation.entry_version_id)


def _query_variants(question: str) -> tuple[str, ...]:
    variants = [" ".join(_TOKEN_PATTERN.findall(question.casefold()))]
    terms = [term for term in _TOKEN_PATTERN.findall(question.casefold()) if term not in _QUERY_STOP_WORDS]
    terms = list(dict.fromkeys(terms))
    if terms:
        variants.append(" ".join(terms))
        if len(terms) > 4:
            windows = [terms[index : index + 4] for index in range(0, len(terms) - 3, 3)]
            if windows and windows[-1][-1] != terms[-1]:
                windows.append(terms[-4:])
            variants.extend(" ".join(window) for window in windows)
    return tuple(dict.fromkeys(variant for variant in variants if variant))[:_QUERY_VARIANTS]


def _query_terms(question: str) -> Counter[str]:
    terms = [term for term in _TOKEN_PATTERN.findall(question.casefold()) if term not in _QUERY_STOP_WORDS]
    return Counter(terms)


def _validate_document_id(document_id: str) -> None:
    if not isinstance(document_id, str) or not document_id.strip():
        raise ValueError("document_id must not be empty or blank")  # noqa: TRY003


def _require_empty_password_access(reader) -> None:
    if reader.is_encrypted and not reader.decrypt(""):
        raise _PDFPasswordRequiredError


__all__ = [
    "PDF_PAGE_SOURCE_ADAPTER",
    "PDF_PAGE_SOURCE_DEFINITION",
    "PDF_PAGE_SOURCE_NAME",
    "PDFDocumentInput",
    "PDFPageInput",
    "PDFPageSource",
    "PDFPageSourceAdapter",
    "PDFPageTextEvidenceProjection",
    "parse_pdf_document",
    "pdf_page_source_id",
    "search_pdf_pages",
]
