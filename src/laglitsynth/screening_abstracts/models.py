from laglitsynth.models import _Base


class FilterVerdict(_Base):
    work_id: str
    relevance_score: int | None = None
    accepted: bool | None = None
    reason: str | None = None


class FilterMeta(_Base):
    tool: str = "laglitsynth.screening_abstracts.screen"
    tool_version: str = "alpha"
    prompt: str
    model: str
    threshold: int
    filtered_at: str
    accepted_count: int
    rejected_count: int
    skipped_count: int
