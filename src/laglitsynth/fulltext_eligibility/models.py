from typing import Literal

from pydantic import BaseModel, ConfigDict

from laglitsynth.models import _LlmMeta, _RunMeta

TOOL_NAME = "laglitsynth.fulltext_eligibility.assess"


SourceBasis = Literal["full_text", "abstract_only", "none"]


class EligibilityVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    eligible: bool | None = None  # None for sentinel-reason skips
    source_basis: SourceBasis
    reason: str | None = None  # LLM free-text or sentinel
    seed: int | None = None  # Ollama seed used for this call; None for sentinel reasons


class EligibilityMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: _RunMeta
    llm: _LlmMeta
    input_catalogue: str
    input_extractions: str
    input_count: int
    eligible_count: int
    excluded_count: int
    skipped_count: int
    by_source_basis: dict[str, int]
