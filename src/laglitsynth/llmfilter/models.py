from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class FilterVerdict(_Base):
    work_id: str
    relevance_score: int
    accepted: bool
    reason: str
