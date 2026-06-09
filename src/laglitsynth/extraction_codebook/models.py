"""Static identifiers and meta models for the extraction codebook stage.

The LLM-fillable payload model and the per-work record class are built
dynamically at startup from the loaded codebook spec — see
[codebook.py](codebook.py) for ``load_codebook`` and
``build_record_model``. Only the identifiers and the run-summary meta
remain here.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from laglitsynth.models import LlmMeta, RunMeta

TOOL_NAME = "laglitsynth.extraction_codebook.extract"


class ExtractionCodebookMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    llm: LlmMeta
    input_catalogue: str
    input_eligibility_verdicts: str
    input_extractions: str
    input_count: int
    full_text_count: int
    skipped_count: int  # tei-parse-failure only
    llm_parse_failure_count: int
    llm_timeout_count: int = 0
    truncated_count: int
