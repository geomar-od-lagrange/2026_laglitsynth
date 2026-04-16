from laglitsynth.models import _Base


class DeduplicationMeta(_Base):
    tool: str = "laglitsynth.catalogue_dedup.dedup"
    tool_version: str = "alpha"
    deduplicated_at: str
    input_count: int
    output_count: int
    duplicates_removed: int
    by_rule: dict[str, int]
