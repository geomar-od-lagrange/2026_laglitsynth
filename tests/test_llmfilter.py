"""Tests for the llmfilter submodule."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from laglitsynth.llmfilter.filter import (
    ClassifyError,
    classify_abstract,
    filter_works,
)
from laglitsynth.llmfilter.models import FilterMeta, FilterVerdict
from laglitsynth.openalex.models import Work


def _make_work(
    work_id: str = "https://openalex.org/W1",
    title: str = "Test Paper",
    abstract: str | None = "An abstract about ocean currents.",
) -> Work:
    """Create a minimal valid Work for testing."""
    return Work(
        id=work_id,
        title=title,
        abstract=abstract,
        authorships=[],
        biblio={},
        cited_by_count=0,
        referenced_works=[],
        keywords=[],
        topics=[],
    )


def _mock_openai_response(content: str) -> MagicMock:
    """Build a mock OpenAI chat completion response."""
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


# --- classify_abstract ---


def test_classify_abstract_valid_response() -> None:
    resp = _mock_openai_response('{"relevance_score": 85, "reason": "relevant"}')
    with patch("laglitsynth.llmfilter.filter.OpenAI") as mock_cls:
        mock_cls.return_value.chat.completions.create.return_value = resp
        verdict = classify_abstract(
            "W1", "some abstract", "about oceans", model="m", base_url="http://x"
        )
    assert verdict.work_id == "W1"
    assert verdict.relevance_score == 85
    assert verdict.reason == "relevant"
    assert verdict.accepted is False  # caller sets this


def test_classify_abstract_malformed_json() -> None:
    resp = _mock_openai_response("this is not json at all")
    with patch("laglitsynth.llmfilter.filter.OpenAI") as mock_cls:
        mock_cls.return_value.chat.completions.create.return_value = resp
        with pytest.raises(ClassifyError):
            classify_abstract(
                "W1", "abstract", "prompt", model="m", base_url="http://x"
            )


def test_classify_abstract_missing_fields() -> None:
    resp = _mock_openai_response('{"relevance_score": 50}')
    with patch("laglitsynth.llmfilter.filter.OpenAI") as mock_cls:
        mock_cls.return_value.chat.completions.create.return_value = resp
        with pytest.raises(ClassifyError):
            classify_abstract(
                "W1", "abstract", "prompt", model="m", base_url="http://x"
            )


def test_classify_abstract_wrong_types() -> None:
    resp = _mock_openai_response('{"relevance_score": "high", "reason": "good"}')
    with patch("laglitsynth.llmfilter.filter.OpenAI") as mock_cls:
        mock_cls.return_value.chat.completions.create.return_value = resp
        with pytest.raises(ClassifyError):
            classify_abstract(
                "W1", "abstract", "prompt", model="m", base_url="http://x"
            )


# --- filter_works ---


def _write_works_jsonl(path: Path, works: list[Work]) -> None:
    with open(path, "w") as f:
        for w in works:
            f.write(w.model_dump_json() + "\n")


def _mock_classify(
    results: dict[str, dict[str, Any]],
) -> Any:
    """Return a side_effect function for classify_abstract.

    results maps work_id to {"relevance_score": ..., "reason": ...}
    or "error" to raise ClassifyError.
    """

    def side_effect(
        work_id: str,
        abstract: str,
        prompt: str,
        *,
        model: str,
        base_url: str,
    ) -> FilterVerdict:
        entry = results[work_id]
        if entry == "error":
            raise ClassifyError(f"mock failure for {work_id}")
        return FilterVerdict(
            work_id=work_id,
            relevance_score=entry["relevance_score"],
            accepted=False,
            reason=entry["reason"],
        )

    return side_effect


def test_filter_works_basic(tmp_path: Path) -> None:
    works = [
        _make_work("W1", abstract="Ocean currents are fast."),
        _make_work("W2", abstract=None),
        _make_work("W3", abstract="Submesoscale dynamics."),
    ]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    classify_results = {
        "W1": {"relevance_score": 80, "reason": "relevant"},
        "W3": {"relevance_score": 30, "reason": "not relevant"},
    }

    with patch(
        "laglitsynth.llmfilter.filter.classify_abstract",
        side_effect=_mock_classify(classify_results),
    ):
        results = list(
            filter_works(
                tmp_path / "input.jsonl",
                "about oceans",
                model="m",
                base_url="http://x",
                threshold=50,
                max_records=None,
            )
        )

    assert len(results) == 3
    # W1: accepted
    assert results[0][1].accepted is True
    assert results[0][1].relevance_score == 80
    # W2: skipped (no abstract)
    assert results[1][1].accepted is None
    assert results[1][1].relevance_score is None
    # W3: rejected
    assert results[2][1].accepted is False
    assert results[2][1].relevance_score == 30


def test_filter_works_max_records_counts_all(tmp_path: Path) -> None:
    """max_records counts every work including those without abstracts."""
    works = [
        _make_work("W1", abstract=None),
        _make_work("W2", abstract="Has abstract."),
        _make_work("W3", abstract=None),
        _make_work("W4", abstract="Also has abstract."),
        _make_work("W5", abstract="Third abstract."),
    ]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    classify_results = {
        "W2": {"relevance_score": 90, "reason": "yes"},
        "W4": {"relevance_score": 10, "reason": "no"},
        "W5": {"relevance_score": 70, "reason": "yes"},
    }

    with patch(
        "laglitsynth.llmfilter.filter.classify_abstract",
        side_effect=_mock_classify(classify_results),
    ):
        results = list(
            filter_works(
                tmp_path / "input.jsonl",
                "prompt",
                model="m",
                base_url="http://x",
                threshold=50,
                max_records=3,
            )
        )

    # Should get W1 (skipped), W2 (classified), W3 (skipped) = 3 total
    assert len(results) == 3
    assert [r[0].id for r in results] == ["W1", "W2", "W3"]


def test_filter_works_llm_failure(tmp_path: Path) -> None:
    """LLM parse failure yields verdict with None fields."""
    works = [
        _make_work("W1", abstract="Good abstract."),
        _make_work("W2", abstract="Another abstract."),
    ]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    classify_results: dict[str, Any] = {
        "W1": "error",
        "W2": {"relevance_score": 75, "reason": "ok"},
    }

    with patch(
        "laglitsynth.llmfilter.filter.classify_abstract",
        side_effect=_mock_classify(classify_results),
    ):
        results = list(
            filter_works(
                tmp_path / "input.jsonl",
                "prompt",
                model="m",
                base_url="http://x",
                threshold=50,
                max_records=None,
            )
        )

    assert len(results) == 2
    # W1: LLM failure
    assert results[0][1].accepted is None
    assert results[0][1].relevance_score is None
    # W2: classified normally
    assert results[1][1].accepted is True
    assert results[1][1].relevance_score == 75


# --- FilterVerdict model ---


def test_filter_verdict_defaults() -> None:
    v = FilterVerdict(work_id="W1")
    assert v.relevance_score is None
    assert v.accepted is None
    assert v.reason is None


def test_filter_verdict_extra_fields_ignored() -> None:
    v = FilterVerdict(
        work_id="W1",
        relevance_score=50,
        accepted=True,
        reason="ok",
        bonus_field="should be ignored",  # type: ignore[call-arg]
    )
    assert not hasattr(v, "bonus_field")


# --- FilterMeta model ---


def test_filter_meta_serialization() -> None:
    meta = FilterMeta(
        prompt="test",
        model="gemma3:4b",
        threshold=50,
        filtered_at="2026-01-01T00:00:00",
        accepted_count=10,
        rejected_count=5,
        skipped_count=2,
    )
    data = meta.model_dump()
    assert data["tool"] == "laglitsynth.llmfilter.filter"
    assert data["accepted_count"] == 10


# --- _preflight ---


def test_preflight_raises_on_connection_failure() -> None:
    from laglitsynth.llmfilter.filter import _preflight

    args = MagicMock()
    args.base_url = "http://localhost:99999"
    args.model = "nonexistent"

    with patch("laglitsynth.llmfilter.filter.OpenAI") as mock_cls:
        mock_cls.return_value.models.retrieve.side_effect = Exception("connection refused")
        with pytest.raises(SystemExit):
            _preflight(args)


# --- run() end-to-end with mocked LLM ---


def test_run_dry_run(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    works = [
        _make_work("W1", abstract="Ocean currents."),
        _make_work("W2", abstract="Forest ecology."),
    ]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    classify_results = {
        "W1": {"relevance_score": 90, "reason": "yes"},
        "W2": {"relevance_score": 20, "reason": "no"},
    }

    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.prompt = "about oceans"
    args.output = None
    args.model = "m"
    args.base_url = "http://x"
    args.threshold = 50
    args.max_records = None
    args.dry_run = True
    args.reject_file = None

    with (
        patch("laglitsynth.llmfilter.filter._preflight"),
        patch(
            "laglitsynth.llmfilter.filter.classify_abstract",
            side_effect=_mock_classify(classify_results),
        ),
    ):
        from laglitsynth.llmfilter.filter import run

        run(args)

    # No output files should be created
    assert not (tmp_path / "output.jsonl").exists()


def test_run_writes_output_files(tmp_path: Path) -> None:
    works = [
        _make_work("W1", abstract="Ocean currents."),
        _make_work("W2", abstract=None),
        _make_work("W3", abstract="Forest ecology."),
    ]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    classify_results = {
        "W1": {"relevance_score": 90, "reason": "yes"},
        "W3": {"relevance_score": 20, "reason": "no"},
    }

    output = tmp_path / "output.jsonl"
    reject = tmp_path / "rejected.jsonl"

    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.prompt = "about oceans"
    args.output = output
    args.model = "m"
    args.base_url = "http://x"
    args.threshold = 50
    args.max_records = None
    args.dry_run = False
    args.reject_file = reject

    with (
        patch("laglitsynth.llmfilter.filter._preflight"),
        patch(
            "laglitsynth.llmfilter.filter.classify_abstract",
            side_effect=_mock_classify(classify_results),
        ),
    ):
        from laglitsynth.llmfilter.filter import run

        run(args)

    # Output should have accepted works only
    accepted_lines = output.read_text().strip().splitlines()
    assert len(accepted_lines) == 1
    assert "W1" in accepted_lines[0]

    # Reject file should have rejected works
    rejected_lines = reject.read_text().strip().splitlines()
    assert len(rejected_lines) == 1
    assert "W3" in rejected_lines[0]

    # Verdicts should have all processed works
    verdicts_path = output.with_suffix(".verdicts.jsonl")
    verdict_lines = verdicts_path.read_text().strip().splitlines()
    assert len(verdict_lines) == 3

    # Meta should exist with correct counts
    meta_path = output.with_suffix(".meta.json")
    assert meta_path.exists()
    import json

    meta = json.loads(meta_path.read_text())
    assert meta["accepted_count"] == 1
    assert meta["rejected_count"] == 1
    assert meta["skipped_count"] == 1
