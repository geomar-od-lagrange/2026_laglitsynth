from pydantic import BaseModel, ConfigDict

from laglitsynth.fulltext_retrieval.models import PdfProvenanceRecord
from laglitsynth.models import LlmMeta, RunMeta

TOOL_NAME = "laglitsynth.corpus.assemble"

WORKS_FILENAME = "works.jsonl"
VERDICTS_FILENAME = "verdicts.jsonl"
PDFS_SUBDIR = "pdfs"
PROVENANCE_FILENAME = "provenance.jsonl"
META_FILENAME = "corpus-meta.json"


class CorpusPdf(BaseModel):
    """One selected work's PDF in the corpus; works without a PDF included."""

    model_config = ConfigDict(extra="forbid")
    work_id: str
    pdf_path: str | None  # "pdfs/<stem>.pdf" relative to the corpus dir; None without a PDF
    content_sha256: str | None  # of the corpus copy; None without a PDF
    store_record: PdfProvenanceRecord | None  # the store's record; None when it has none


class CorpusMeta(BaseModel):
    """How the corpus was assembled: ``corpus-meta.json``."""

    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    corpus_id: str  # sha256 over sorted "work_id\tcontent_sha256" lines
    analysis_run_id: str  # default run-id of stages 7 and 8 on this corpus
    catalogue: str
    screening_verdicts: str
    screening_run_id: str | None  # None when --screening-verdicts was passed explicitly
    screening_llm: LlmMeta
    screening_threshold: float
    catalogue_count: int
    selected_count: int
    unscored_count: int  # selected works whose verdict has no score
    below_threshold_count: int  # catalogue works left out by the threshold
    no_verdict_count: int  # catalogue works without a verdict row (included)
    with_pdf_count: int
