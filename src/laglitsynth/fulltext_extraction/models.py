from pathlib import Path

from pydantic import BaseModel, ConfigDict

from laglitsynth.fulltext_extraction.tei import TeiDocument
from laglitsynth.models import RunMeta

TOOL_NAME = "laglitsynth.fulltext_extraction.extract"


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    tei_path: str  # relative to output_dir, e.g. "tei/W123.tei.xml"
    content_sha256: str  # sha256 hex digest of the TEI bytes on disk
    extracted_at: str  # per-record wall-clock timestamp

    def open_tei(self, output_dir: Path) -> TeiDocument:
        """Load the TEI referenced by ``self.tei_path``, relative to ``output_dir``."""
        return TeiDocument(output_dir / self.tei_path)


class ExtractionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    grobid_version: str
    total_pdfs: int
    extracted_count: int
    failed_count: int
    invalid_stem_count: int = 0
