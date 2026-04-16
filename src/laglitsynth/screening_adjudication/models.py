from laglitsynth.models import _Base


class AdjudicationMeta(_Base):
    tool: str = "laglitsynth.screening_adjudication.adjudicate"
    tool_version: str = "alpha"
    adjudicated_at: str
    mode: str = "pass_through"
    input_count: int
    output_count: int
    human_reviewed: int = 0
