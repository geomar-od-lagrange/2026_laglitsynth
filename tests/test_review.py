"""Tests for ``laglitsynth.review`` — ReviewConfig + the CFG_* emitter."""

from __future__ import annotations

import shlex
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from laglitsynth.review import (
    ReviewConfig,
    emit_cfg_lines,
    load_review_config,
)


def _write_yaml(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def _as_dict(lines: list[str]) -> dict[str, str]:
    """Parse ``CFG_NAME=<shell-quoted>`` lines into name -> unquoted value."""
    out: dict[str, str] = {}
    for line in lines:
        name, _, rhs = line.partition("=")
        # shlex.split reverses shlex.quote; empty '' -> [''] -> ''.
        parts = shlex.split(rhs)
        out[name] = parts[0] if parts else ""
    return out


def test_minimal_query_only_validates() -> None:
    config = ReviewConfig.model_validate({"query": "hello world"})
    assert config.query == "hello world"
    # All optional knobs default to None / empty nested models.
    assert config.from_year is None
    assert config.max_records is None
    assert config.screening_criteria is None
    assert config.thresholds.retrieval is None
    assert config.models.screening is None
    assert config.num_ctx.eligibility is None
    assert config.concurrency.llm is None


def test_extra_top_level_key_rejected() -> None:
    with pytest.raises(ValidationError):
        ReviewConfig.model_validate({"query": "x", "bogus": 1})


def test_extra_nested_key_rejected() -> None:
    with pytest.raises(ValidationError):
        ReviewConfig.model_validate({"query": "x", "thresholds": {"bogus": 1}})


def test_emit_full_config_round_trips(tmp_path: Path) -> None:
    """A fully-populated config emits every CFG_* with the right value."""
    cfg = _write_yaml(
        tmp_path / "review.yaml",
        {
            "query": "Lagrangian particle tracking",
            "from_year": 2000,
            "to_year": 2025,
            "max_records": 500,
            "screening_criteria": "screen.yaml",
            "eligibility_criteria": "elig.yaml",
            "codebook": "book.yaml",
            "thresholds": {"retrieval": 40.0, "eligibility": 60.0},
            "models": {
                "screening": "gemma3:4b",
                "eligibility": "gemma3:4b",
                "extraction": "llama3.1:8b",
            },
            "num_ctx": {"eligibility": 32768, "extraction": 16384},
            "concurrency": {"llm": 4, "extraction": 1},
            "export_gap": True,
        },
    )
    parsed = _as_dict(emit_cfg_lines(load_review_config(cfg)))

    assert parsed["CFG_QUERY"] == "Lagrangian particle tracking"
    assert parsed["CFG_FROM_YEAR"] == "2000"
    assert parsed["CFG_TO_YEAR"] == "2025"
    assert parsed["CFG_MAX_RECORDS"] == "500"
    # Criteria paths resolved absolute against the config's directory.
    assert parsed["CFG_SCREENING_CRITERIA"] == str((tmp_path / "screen.yaml").resolve())
    assert parsed["CFG_ELIGIBILITY_CRITERIA"] == str((tmp_path / "elig.yaml").resolve())
    assert parsed["CFG_CODEBOOK"] == str((tmp_path / "book.yaml").resolve())
    assert parsed["CFG_RETRIEVAL_THRESHOLD"] == "40.0"
    assert parsed["CFG_ELIGIBILITY_THRESHOLD"] == "60.0"
    assert parsed["CFG_SCREENING_MODEL"] == "gemma3:4b"
    assert parsed["CFG_ELIGIBILITY_MODEL"] == "gemma3:4b"
    assert parsed["CFG_EXTRACTION_MODEL"] == "llama3.1:8b"
    assert parsed["CFG_ELIGIBILITY_NUM_CTX"] == "32768"
    assert parsed["CFG_EXTRACTION_NUM_CTX"] == "16384"
    assert parsed["CFG_LLM_CONCURRENCY"] == "4"
    assert parsed["CFG_EXTRACTION_CONCURRENCY"] == "1"
    assert parsed["CFG_EXPORT_GAP"] == "1"


def test_export_gap_false_emits_zero(tmp_path: Path) -> None:
    """A bool renders as 1/0, the form the runner's numeric flags take."""
    cfg = _write_yaml(tmp_path / "review.yaml", {"query": "x", "export_gap": False})
    parsed = _as_dict(emit_cfg_lines(load_review_config(cfg)))

    assert parsed["CFG_EXPORT_GAP"] == "0"


def test_emit_none_fields_are_empty(tmp_path: Path) -> None:
    """Absent optional knobs emit an empty value for the runner to fill."""
    cfg = _write_yaml(tmp_path / "review.yaml", {"query": "x"})
    parsed = _as_dict(emit_cfg_lines(load_review_config(cfg)))

    assert parsed["CFG_QUERY"] == "x"
    for name in (
        "CFG_FROM_YEAR",
        "CFG_TO_YEAR",
        "CFG_MAX_RECORDS",
        "CFG_SCREENING_CRITERIA",
        "CFG_ELIGIBILITY_CRITERIA",
        "CFG_CODEBOOK",
        "CFG_RETRIEVAL_THRESHOLD",
        "CFG_ELIGIBILITY_THRESHOLD",
        "CFG_SCREENING_MODEL",
        "CFG_ELIGIBILITY_MODEL",
        "CFG_EXTRACTION_MODEL",
        "CFG_ELIGIBILITY_NUM_CTX",
        "CFG_EXTRACTION_NUM_CTX",
        "CFG_LLM_CONCURRENCY",
        "CFG_EXTRACTION_CONCURRENCY",
    ):
        assert parsed[name] == "", name
    # Empty value is literally `''` in the raw line so `eval` is safe.
    raw = {line.split("=", 1)[0]: line.split("=", 1)[1] for line in
           emit_cfg_lines(load_review_config(cfg))}
    assert raw["CFG_FROM_YEAR"] == "''"


def test_query_with_spaces_and_quotes_is_shell_safe() -> None:
    """A query with shell metacharacters survives quoting + re-parse."""
    query = "particle 'tracking' & \"dispersion\""
    config = ReviewConfig.model_validate({"query": query})
    lines = emit_cfg_lines(config)
    cfg_query_line = next(line for line in lines if line.startswith("CFG_QUERY="))
    rhs = cfg_query_line.split("=", 1)[1]
    # shlex.split must recover the exact original string.
    assert shlex.split(rhs) == [query]


def test_absolute_criteria_path_left_alone(tmp_path: Path) -> None:
    abs_path = (tmp_path / "elsewhere" / "screen.yaml").resolve()
    cfg = _write_yaml(
        tmp_path / "review.yaml",
        {"query": "x", "screening_criteria": str(abs_path)},
    )
    config = load_review_config(cfg)
    assert config.screening_criteria == abs_path


def test_load_rejects_non_mapping(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a top-level mapping"):
        load_review_config(bad)


def test_cli_subcommand_prints_cfg_lines(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from laglitsynth.cli import main

    cfg = _write_yaml(
        tmp_path / "review.yaml", {"query": "hello", "max_records": 7}
    )
    main(["review-config", str(cfg)])
    out = capsys.readouterr().out.splitlines()
    parsed = _as_dict(out)
    assert parsed["CFG_QUERY"] == "hello"
    assert parsed["CFG_MAX_RECORDS"] == "7"
    assert parsed["CFG_SCREENING_MODEL"] == ""


def test_shipped_example_config_validates() -> None:
    """The committed default review config validates and emits cleanly."""
    example = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "reviews"
        / "lagrangian-oceanography.yaml"
    )
    parsed = _as_dict(emit_cfg_lines(load_review_config(example)))
    assert parsed["CFG_QUERY"] == "Lagrangian particle tracking"
    assert parsed["CFG_MAX_RECORDS"] == "5"
    assert parsed["CFG_SCREENING_CRITERIA"].endswith(
        "screening-criteria/lagrangian-oceanography.yaml"
    )
