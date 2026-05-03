"""Tests for the screening_adjudication submodule."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from laglitsynth.screening_adjudication.models import AdjudicationMeta, AdjudicationVerdict
from laglitsynth.screening_adjudication.adjudicate import run
from laglitsynth.screening_abstracts.models import ScreeningVerdict

from conftest import _make_work, _write_works_jsonl


def _write_verdicts_jsonl(path: Path, verdicts: list[ScreeningVerdict]) -> None:
    with open(path, "w") as f:
        for v in verdicts:
            f.write(v.model_dump_json() + "\n")


def _make_args(
    tmp_path: Path,
    verdicts_path: Path,
    catalogue_path: Path,
    threshold: int = 50,
) -> MagicMock:
    args = MagicMock()
    args.input = verdicts_path
    args.catalogue = catalogue_path
    args.screening_threshold = threshold
    args.output_dir = tmp_path / "out"
    return args


def test_pass_through_accepts_above_threshold(tmp_path: Path) -> None:
    works = [_make_work("https://openalex.org/W1"), _make_work("https://openalex.org/W2")]
    verdicts = [
        ScreeningVerdict(work_id="https://openalex.org/W1", relevance_score=80, reason="relevant"),
        ScreeningVerdict(work_id="https://openalex.org/W2", relevance_score=30, reason="not relevant"),
    ]
    catalogue_path = tmp_path / "catalogue.jsonl"
    verdicts_path = tmp_path / "verdicts.jsonl"
    _write_works_jsonl(catalogue_path, works)
    _write_verdicts_jsonl(verdicts_path, verdicts)

    args = _make_args(tmp_path, verdicts_path, catalogue_path, threshold=50)
    run(args)

    included_lines = (tmp_path / "out" / "included.jsonl").read_text().strip().splitlines()
    assert len(included_lines) == 1
    assert "W1" in included_lines[0]


def test_included_jsonl_matches_threshold(tmp_path: Path) -> None:
    """Two works, scores 70 and 30, threshold 50 — only first in included.jsonl."""
    works = [_make_work("https://openalex.org/W1"), _make_work("https://openalex.org/W2")]
    verdicts = [
        ScreeningVerdict(work_id="https://openalex.org/W1", relevance_score=70, reason="yes"),
        ScreeningVerdict(work_id="https://openalex.org/W2", relevance_score=30, reason="no"),
    ]
    catalogue_path = tmp_path / "catalogue.jsonl"
    verdicts_path = tmp_path / "verdicts.jsonl"
    _write_works_jsonl(catalogue_path, works)
    _write_verdicts_jsonl(verdicts_path, verdicts)

    args = _make_args(tmp_path, verdicts_path, catalogue_path, threshold=50)
    run(args)

    out_dir = tmp_path / "out"

    # included.jsonl has only W1
    included_lines = (out_dir / "included.jsonl").read_text().strip().splitlines()
    assert len(included_lines) == 1
    assert "W1" in included_lines[0]

    # verdicts.jsonl has exactly one AdjudicationVerdict with decision="accept" for W1
    verdict_lines = (out_dir / "verdicts.jsonl").read_text().strip().splitlines()
    assert len(verdict_lines) == 1
    v = json.loads(verdict_lines[0])
    assert v["work_id"] == "https://openalex.org/W1"
    assert v["decision"] == "accept"


def test_adjudication_verdict_sidecar_written(tmp_path: Path) -> None:
    works = [_make_work("https://openalex.org/W1"), _make_work("https://openalex.org/W2")]
    verdicts = [
        ScreeningVerdict(work_id="https://openalex.org/W1", relevance_score=80, reason="yes"),
        ScreeningVerdict(work_id="https://openalex.org/W2", relevance_score=20, reason="no"),
    ]
    catalogue_path = tmp_path / "catalogue.jsonl"
    verdicts_path = tmp_path / "verdicts.jsonl"
    _write_works_jsonl(catalogue_path, works)
    _write_verdicts_jsonl(verdicts_path, verdicts)

    args = _make_args(tmp_path, verdicts_path, catalogue_path, threshold=50)
    run(args)

    out_dir = tmp_path / "out"
    verdict_lines = (out_dir / "verdicts.jsonl").read_text().strip().splitlines()
    assert len(verdict_lines) == 1
    v = AdjudicationVerdict.model_validate_json(verdict_lines[0])
    assert v.decision == "accept"
    assert v.reviewer == "pass-through"
    assert v.reason is None


def test_meta_correctness(tmp_path: Path) -> None:
    works = [_make_work("https://openalex.org/W1"), _make_work("https://openalex.org/W2")]
    verdicts = [
        ScreeningVerdict(work_id="https://openalex.org/W1", relevance_score=80, reason="yes"),
        ScreeningVerdict(work_id="https://openalex.org/W2", relevance_score=20, reason="no"),
    ]
    catalogue_path = tmp_path / "catalogue.jsonl"
    verdicts_path = tmp_path / "verdicts.jsonl"
    _write_works_jsonl(catalogue_path, works)
    _write_verdicts_jsonl(verdicts_path, verdicts)

    args = _make_args(tmp_path, verdicts_path, catalogue_path, threshold=50)
    run(args)

    meta_data = json.loads((tmp_path / "out" / "adjudication-meta.json").read_text())
    meta = AdjudicationMeta.model_validate(meta_data)
    assert meta.input_count == 2
    assert meta.accepted_count == 1
    assert meta.rejected_count == 0
    assert meta.missing_in_catalogue == 0
    assert meta.threshold == 50
    assert meta.run.tool == "laglitsynth.screening_adjudication.adjudicate"
    assert meta.run.validation_skipped == 0


def test_empty_input(tmp_path: Path) -> None:
    _write_works_jsonl(tmp_path / "catalogue.jsonl", [])
    _write_verdicts_jsonl(tmp_path / "verdicts.jsonl", [])

    args = _make_args(tmp_path, tmp_path / "verdicts.jsonl", tmp_path / "catalogue.jsonl")
    run(args)

    included_lines = (tmp_path / "out" / "included.jsonl").read_text().strip()
    assert included_lines == ""

    meta_data = json.loads((tmp_path / "out" / "adjudication-meta.json").read_text())
    assert meta_data["input_count"] == 0
    assert meta_data["accepted_count"] == 0


def test_includes_null_score_sentinels(tmp_path: Path) -> None:
    """Sentinels with relevance_score=None ride through to fulltext-retrieval.

    no-abstract / llm-parse-failure / llm-timeout are not evidence of
    irrelevance; only an explicit below-threshold numeric score excludes.
    """
    works = [
        _make_work("https://openalex.org/W1"),
        _make_work("https://openalex.org/W2"),
        _make_work("https://openalex.org/W3"),
        _make_work("https://openalex.org/W4"),
        _make_work("https://openalex.org/W5"),
    ]
    verdicts = [
        ScreeningVerdict(
            work_id="https://openalex.org/W1",
            relevance_score=None,
            reason="no-abstract",
        ),
        ScreeningVerdict(
            work_id="https://openalex.org/W2",
            relevance_score=None,
            reason="llm-parse-failure",
        ),
        ScreeningVerdict(
            work_id="https://openalex.org/W3",
            relevance_score=None,
            reason="llm-timeout",
        ),
        ScreeningVerdict(
            work_id="https://openalex.org/W4", relevance_score=80, reason="yes"
        ),
        ScreeningVerdict(
            work_id="https://openalex.org/W5", relevance_score=10, reason="no"
        ),
    ]
    catalogue_path = tmp_path / "catalogue.jsonl"
    verdicts_path = tmp_path / "verdicts.jsonl"
    _write_works_jsonl(catalogue_path, works)
    _write_verdicts_jsonl(verdicts_path, verdicts)

    args = _make_args(tmp_path, verdicts_path, catalogue_path, threshold=50)
    run(args)

    included_lines = (
        (tmp_path / "out" / "included.jsonl").read_text().strip().splitlines()
    )
    # W1, W2, W3 ride through; W4 above threshold; W5 below threshold excluded.
    assert len(included_lines) == 4
    ids = {json.loads(line)["id"] for line in included_lines}
    assert ids == {
        "https://openalex.org/W1",
        "https://openalex.org/W2",
        "https://openalex.org/W3",
        "https://openalex.org/W4",
    }

    meta_data = json.loads((tmp_path / "out" / "adjudication-meta.json").read_text())
    meta = AdjudicationMeta.model_validate(meta_data)
    assert meta.accepted_count == 4
    assert meta.accepted_null_score_count == 3


def test_missing_in_catalogue_counted(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Verdicts above threshold without a matching catalogue entry are counted and warned."""
    works = [_make_work("https://openalex.org/W1")]
    verdicts = [
        ScreeningVerdict(work_id="https://openalex.org/W1", relevance_score=80, reason="yes"),
        ScreeningVerdict(work_id="https://openalex.org/W_MISSING", relevance_score=90, reason="yes"),
    ]
    catalogue_path = tmp_path / "catalogue.jsonl"
    verdicts_path = tmp_path / "verdicts.jsonl"
    _write_works_jsonl(catalogue_path, works)
    _write_verdicts_jsonl(verdicts_path, verdicts)

    args = _make_args(tmp_path, verdicts_path, catalogue_path, threshold=50)
    with caplog.at_level("WARNING", logger="laglitsynth.screening_adjudication.adjudicate"):
        run(args)

    meta_data = json.loads((tmp_path / "out" / "adjudication-meta.json").read_text())
    meta = AdjudicationMeta.model_validate(meta_data)
    assert meta.input_count == 2
    assert meta.accepted_count == 1
    assert meta.missing_in_catalogue == 1

    included_lines = (tmp_path / "out" / "included.jsonl").read_text().strip().splitlines()
    assert len(included_lines) == 1
    assert "W1" in included_lines[0]

    assert any("W_MISSING" in rec.message for rec in caplog.records)


def test_rerun_overwrites(tmp_path: Path) -> None:
    """Running stage 4 twice overwrites verdicts.jsonl and included.jsonl (no FileExistsError)."""
    works = [_make_work("https://openalex.org/W1")]
    verdicts = [
        ScreeningVerdict(work_id="https://openalex.org/W1", relevance_score=80, reason="yes"),
    ]
    catalogue_path = tmp_path / "catalogue.jsonl"
    verdicts_path = tmp_path / "verdicts.jsonl"
    _write_works_jsonl(catalogue_path, works)
    _write_verdicts_jsonl(verdicts_path, verdicts)

    args = _make_args(tmp_path, verdicts_path, catalogue_path)
    run(args)
    # Second run should not raise
    run(args)

    included_lines = (tmp_path / "out" / "included.jsonl").read_text().strip().splitlines()
    assert len(included_lines) == 1
