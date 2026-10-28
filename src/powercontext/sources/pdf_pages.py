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
import math
import re
from collections import Counter
from itertools import pairwise
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
_QUERY_STOP_WORDS = frozenset([
    "a",
    "an",
    "analyst",
    "and",
    "any",
    "are",
    "as",
    "assuming",
    "at",
    "answer",
    "be",
    "been",
    "by",
    "can",
    "calculate",
    "d",
    "define",
    "did",
    "do",
    "does",
    "for",
    "from",
    "give",
    "has",
    "have",
    "how",
    "if",
    "important",
    "in",
    "into",
    "is",
    "it",
    "its",
    "l",
    "million",
    "millions",
    "not",
    "of",
    "on",
    "or",
    "p",
    "perspective",
    "primarily",
    "reported",
    "respond",
    "response",
    "relying",
    "question",
    "s",
    "should",
    "shown",
    "state",
    "that",
    "the",
    "their",
    "then",
    "these",
    "this",
    "there",
    "to",
    "use",
    "using",
    "what",
    "when",
    "where",
    "which",
    "who",
    "with",
    "within",
    "only",
    "details",
    "investment",
    "ongoing",
    "quantity",
    "directly",
    "outlined",
    "explicitly",
    "usd",
])
_QUERY_TERM_ALIASES = {
    "battle": ("legal proceedings", "litigation", "lawsuit"),
    "capex": ("capital expenditures", "capital spending"),
}
_QUERY_VARIANTS = 24
_CANDIDATE_MULTIPLIER = 12
_MAX_SEARCH_CANDIDATES = 320


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
    candidate_limit = max(limit, min(_MAX_SEARCH_CANDIDATES, limit * _CANDIDATE_MULTIPLIER))
    candidates: dict[tuple[str, str, str, int], _RankedCitation] = {}
    for query in variants:
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
            item.add(rank, question)

    query_terms = tuple(_query_terms(question))
    prepared = [(item, *_page_term_index(item.text)) for item in candidates.values()]
    document_frequency = Counter(
        term
        for _item, page_terms, searchable_text in prepared
        for term in query_terms
        if _query_term_present(term, page_terms, searchable_text)
    )
    term_weights = {
        term: max(0.1, math.log((len(candidates) + 1) / (document_frequency[term] + 1))) for term in query_terms
    }
    for item, page_terms, searchable_text in prepared:
        item.score_relevance(query_terms, term_weights, page_terms, searchable_text)

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

    def add(self, rank: int, _question: str) -> None:
        self.rrf += 1 / (60 + rank)

    def score_relevance(
        self,
        query_terms: tuple[str, ...],
        term_weights: dict[str, float],
        page_terms: Counter[str],
        searchable_text: str,
    ) -> None:
        if query_terms:
            total_weight = sum(term_weights.values())
            matched_weight = sum(
                term_weights[term] for term in query_terms if _query_term_present(term, page_terms, searchable_text)
            )
            self.lexical = matched_weight / total_weight if total_weight else 0.0
            longest_run = max(
                (
                    size
                    for size in range(min(len(query_terms), 4), 1, -1)
                    if any(
                        f" {' '.join(query_terms[index : index + size])} " in searchable_text
                        for index in range(len(query_terms) - size + 1)
                    )
                ),
                default=0,
            )
            self.phrase = max(self.phrase, longest_run / 4)

    @property
    def sort_key(self) -> tuple[float, float, float, str, str]:
        score = self.rrf + self.lexical * 0.2 + self.phrase * 0.05
        return (-score, -self.lexical, -self.phrase, self.citation.entry_id, self.citation.entry_version_id)


def _page_evidence_text(text: str) -> str:
    """Exclude the projection's document identity line from page relevance evidence."""

    _document_id, separator, page_text = text.partition("\n")
    return page_text if separator else text


def _page_term_index(text: str) -> tuple[Counter[str], str]:
    page_text = _page_evidence_text(text)
    tokens = [_normalize_query_term(term) for term in _TOKEN_PATTERN.findall(page_text.casefold())]
    return Counter(tokens), f" {' '.join(tokens)} "


def _query_variants(question: str) -> tuple[str, ...]:
    tokens = _TOKEN_PATTERN.findall(question.casefold())
    terms = list(dict.fromkeys(term for term in tokens if term not in _QUERY_STOP_WORDS))
    normalized_terms = list(dict.fromkeys(_normalize_query_term(term) for term in terms))
    fixed = [" ".join(tokens)]
    if terms:
        fixed.append(" ".join(terms))
    if normalized_terms and normalized_terms != terms:
        fixed.append(" ".join(normalized_terms))

    original_windows = _query_windows(terms)
    windows = original_windows + _query_windows(normalized_terms)
    anchors: list[str] = []
    for term in terms:
        normalized = _normalize_query_term(term)
        aliases = _QUERY_TERM_ALIASES.get(normalized, ())
        anchors.extend(aliases)
    phrase_anchors = [
        (left, right)
        for left, right in pairwise(normalized_terms)
        if left.isalpha() and right.isalpha() and len(left) >= 4 and len(right) >= 4
    ]
    phrase_anchors.sort(key=lambda pair: len(pair[0]) + len(pair[1]), reverse=True)
    anchors.extend(" ".join(pair) for pair in phrase_anchors)
    anchors.extend(
        normalized for term in terms if (normalized := _normalize_query_term(term)) != term or len(normalized) >= 7
    )

    fixed = list(dict.fromkeys(variant for variant in fixed if variant))
    anchors = list(dict.fromkeys(anchor for anchor in anchors if anchor))
    windows = list(dict.fromkeys(window for window in windows if window))
    slots = _QUERY_VARIANTS - len(fixed)
    if slots <= 0:
        return tuple(fixed[:_QUERY_VARIANTS])

    anchor_slots = min(len(anchors), max(1, slots // 2))
    selected_anchors = anchors[:anchor_slots]
    window_slots = slots - len(selected_anchors)
    selected_windows = _sample_queries(windows, window_slots)
    if original_windows and original_windows[-1] not in selected_windows:
        if selected_windows:
            selected_windows[-1] = original_windows[-1]
        else:
            selected_anchors.append(original_windows[-1])
    if len(selected_windows) < window_slots:
        selected_anchors.extend(anchors[anchor_slots : anchor_slots + window_slots - len(selected_windows)])
    return (*fixed, *selected_anchors, *selected_windows)


def _query_terms(question: str) -> Counter[str]:
    terms = [
        _normalize_query_term(term)
        for term in _TOKEN_PATTERN.findall(question.casefold())
        if term not in _QUERY_STOP_WORDS
    ]
    return Counter(terms)


def _query_windows(terms: list[str]) -> list[str]:
    if len(terms) <= 4:
        return []
    windows = [terms[index : index + 4] for index in range(0, len(terms) - 3, 3)]
    if windows and windows[-1][-1] != terms[-1]:
        windows.append(terms[-4:])
    return [" ".join(window) for window in windows]


def _sample_queries(queries: list[str], limit: int) -> list[str]:
    if limit <= 0 or not queries:
        return []
    if len(queries) <= limit:
        return queries
    if limit == 1:
        return [queries[len(queries) // 2]]
    return [queries[index * (len(queries) - 1) // (limit - 1)] for index in range(limit)]


def _normalize_query_term(term: str) -> str:
    if term.endswith("ally") and len(term) > 6:
        term = term[:-2]
    if term.endswith("ies") and term not in {"series", "species"} and len(term) > 4:
        return f"{term[:-3]}y"
    if (
        term.endswith("s")
        and term not in {"news", "series", "species"}
        and not term.endswith(("ss", "us", "is"))
        and len(term) > 3
    ):
        return term[:-1]
    return term


def _query_term_present(term: str, page_terms: Counter[str], searchable_text: str) -> bool:
    if page_terms[term]:
        return True
    aliases = _QUERY_TERM_ALIASES.get(term, ())
    return any(alias in page_terms if " " not in alias else f" {alias} " in searchable_text for alias in aliases)


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
