from enum import Enum

from pydantic import BaseModel, ConfigDict

from laglitsynth.models import RunMeta

TOOL_NAME = "laglitsynth.fulltext_retrieval.retrieve"


class PdfSource(str, Enum):
    oa = "oa"  # OA URL on the Work record
    unpaywall = "unpaywall"  # Unpaywall best_oa_location
    zotero_import = "zotero-import"  # ingested from a Zotero-exported folder
    manual = "manual"  # ingested from a plain folder drop
    missing = "missing"  # no PDF yet; recorded so gaps are addressable


class PdfProvenanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    stem: str  # data/pdfs/<stem>.pdf
    doi: str | None  # None for DOI-less works
    source: PdfSource
    source_url: str | None  # the URL a PDF was fetched from, when applicable
    pdf_path: str | None  # "data/pdfs/<stem>.pdf" when source != missing, else None
    content_sha256: str | None  # of the PDF bytes; None when missing
    obtained_at: str  # ISO-8601 UTC of the record


class RetrievalMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    total_works: int
    retrieved_count: int
    abstract_only_count: int
    failed_count: int
    by_source: dict[str, int]
