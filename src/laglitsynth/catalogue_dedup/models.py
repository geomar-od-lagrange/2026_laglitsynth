from pydantic import BaseModel, ConfigDict

from laglitsynth.models import _RunMeta

TOOL_NAME = "laglitsynth.catalogue_dedup.dedup"


class DeduplicationMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: _RunMeta
    input_count: int
    output_count: int
    duplicates_removed: int
    by_rule: dict[str, int]
