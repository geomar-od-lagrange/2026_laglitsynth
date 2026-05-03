"""Tests for the data-driven codebook loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from laglitsynth.extraction_codebook.codebook import (
    CodebookSpec,
    load_codebook,
    render_field_list,
    render_system_prompt,
)

DEFAULT_CODEBOOK_PATH = Path("examples/codebooks/lagrangian-oceanography.yaml")

# Snapshot of the assembled stage-8 system prompt against the shipped
# codebook YAML. Pinning this string is how transcription bugs in the
# YAML show up — a stray character shift in a description would change
# the rendered prompt and the downstream prompt_sha256.
EXPECTED_SYSTEM_PROMPT = '''You extract structured metadata from scientific papers on computational
Lagrangian methods in oceanography. You will be shown the text of one
paper and must fill a JSON object with the fields below. Every value
field has a companion "*_context" field containing a short verbatim
excerpt from the paper supporting that value. If the paper does not
state the information, write null for both the value and its context —
do not guess or infer.

Fields:
- "sub_discipline": Sub-discipline tag. Free text — use the paper\'s own phrasing (e.g. \'water parcels\', \'tracers\', \'objects\', \'passive debris tracking\', \'larval connectivity\'). Not a fixed taxonomy.
- "sub_discipline_context": Short verbatim excerpt supporting sub_discipline.
- "integration_scheme": Numerical integration scheme used to advance particles (e.g. \'Euler forward\', \'RK4\', \'RK45 adaptive\').
- "integration_scheme_context": Short verbatim excerpt supporting integration_scheme.
- "time_step_strategy": How the time step is chosen (e.g. \'fixed\', \'adaptive CFL-based\', \'sub-stepped within ocean model step\'). Separate from time_step_value.
- "time_step_strategy_context": Short verbatim excerpt supporting time_step_strategy.
- "time_step_value": Numeric time step with units if stated (e.g. \'300s\', \'1h\', \'variable\'). Interpretable only alongside ocean_model resolution.
- "time_step_value_context": Short verbatim excerpt supporting time_step_value.
- "interpolation_spatial": Spatial interpolation scheme for velocity at particle positions (e.g. \'trilinear\', \'bicubic + linear vertical\').
- "interpolation_spatial_context": Short verbatim excerpt supporting interpolation_spatial.
- "interpolation_temporal": Temporal interpolation scheme between model snapshots (e.g. \'linear between snapshots\', \'none\'). Often unstated.
- "interpolation_temporal_context": Short verbatim excerpt supporting interpolation_temporal.
- "diffusion_scheme": Stochastic diffusion / random-walk scheme, if any (e.g. \'random walk\', \'Markov-1\', \'none\'). May not apply.
- "diffusion_scheme_context": Short verbatim excerpt supporting diffusion_scheme.
- "software": Particle-tracking software used (e.g. \'OceanParcels v2.4\', \'OpenDrift\', \'custom Fortran\').
- "software_context": Short verbatim excerpt supporting software.
- "ocean_model": Ocean circulation model whose velocity field drives the tracking (e.g. \'NEMO ORCA025\', \'MITgcm\', \'ROMS L3\').
- "ocean_model_context": Short verbatim excerpt supporting ocean_model.
- "methods_detail": Brief summary of how thoroughly the numerical methods are described in the paper. Describe quality, not content.
- "methods_detail_context": Short verbatim excerpt supporting methods_detail.
- "code_tracking_software": Availability statement for the particle-tracking software itself (e.g. \'yes, GitHub link\', \'named but not linked\', \'not mentioned\').
- "code_tracking_software_context": Short verbatim excerpt supporting code_tracking_software.
- "code_experiment_setup": Availability of experiment setup / preprocessing scripts (configuration, domain setup, forcing).
- "code_experiment_setup_context": Short verbatim excerpt supporting code_experiment_setup.
- "code_analysis": Availability of analysis scripts (post-processing, plotting, statistics).
- "code_analysis_context": Short verbatim excerpt supporting code_analysis.
- "config_available": Whether runtime parameters (domain, resolution, forcing) are given in the paper itself. Describe availability in words, not as a yes/no boolean.
- "config_available_context": Short verbatim excerpt supporting config_available.
- "in_text_locations": Where in THIS PAPER the extracted values were found — section headings, figure/table numbers, or nearby phrases that help a human reviewer locate them. Not the paper\'s subject matter; not geographic place names in the paper\'s content.
- "extraction_notes": What was ambiguous, surprising, or hard to classify about this paper\'s methods. Free text.

Respond with a single JSON object containing exactly these keys.'''


# Field list extracted from the same v1 prompt, used for granular asserts.
EXPECTED_FIELD_LIST = (
    EXPECTED_SYSTEM_PROMPT.split("Fields:\n", 1)[1]
    .split("\n\nRespond with a single JSON object", 1)[0]
)


def test_load_default_codebook_field_order_matches_snapshot() -> None:
    """Loaded model preserves the YAML's field order — paired contexts interleaved."""
    _spec, model = load_codebook(DEFAULT_CODEBOOK_PATH)
    expected = [
        line[len('- "') : line.index('"', 3)] for line in EXPECTED_FIELD_LIST.splitlines()
    ]
    assert list(model.model_fields) == expected


def test_render_field_list_byte_for_byte_against_snapshot() -> None:
    """``render_field_list`` against the shipped YAML reproduces the snapshot."""
    _spec, model = load_codebook(DEFAULT_CODEBOOK_PATH)
    assert render_field_list(model) == EXPECTED_FIELD_LIST


def test_render_system_prompt_byte_for_byte_against_snapshot() -> None:
    """Assembled system prompt matches the snapshot — guards against transcription bugs."""
    spec, model = load_codebook(DEFAULT_CODEBOOK_PATH)
    assert render_system_prompt(spec, model) == EXPECTED_SYSTEM_PROMPT


def test_payload_validates_typical_response() -> None:
    """A minimal valid LLM response validates and parses to None defaults."""
    _spec, model = load_codebook(DEFAULT_CODEBOOK_PATH)
    obj = model.model_validate({"sub_discipline": "tracers", "ocean_model": "NEMO"})
    assert obj.sub_discipline == "tracers"  # type: ignore[attr-defined]
    assert obj.ocean_model == "NEMO"  # type: ignore[attr-defined]
    assert obj.integration_scheme is None  # type: ignore[attr-defined]


def test_payload_coerces_bool_to_string() -> None:
    _spec, model = load_codebook(DEFAULT_CODEBOOK_PATH)
    obj = model.model_validate({"config_available": True})
    assert obj.config_available == "yes"  # type: ignore[attr-defined]


def test_payload_coerces_list_to_joined_string() -> None:
    _spec, model = load_codebook(DEFAULT_CODEBOOK_PATH)
    obj = model.model_validate({"in_text_locations": ["Section 2", "Fig 3"]})
    assert obj.in_text_locations == "Section 2 / Fig 3"  # type: ignore[attr-defined]


def test_payload_drops_unknown_keys() -> None:
    _spec, model = load_codebook(DEFAULT_CODEBOOK_PATH)
    obj = model.model_validate({"sub_discipline": "x", "confidence": 95})
    assert obj.sub_discipline == "x"  # type: ignore[attr-defined]


def test_codebook_spec_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        CodebookSpec.model_validate(
            {
                "id": "x",
                "system_prompt": "p",
                "fields": [],
                "bonus": "no",
            }
        )


def test_load_codebook_from_inlined_dict() -> None:
    spec_dict = {
        "id": "minimal",
        "system_prompt": "Fields:\n{fields}",
        "fields": [
            {"name": "alpha", "description": "First field."},
            {"name": "beta", "description": "Second field.", "context": False},
        ],
    }
    spec, model = load_codebook(spec_dict)
    assert spec.id == "minimal"
    # alpha + alpha_context + beta = 3 fields
    assert list(model.model_fields) == ["alpha", "alpha_context", "beta"]


def test_load_codebook_from_path() -> None:
    spec, _model = load_codebook(DEFAULT_CODEBOOK_PATH)
    assert spec.id == "lagrangian-oceanography"
