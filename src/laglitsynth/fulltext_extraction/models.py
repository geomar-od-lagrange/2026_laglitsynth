from laglitsynth.models import _Base


class TextSection(_Base):
    title: str
    text: str


class ExtractedDocument(_Base):
    work_id: str
    sections: list[TextSection]
    raw_text: str
    extracted_at: str


class ExtractionMeta(_Base):
    tool: str = "laglitsynth.fulltext_extraction.extract"
    tool_version: str = "alpha"
    grobid_version: str
    extracted_at: str
    total_pdfs: int
    extracted_count: int
    failed_count: int
