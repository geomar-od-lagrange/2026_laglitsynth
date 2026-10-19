from pydantic import BaseModel, ConfigDict

from laglitsynth.models import RunMeta

TOOL_NAME = "laglitsynth.catalogue_dedup.dedup"
SAMPLE_TOOL_NAME = "laglitsynth.catalogue_dedup.sample"


class DroppedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dropped_work_id: str
    survived_work_id: str
    rule: str


class DeduplicationMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    input_count: int
    output_count: int
    duplicates_removed: int
    by_rule: dict[str, int]


class SampleMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    input_path: str
    input_count: int
    n: int
    seed: int
    output_count: int
