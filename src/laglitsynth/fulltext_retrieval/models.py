from enum import Enum

from laglitsynth.models import _Base


class RetrievalStatus(str, Enum):
    retrieved_oa = "retrieved_oa"
    retrieved_unpaywall = "retrieved_unpaywall"
    retrieved_preprint = "retrieved_preprint"
    retrieved_manual = "retrieved_manual"
    abstract_only = "abstract_only"
    failed = "failed"


class RetrievalRecord(_Base):
    work_id: str
    retrieval_status: RetrievalStatus
    source_url: str | None = None
    pdf_path: str | None = None
    error: str | None = None
    retrieved_at: str


class RetrievalMeta(_Base):
    tool: str = "laglitsynth.fulltext_retrieval.retrieve"
    tool_version: str = "alpha"
    retrieved_at: str
    total_works: int
    retrieved_count: int
    abstract_only_count: int
    failed_count: int
    by_source: dict[str, int]
