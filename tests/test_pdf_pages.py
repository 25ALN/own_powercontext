from __future__ import annotations

import hashlib
import io
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from powercontext.artifacts import ArtifactRef
from powercontext.sources import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    PDFPageInput,
    SourceDefinitionRegistry,
    parse_pdf_document,
    pdf_page_source_id,
    search_pdf_pages,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY


def blank_pdf(page_count: int = 2) -> bytes:
    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=100, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_parse_pdf_preserves_physical_blank_pages_and_full_digest() -> None:
    content = blank_pdf()

    pages = parse_pdf_document(PDFDocumentInput(document_id="annual-report", content=content))

    assert isinstance(pages, tuple)
    assert [(page.page_number, page.text) for page in pages] == [(1, ""), (2, "")]
    assert {page.document_sha256 for page in pages} == {hashlib.sha256(content).hexdigest()}


@pytest.mark.parametrize("content", [b"", b"not a PDF", b"%PDF-1.7\ntruncated"])
def test_invalid_pdf_fails_without_returning_partial_pages(content: bytes) -> None:
    with pytest.raises(ValueError):
        parse_pdf_document(PDFDocumentInput(document_id="annual-report", content=content))


@pytest.mark.parametrize("document_id", ["", "  "])
def test_document_id_must_not_be_empty_or_blank(document_id: str) -> None:
    with pytest.raises(ValueError):
        PDFDocumentInput(document_id=document_id, content=blank_pdf(1))


def test_empty_password_permission_marker_is_readable() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=200)
    writer.encrypt(user_password="", owner_password="owner")  # noqa: S106
    buffer = io.BytesIO()
    writer.write(buffer)

    pages = parse_pdf_document(PDFDocumentInput(document_id="restricted", content=buffer.getvalue()))

    assert len(pages) == 1
    assert pages[0].page_number == 1


def test_page_source_id_is_bounded_stable_and_revision_sensitive() -> None:
    document_id = "report/" + "x" * 2_000
    first = pdf_page_source_id(document_id, "a" * 64, 3)

    assert len(first) <= 256
    assert first == pdf_page_source_id(document_id, "a" * 64, 3)
    assert first != pdf_page_source_id(document_id, "b" * 64, 3)
    assert first != pdf_page_source_id(document_id, "a" * 64, 4)


async def test_page_source_projection_has_only_contract_metadata() -> None:
    page = PDFPageInput(
        document_id="annual-report",
        document_sha256="a" * 64,
        page_number=7,
        text="Revenue 1,234",
    )
    registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))

    source = await registry.resolve(page)
    assert await registry.read(source) == page
    projection = registry.project(source, TEXT_EVIDENCE_PROJECTION_KEY)
    assert projection["source_type"] == "pdf-page"
    assert projection["source_id"] == source.name
    assert projection["content"] == "annual-report\nRevenue 1,234"
    assert projection["metadata"] == {
        "document_id": "annual-report",
        "document_sha256": "a" * 64,
        "page_number": 7,
    }


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
async def test_search_rejects_invalid_limit(limit) -> None:
    with pytest.raises(ValueError, match="limit"):
        await search_pdf_pages(object(), "revenue", limit=limit)


async def test_search_rejects_blank_question() -> None:
    with pytest.raises(ValueError, match="question"):
        await search_pdf_pages(object(), "  ")


async def test_search_merges_duplicate_candidates_and_obeys_limit() -> None:
    memory_ref = ArtifactRef(family="memory", artifact_id="main", revision=1)
    first = SimpleNamespace(
        memory_ref=memory_ref,
        entry_id="entry-1",
        entry_version_id="version-1",
        text="PepsiCo operates in the United States and Europe.",
    )
    second = SimpleNamespace(
        memory_ref=memory_ref,
        entry_id="entry-2",
        entry_version_id="version-2",
        text="Net revenue was 86,392 million dollars.",
    )

    class Memory:
        def __init__(self) -> None:
            self.queries: list[str] = []

        async def search(self, request, /):
            self.queries.append(request.query)
            hits = (first, second) if len(self.queries) == 1 else (first,)
            return SimpleNamespace(hits=hits)

    memory = Memory()
    citations = await search_pdf_pages(memory, "Where does PepsiCo primarily operate?", limit=1)

    assert len(memory.queries) > 1
    assert len(citations) == 1
    assert citations[0].entry_id == "entry-1"
    assert citations[0].entry_version_id == "version-1"


def test_query_variants_include_overlapping_content_windows() -> None:
    from powercontext.sources.pdf_pages import _query_variants

    variants = _query_variants("What is the FY2022 unadjusted EBITDA less capex for PepsiCo annual report?")

    assert "fy 2022 unadjusted ebitda" in variants
    assert any("pepsico annual report" in variant for variant in variants)
