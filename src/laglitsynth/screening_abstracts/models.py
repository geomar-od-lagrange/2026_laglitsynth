from pydantic import BaseModel, ConfigDict

from laglitsynth.models import LlmMeta, RunMeta

TOOL_NAME = "laglitsynth.screening_abstracts.screen"


class ScreeningVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    relevance_score: int | None = None  # round(100 * probability); None for sentinels
    reason: str | None = None  # sentinel reason; None for a scored work
    raw_response: str | None = None  # endpoint response body; None when no call was made


class ScreeningMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    llm: LlmMeta
    input_path: str
    input_count: int
    scored_count: int
    skipped_count: int
    llm_parse_failure_count: int = 0
    llm_timeout_count: int = 0
    llm_rejected_count: int = 0
