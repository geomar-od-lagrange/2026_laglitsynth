from typing import Literal

from pydantic import BaseModel, ConfigDict

from laglitsynth.models import RunMeta

TOOL_NAME = "laglitsynth.screening_adjudication.adjudicate"


class AdjudicationVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    decision: Literal["accept"]
    reviewer: str
    adjudicated_at: str
    reason: str | None = None


class AdjudicationMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    threshold: int
    input_count: int
    accepted_count: int
    rejected_count: int
    missing_in_catalogue: int
