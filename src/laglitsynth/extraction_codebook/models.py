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

from pydantic import BaseModel, ConfigDict, Field, model_validator

from laglitsynth.models import LlmMeta, RunMeta

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
    sub_discipline: str | None = Field(
        default=None,
        description=(
            "Sub-discipline tag. Free text — use the paper's own phrasing "
            "(e.g. 'water parcels', 'tracers', 'objects', 'passive debris "
            "tracking', 'larval connectivity'). Not a fixed taxonomy."
        ),
    )
    sub_discipline_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt from the paper supporting the sub_discipline tag.",
    )

    # RQ1.2 — numerical choices.
    integration_scheme: str | None = Field(
        default=None,
        description=(
            "Numerical integration scheme used to advance particles "
            "(e.g. 'Euler forward', 'RK4', 'RK45 adaptive')."
        ),
    )
    integration_scheme_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt supporting integration_scheme.",
    )
    time_step_strategy: str | None = Field(
        default=None,
        description=(
            "How the time step is chosen (e.g. 'fixed', 'adaptive CFL-based', "
            "'sub-stepped within ocean model step'). Separate from time_step_value."
        ),
    )
    time_step_strategy_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt supporting time_step_strategy.",
    )
    time_step_value: str | None = Field(
        default=None,
        description=(
            "Numeric time step with units if stated (e.g. '300s', '1h', "
            "'variable'). Interpretable only alongside ocean_model resolution."
        ),
    )
    time_step_value_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt supporting time_step_value.",
    )
    interpolation_spatial: str | None = Field(
        default=None,
        description=(
            "Spatial interpolation scheme for velocity at particle positions "
            "(e.g. 'trilinear', 'bicubic + linear vertical')."
        ),
    )
    interpolation_spatial_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt supporting interpolation_spatial.",
    )
    interpolation_temporal: str | None = Field(
        default=None,
        description=(
            "Temporal interpolation scheme between model snapshots "
            "(e.g. 'linear between snapshots', 'none'). Often unstated."
        ),
    )
    interpolation_temporal_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt supporting interpolation_temporal.",
    )
    diffusion_scheme: str | None = Field(
        default=None,
        description=(
            "Stochastic diffusion / random-walk scheme, if any "
            "(e.g. 'random walk', 'Markov-1', 'none'). May not apply."
        ),
    )
    diffusion_scheme_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt supporting diffusion_scheme.",
    )
    software: str | None = Field(
        default=None,
        description=(
            "Particle-tracking software used "
            "(e.g. 'OceanParcels v2.4', 'OpenDrift', 'custom Fortran')."
        ),
    )
    software_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt supporting software.",
    )
    ocean_model: str | None = Field(
        default=None,
        description=(
            "Ocean circulation model whose velocity field drives the tracking "
            "(e.g. 'NEMO ORCA025', 'MITgcm', 'ROMS L3')."
        ),
    )
    ocean_model_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt supporting ocean_model.",
    )

    # RQ1.1 — reproducibility.
    methods_detail: str | None = Field(
        default=None,
        description=(
            "Brief summary of how thoroughly the numerical methods are "
            "described in the paper. Describe quality, not content."
        ),
    )
    methods_detail_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt supporting methods_detail.",
    )
    code_tracking_software: str | None = Field(
        default=None,
        description=(
            "Availability statement for the particle-tracking software itself "
            "(e.g. 'yes, GitHub link', 'named but not linked', 'not mentioned')."
        ),
    )
    code_tracking_software_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt supporting code_tracking_software.",
    )
    code_experiment_setup: str | None = Field(
        default=None,
        description=(
            "Availability of experiment setup / preprocessing scripts "
            "(configuration, domain setup, forcing)."
        ),
    )
    code_experiment_setup_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt supporting code_experiment_setup.",
    )
    code_analysis: str | None = Field(
        default=None,
        description=(
            "Availability of analysis scripts (post-processing, plotting, statistics)."
        ),
    )
    code_analysis_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt supporting code_analysis.",
    )
    config_available: str | None = Field(
        default=None,
        description=(
            "Whether runtime parameters (domain, resolution, forcing) are "
            "given in the paper itself. Describe availability in words, "
            "not as a yes/no boolean."
        ),
    )
    config_available_context: str | None = Field(
        default=None,
        description="Short verbatim excerpt supporting config_available.",
    )

    # Extraction metadata.
    in_text_locations: str | None = Field(
        default=None,
        description=(
            "Where in THIS PAPER the extracted values were found — section "
            "headings, figure/table numbers, or nearby phrases that help a "
            "human reviewer locate them. Not the paper's subject matter; "
            "not geographic place names in the paper's content."
        ),
    )
    extraction_notes: str | None = Field(
        default=None,
        description=(
            "What was ambiguous, surprising, or hard to classify about this "
            "paper's methods. Free text."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _coerce_llm_types(cls, data: Any) -> Any:
        """Coerce natural-JSON LLM responses into the ``str | None`` contract.

        LLMs frequently answer a field called ``config_available`` with a
        JSON bool, or ``in_text_locations`` with a list. Rather than fail
        the whole record on a type mismatch, flatten to string at ingest.
        ``None`` and empty lists pass through as ``None``.

        Unknown keys are dropped here rather than hitting ``extra="forbid"``
        on the model — LLMs at t>0 routinely sprinkle extras like
        ``"confidence"`` or ``"notes"``, and those should not nuke the
        whole record. The 30-field contract remains strict for the model
        shape itself; widening happens only at the ingestion boundary.
        """
        if not isinstance(data, dict):
            return data
        known = set(cls.model_fields)
        coerced: dict[str, Any] = {}
        for key, value in data.items():
            if key not in known:
                continue
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


class ExtractionRecord(_ExtractionPayload):
    """One codebook record per input work, successes and failures alike.

    Subclasses ``_ExtractionPayload`` so the 28 content fields are
    declared exactly once. This class adds only the identification and
    run-metadata fields that the caller fills; the LLM fills the rest.
    """

    model_config = ConfigDict(extra="forbid")

    # Identification block — filled by the caller, not the LLM.
    work_id: str
    source_basis: SourceBasis
    reason: str | None  # sentinel for skips; None for normal records
    seed: int | None
    truncated: bool
    raw_response: str | None = None  # LLM's raw message content; None when no call was made


class ExtractionCodebookMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    llm: LlmMeta
    input_catalogue: str
    input_extractions: str
    input_count: int
    full_text_count: int
    abstract_only_count: int
    skipped_count: int  # no-source + tei-parse-failure
    llm_parse_failure_count: int
    truncated_count: int
    by_source_basis: dict[str, int]
