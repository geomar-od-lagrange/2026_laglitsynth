from typing import Literal

from laglitsynth.models import _Base


class AdjudicationVerdict(_Base):
    work_id: str
    decision: Literal["accept", "reject", "skip"]
    reviewer: str
    adjudicated_at: str
    reason: str | None = None


class AdjudicationMeta(_Base):
    tool: str = "laglitsynth.screening_adjudication.adjudicate"
    tool_version: str = "alpha"
    adjudicated_at: str
    threshold: int
    input_count: int
    accepted_count: int
    rejected_count: int
