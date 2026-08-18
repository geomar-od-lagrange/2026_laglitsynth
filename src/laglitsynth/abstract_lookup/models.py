from pydantic import BaseModel, ConfigDict

from laglitsynth.models import RunMeta

TOOL_NAME = "laglitsynth.abstract_lookup.lookup"

# Source labels recorded in AbstractRecord.source, in lookup order.
SOURCE_SEMANTIC_SCHOLAR = "semantic_scholar"
SOURCE_OPENALEX = "openalex"
SOURCE_CROSSREF = "crossref"


class AbstractRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    abstract: str | None = None
    # One of SOURCE_* above, or None when no source returned an abstract.
    source: str | None = None


class AbstractLookupMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    input_path: str
    input_count: int
    filled_count: int
    still_missing_count: int
    no_doi_count: int
    by_source: dict[str, int]  # counts per source label that yielded an abstract
