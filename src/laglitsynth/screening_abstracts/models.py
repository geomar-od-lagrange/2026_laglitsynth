from pydantic import BaseModel, ConfigDict

from laglitsynth.models import _LlmMeta, _RunMeta

TOOL_NAME = "laglitsynth.screening_abstracts.screen"


class ScreeningVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    relevance_score: int | None = None
    reason: str | None = None
    seed: int | None = None  # Ollama seed used for this call; None for sentinel reasons
    raw_response: str | None = None  # LLM's raw message content; None when no call was made


class ScreeningMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: _RunMeta
    llm: _LlmMeta
    threshold: int
    input_path: str
    input_count: int
    above_threshold_count: int
    below_threshold_count: int
    skipped_count: int
