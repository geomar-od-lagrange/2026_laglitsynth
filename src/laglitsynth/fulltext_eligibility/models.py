from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from laglitsynth.models import LlmMeta, RunMeta

TOOL_NAME = "laglitsynth.fulltext_eligibility.assess"


class EligibilityVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    probability: float | None = None  # decisions-endpoint answer; None for sentinels
    eligible: bool | None = None  # probability >= 0.5; None for sentinels
    reason: str | None = None  # sentinel reason; None on a decided verdict
    raw_response: str | None = None  # response or error body; None when none was received


class EligibilityMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    llm: LlmMeta
    corpus_id: str  # CorpusMeta.corpus_id of the corpus the works came from
    input_extractions: str
    input_count: int
    eligible_count: int
    excluded_count: int
    tei_parse_failure_count: int
    llm_parse_failure_count: int
    llm_timeout_count: int = 0
    llm_rejected_count: int = 0
    criterion: str = ""  # the predicate instructions sent, verbatim
