from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from laglitsynth.models import LlmMeta, RunMeta

TOOL_NAME = "laglitsynth.fulltext_eligibility.assess"


SourceBasis = Literal["full_text", "abstract_only", "none"]


class _EligibilityPayload(BaseModel):
    """LLM-fillable content fields of an ``EligibilityVerdict``.

    Used to validate the raw LLM response before composing the full
    verdict. ``mode="before"`` validator drops unknown keys (LLMs at
    t>0 sprinkle extras) and coerces non-bool ``eligible`` / non-str
    ``reason`` into the contract the schema advertises. Widening
    happens only at the ingestion boundary; downstream consumers see
    the clean shape.
    """

    model_config = ConfigDict(extra="forbid")

    eligible: bool
    reason: str

    @model_validator(mode="before")
    @classmethod
    def _coerce_llm_types(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        known = set(cls.model_fields)
        coerced: dict[str, Any] = {}
        for key, value in data.items():
            if key not in known:
                continue
            if key == "reason":
                if value is None or isinstance(value, str):
                    coerced[key] = value if value is not None else ""
                elif isinstance(value, list):
                    coerced[key] = " / ".join(str(item) for item in value)
                else:
                    coerced[key] = str(value)
            else:
                # eligible: leave for pydantic's str→bool coercion on
                # "true"/"false"; reject on anything exotic below by
                # passing through unchanged.
                coerced[key] = value
        return coerced


class EligibilityVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    eligible: bool | None = None  # None for sentinel-reason skips
    source_basis: SourceBasis
    reason: str | None = None  # LLM free-text or sentinel
    seed: int | None = None  # Ollama seed used for this call; None for sentinel reasons
    raw_response: str | None = None  # LLM's raw message content; None when no call was made


class EligibilityMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    llm: LlmMeta
    input_catalogue: str
    input_extractions: str
    input_count: int
    eligible_count: int
    excluded_count: int
    no_source_count: int
    tei_parse_failure_count: int
    llm_parse_failure_count: int
    by_source_basis: dict[str, int]
