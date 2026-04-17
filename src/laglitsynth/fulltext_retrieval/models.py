from enum import Enum

from pydantic import BaseModel, ConfigDict

from laglitsynth.models import _RunMeta

TOOL_NAME = "laglitsynth.fulltext_retrieval.retrieve"


class RetrievalStatus(str, Enum):
    retrieved_oa = "retrieved_oa"
    retrieved_unpaywall = "retrieved_unpaywall"
    retrieved_preprint = "retrieved_preprint"
    retrieved_manual = "retrieved_manual"
    abstract_only = "abstract_only"
    failed = "failed"


class RetrievalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    retrieval_status: RetrievalStatus
    source_url: str | None = None
    pdf_path: str | None = None
    error: str | None = None
    retrieved_at: str  # per-record wall-clock timestamp


class RetrievalMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: _RunMeta
    total_works: int
    retrieved_count: int
    abstract_only_count: int
    failed_count: int
    by_source: dict[str, int]
