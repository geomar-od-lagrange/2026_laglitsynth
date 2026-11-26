from pathlib import Path

from pydantic import BaseModel, ConfigDict

from laglitsynth.fulltext_extraction.tei import TeiDocument
from laglitsynth.models import RunMeta

TOOL_NAME = "laglitsynth.fulltext_extraction.extract"

EXTRACTIONS_FILENAME = "extraction.jsonl"
META_FILENAME = "extraction-meta.json"


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    tei_path: str  # relative to output_dir, e.g. "tei/W123.tei.xml"
    content_sha256: str  # sha256 hex digest of the TEI bytes on disk
    pdf_sha256: str  # CorpusPdf.content_sha256 of the PDF the TEI came from
    extracted_at: str  # per-record wall-clock timestamp

    def open_tei(self, output_dir: Path) -> TeiDocument:
        """Load the TEI referenced by ``self.tei_path``, relative to ``output_dir``."""
        return TeiDocument(output_dir / self.tei_path)


class ExtractionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    corpus_id: str  # CorpusMeta.corpus_id of the corpus the PDFs came from
    grobid_version: str
    total_pdfs: int  # corpus works with a PDF
    extracted_count: int
    failed_count: int
