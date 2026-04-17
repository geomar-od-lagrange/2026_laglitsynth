from laglitsynth.models import _Base


class ScreeningVerdict(_Base):
    work_id: str
    relevance_score: int | None = None
    reason: str | None = None


class ScreeningMeta(_Base):
    tool: str = "laglitsynth.screening_abstracts.screen"
    tool_version: str = "alpha"
    prompt: str
    model: str
    threshold: int
    screened_at: str
    input_path: str
    input_count: int
    above_threshold_count: int
    below_threshold_count: int
    skipped_count: int
