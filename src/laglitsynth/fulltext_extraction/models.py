from pydantic import BaseModel, ConfigDict

from laglitsynth.models import _RunMeta

TOOL_NAME = "laglitsynth.fulltext_extraction.extract"


class TextSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    text: str


class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    sections: list[TextSection]
    raw_text: str
    extracted_at: str  # per-record wall-clock timestamp


class ExtractionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: _RunMeta
    grobid_version: str
    total_pdfs: int
    extracted_count: int
    failed_count: int
    invalid_stem_count: int = 0
