"""Tests for the screening_abstracts submodule."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from laglitsynth.screening_abstracts.screen import (
    ClassifyError,
    classify_abstract,
    screen_works,
)
from laglitsynth.screening_abstracts.models import ScreeningMeta, ScreeningVerdict
from laglitsynth.catalogue_fetch.models import Work


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
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = resp
    verdict = classify_abstract(
        "W1", "some abstract", "about oceans", model="m", base_url="http://x", client=mock_client
    )
    assert verdict.work_id == "W1"
    assert verdict.relevance_score == 85
    assert verdict.reason == "relevant"


def test_classify_abstract_malformed_json() -> None:
    resp = _mock_openai_response("this is not json at all")
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = resp
    with pytest.raises(ClassifyError):
        classify_abstract(
            "W1", "abstract", "prompt", model="m", base_url="http://x", client=mock_client
        )


def test_classify_abstract_missing_fields() -> None:
    resp = _mock_openai_response('{"relevance_score": 50}')
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = resp
    with pytest.raises(ClassifyError):
        classify_abstract(
            "W1", "abstract", "prompt", model="m", base_url="http://x", client=mock_client
        )


def test_classify_abstract_wrong_types() -> None:
    resp = _mock_openai_response('{"relevance_score": "high", "reason": "good"}')
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = resp
    with pytest.raises(ClassifyError):
        classify_abstract(
            "W1", "abstract", "prompt", model="m", base_url="http://x", client=mock_client
        )


# --- screen_works ---


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
        client: Any,
    ) -> ScreeningVerdict:
        entry = results[work_id]
        if entry == "error":
            raise ClassifyError(f"mock failure for {work_id}")
        return ScreeningVerdict(
            work_id=work_id,
            relevance_score=entry["relevance_score"],
            reason=entry["reason"],
        )

    return side_effect


def test_screen_works_basic(tmp_path: Path) -> None:
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
        "laglitsynth.screening_abstracts.screen.classify_abstract",
        side_effect=_mock_classify(classify_results),
    ):
        with patch("laglitsynth.screening_abstracts.screen.OpenAI"):
            results = list(
                screen_works(
                    tmp_path / "input.jsonl",
                    "about oceans",
                    model="m",
                    base_url="http://x",
                    threshold=50,
                    max_records=None,
                )
            )

    assert len(results) == 3
    # W1: above threshold
    assert results[0].relevance_score == 80
    assert results[0].reason == "relevant"
    # W2: skipped (no abstract)
    assert results[1].relevance_score is None
    assert results[1].reason == "no-abstract"
    # W3: below threshold but still emitted
    assert results[2].relevance_score == 30
    assert results[2].reason == "not relevant"


def test_screen_works_max_records_counts_all(tmp_path: Path) -> None:
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
        "laglitsynth.screening_abstracts.screen.classify_abstract",
        side_effect=_mock_classify(classify_results),
    ):
        with patch("laglitsynth.screening_abstracts.screen.OpenAI"):
            results = list(
                screen_works(
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
    assert [r.work_id for r in results] == ["W1", "W2", "W3"]


def test_screen_works_llm_failure(tmp_path: Path) -> None:
    """LLM parse failure yields verdict with reason='llm-parse-failure'."""
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
        "laglitsynth.screening_abstracts.screen.classify_abstract",
        side_effect=_mock_classify(classify_results),
    ):
        with patch("laglitsynth.screening_abstracts.screen.OpenAI"):
            results = list(
                screen_works(
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
    assert results[0].relevance_score is None
    assert results[0].reason == "llm-parse-failure"
    # W2: classified normally
    assert results[1].relevance_score == 75


# --- New sentinel reason tests ---


def test_verdict_reason_no_abstract(tmp_path: Path) -> None:
    """A Work with abstract=None produces a verdict with reason='no-abstract' and no LLM call."""
    works = [_make_work("W1", abstract=None)]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    with patch("laglitsynth.screening_abstracts.screen.classify_abstract") as mock_classify:
        with patch("laglitsynth.screening_abstracts.screen.OpenAI"):
            results = list(
                screen_works(
                    tmp_path / "input.jsonl",
                    "prompt",
                    model="m",
                    base_url="http://x",
                    threshold=50,
                    max_records=None,
                )
            )

    assert len(results) == 1
    assert results[0].relevance_score is None
    assert results[0].reason == "no-abstract"
    mock_classify.assert_not_called()


def test_verdict_reason_llm_parse_failure(tmp_path: Path) -> None:
    """LLM returns malformed JSON; verdict has reason='llm-parse-failure'."""
    works = [_make_work("W1", abstract="An abstract.")]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    def raise_classify_error(*args: Any, **kwargs: Any) -> ScreeningVerdict:
        raise ClassifyError("mock failure")

    with patch(
        "laglitsynth.screening_abstracts.screen.classify_abstract",
        side_effect=raise_classify_error,
    ):
        with patch("laglitsynth.screening_abstracts.screen.OpenAI"):
            results = list(
                screen_works(
                    tmp_path / "input.jsonl",
                    "prompt",
                    model="m",
                    base_url="http://x",
                    threshold=50,
                    max_records=None,
                )
            )

    assert len(results) == 1
    assert results[0].relevance_score is None
    assert results[0].reason == "llm-parse-failure"


# --- ScreeningVerdict model ---


def test_screening_verdict_defaults() -> None:
    v = ScreeningVerdict(work_id="W1")
    assert v.relevance_score is None
    assert v.reason is None


def test_screening_verdict_extra_fields_ignored() -> None:
    v = ScreeningVerdict(
        work_id="W1",
        relevance_score=50,
        reason="ok",
        bonus_field="should be ignored",  # type: ignore[call-arg]
    )
    assert not hasattr(v, "bonus_field")


# --- ScreeningMeta model ---


def test_screening_meta_serialization() -> None:
    meta = ScreeningMeta(
        prompt="test",
        model="gemma3:4b",
        threshold=50,
        screened_at="2026-01-01T00:00:00",
        input_path="data/catalogue-dedup/deduplicated.jsonl",
        input_count=17,
        above_threshold_count=10,
        below_threshold_count=5,
        skipped_count=2,
    )
    data = meta.model_dump()
    assert data["tool"] == "laglitsynth.screening_abstracts.screen"
    assert data["above_threshold_count"] == 10


# --- _preflight ---


def test_preflight_raises_on_connection_failure() -> None:
    from laglitsynth.screening_abstracts.screen import _preflight

    args = MagicMock()
    args.base_url = "http://localhost:99999"
    args.model = "nonexistent"

    with patch("laglitsynth.screening_abstracts.screen.OpenAI") as mock_cls:
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
    args.output_dir = tmp_path / "out"
    args.model = "m"
    args.base_url = "http://x"
    args.screening_threshold = 50
    args.max_records = None
    args.dry_run = True

    with (
        patch("laglitsynth.screening_abstracts.screen._preflight"),
        patch(
            "laglitsynth.screening_abstracts.screen.classify_abstract",
            side_effect=_mock_classify(classify_results),
        ),
        patch("laglitsynth.screening_abstracts.screen.OpenAI"),
    ):
        from laglitsynth.screening_abstracts.screen import run

        run(args)

    # No output files should be created
    assert not (tmp_path / "out" / "verdicts.jsonl").exists()
    assert not (tmp_path / "out" / "screening-meta.json").exists()


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

    out_dir = tmp_path / "out"

    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.prompt = "about oceans"
    args.output_dir = out_dir
    args.model = "m"
    args.base_url = "http://x"
    args.screening_threshold = 50
    args.max_records = None
    args.dry_run = False

    with (
        patch("laglitsynth.screening_abstracts.screen._preflight"),
        patch(
            "laglitsynth.screening_abstracts.screen.classify_abstract",
            side_effect=_mock_classify(classify_results),
        ),
        patch("laglitsynth.screening_abstracts.screen.OpenAI"),
    ):
        from laglitsynth.screening_abstracts.screen import run

        run(args)

    # Only verdicts.jsonl and screening-meta.json should exist; no accepted/rejected split
    assert (out_dir / "verdicts.jsonl").exists()
    assert (out_dir / "screening-meta.json").exists()
    assert not (out_dir / "accepted.jsonl").exists()
    assert not (out_dir / "rejected.jsonl").exists()

    # Every input work has a verdict
    verdict_lines = (out_dir / "verdicts.jsonl").read_text().strip().splitlines()
    assert len(verdict_lines) == 3

    work_ids_in_verdicts = {json.loads(line)["work_id"] for line in verdict_lines}
    assert "W1" in work_ids_in_verdicts
    assert "W2" in work_ids_in_verdicts
    assert "W3" in work_ids_in_verdicts

    # Meta should exist with correct counts
    meta = json.loads((out_dir / "screening-meta.json").read_text())
    assert meta["above_threshold_count"] == 1
    assert meta["below_threshold_count"] == 1
    assert meta["skipped_count"] == 1
