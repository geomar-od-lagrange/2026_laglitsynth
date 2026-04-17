"""Pydantic models for the extraction codebook stage.

``ExtractionRecord`` translates the seed codebook table in
[docs/codebook.md](../../../docs/codebook.md) into a typed record: every
value field is paired with a ``*_context: str | None`` verbatim snippet,
and all content fields are ``str | None`` so the prototype does not
prematurely commit to enum categories.

``_ExtractionPayload`` is a narrower submodel containing only the
LLM-fillable content fields. The caller fills the identification block
(``work_id``, ``source_basis``, ``reason``, ``seed``, ``truncated``).
``ExtractionCodebookMeta`` records per-branch counts so operators can
see how each work was handled without replaying the cascade.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator

from laglitsynth.models import _LlmMeta, _RunMeta

TOOL_NAME = "laglitsynth.extraction_codebook.extract"


SourceBasis = Literal["full_text", "abstract_only", "none"]


class _ExtractionPayload(BaseModel):
    """LLM-fillable content fields of an ``ExtractionRecord``.

    Used to validate the raw LLM response before composing the full
    record. Keeping this as a separate submodel lets the prompt's field
    list be generated from the same schema the validator applies.

    All content fields default to ``None`` so a partial LLM response
    parses cleanly — a single malformed field cannot discard the other
    29. A ``mode="before"`` validator coerces common type mismatches
    (bool, list, numeric) into the ``str | None`` contract the schema
    advertises. Downstream consumers see only ``str | None``; widening
    happens at the ingestion boundary.
    """

    model_config = ConfigDict(extra="forbid")

    # Tagging (free-text, consolidated later).
    sub_discipline: str | None = None
    sub_discipline_context: str | None = None

    # RQ1.2 — numerical choices.
    integration_scheme: str | None = None
    integration_scheme_context: str | None = None
    time_step_strategy: str | None = None
    time_step_strategy_context: str | None = None
    time_step_value: str | None = None
    time_step_value_context: str | None = None
    interpolation_spatial: str | None = None
    interpolation_spatial_context: str | None = None
    interpolation_temporal: str | None = None
    interpolation_temporal_context: str | None = None
    diffusion_scheme: str | None = None
    diffusion_scheme_context: str | None = None
    software: str | None = None
    software_context: str | None = None
    ocean_model: str | None = None
    ocean_model_context: str | None = None

    # RQ1.1 — reproducibility.
    methods_detail: str | None = None
    methods_detail_context: str | None = None
    code_tracking_software: str | None = None
    code_tracking_software_context: str | None = None
    code_experiment_setup: str | None = None
    code_experiment_setup_context: str | None = None
    code_analysis: str | None = None
    code_analysis_context: str | None = None
    config_available: str | None = None
    config_available_context: str | None = None

    # Extraction metadata.
    passage_locations: str | None = None
    extraction_notes: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_llm_types(cls, data: Any) -> Any:
        """Coerce natural-JSON LLM responses into the ``str | None`` contract.

        LLMs frequently answer a field called ``config_available`` with a
        JSON bool, or ``passage_locations`` with a list. Rather than fail
        the whole record on a type mismatch, flatten to string at ingest.
        ``None`` and empty lists pass through as ``None``.
        """
        if not isinstance(data, dict):
            return data
        coerced: dict[str, Any] = {}
        for key, value in data.items():
            if value is None or isinstance(value, str):
                coerced[key] = value
            elif isinstance(value, bool):  # must precede int; bool is an int subclass
                coerced[key] = "yes" if value else "no"
            elif isinstance(value, list):
                coerced[key] = (
                    " / ".join(str(item) for item in value) if value else None
                )
            else:
                coerced[key] = str(value)
        return coerced


class ExtractionRecord(BaseModel):
    """One codebook record per input work, successes and failures alike."""

    model_config = ConfigDict(extra="forbid")

    # Identification.
    work_id: str
    source_basis: SourceBasis
    reason: str | None  # sentinel for skips; None for normal records
    seed: int | None
    truncated: bool

    # Tagging (free-text, consolidated later).
    sub_discipline: str | None
    sub_discipline_context: str | None

    # RQ1.2 — numerical choices.
    integration_scheme: str | None
    integration_scheme_context: str | None
    time_step_strategy: str | None
    time_step_strategy_context: str | None
    time_step_value: str | None
    time_step_value_context: str | None
    interpolation_spatial: str | None
    interpolation_spatial_context: str | None
    interpolation_temporal: str | None
    interpolation_temporal_context: str | None
    diffusion_scheme: str | None
    diffusion_scheme_context: str | None
    software: str | None
    software_context: str | None
    ocean_model: str | None
    ocean_model_context: str | None

    # RQ1.1 — reproducibility.
    methods_detail: str | None
    methods_detail_context: str | None
    code_tracking_software: str | None
    code_tracking_software_context: str | None
    code_experiment_setup: str | None
    code_experiment_setup_context: str | None
    code_analysis: str | None
    code_analysis_context: str | None
    config_available: str | None
    config_available_context: str | None

    # Extraction metadata.
    passage_locations: str | None
    extraction_notes: str | None


class ExtractionCodebookMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: _RunMeta
    llm: _LlmMeta
    input_catalogue: str
    input_extractions: str
    input_count: int
    full_text_count: int
    abstract_only_count: int
    skipped_count: int  # no-source + tei-parse-failure
    llm_parse_failure_count: int
    truncated_count: int
    by_source_basis: dict[str, int]
