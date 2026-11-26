from enum import Enum

from pydantic import BaseModel, ConfigDict

from laglitsynth.models import RunMeta

TOOL_NAME = "laglitsynth.fulltext_retrieval.retrieve"


class PdfSource(str, Enum):
    oa = "oa"  # OA URL: OpenAlex location, Semantic Scholar or arXiv
    unpaywall = "unpaywall"  # Unpaywall best_oa_location
    manual = "manual"  # imported from a folder obtained any other way
    institutional = "institutional"  # scripted fetch over an institution's network
    zotero_desktop = "zotero-desktop"  # Zotero desktop "Find Available PDF"
    browser = "browser"  # browser session: Zotero connector or a clicked PDF link
    missing = "missing"  # no PDF yet; recorded so gaps are addressable


class PdfProvenanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    stem: str  # data/pdfs/<stem>.pdf
    doi: str | None  # None for DOI-less works
    source: PdfSource
    source_url: str | None  # the URL a PDF was fetched from, when applicable
    pdf_path: str | None  # "pdfs/<stem>.pdf" (under data-dir) when source != missing, else None
    content_sha256: str | None  # of the PDF bytes; None when missing
    obtained_at: str  # ISO-8601 UTC of the record


class RetrievalMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    total_works: int
    retrieved_count: int
    missing_count: int
    by_source: dict[str, int]


EXPORT_TOOL_NAME = "laglitsynth.fulltext_retrieval.export"


class ExportMeta(BaseModel):
    """How a handoff bundle was made: ``export-meta.json`` next to it."""

    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    git_commit: str | None  # None outside a git checkout
    run_id: str | None  # None without a threshold or with explicit --screening-verdicts
    catalogue: str
    screening_verdicts: str | None  # None without --screening-threshold
    screening_threshold: float | None  # None: every catalogue work is in scope
    active_count: int  # works in scope: the catalogue, or what the screening join keeps
    held_count: int  # of those, already holding a PDF
    exported_count: int  # active minus held: what the bundle lists
    with_doi_count: int
    packs: int
    pack_sizes: list[int]
