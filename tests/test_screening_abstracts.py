"""Tests for the screening_abstracts submodule."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from openai import APIConnectionError, APITimeoutError

from laglitsynth.screening_abstracts.screen import (
    SYSTEM_PROMPT,
    classify_abstract,
    format_screening_input,
    screen_works,
)
from laglitsynth.screening_abstracts.models import ScreeningMeta, ScreeningVerdict
from laglitsynth.models import LlmMeta, RunMeta

from conftest import _make_work, _mock_openai_response, _write_works_jsonl


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
    assert verdict.raw_response == '{"relevance_score": 85, "reason": "relevant"}'


def test_classify_abstract_malformed_json_returns_sentinel() -> None:
    resp = _mock_openai_response("this is not json at all")
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = resp
    verdict = classify_abstract(
        "W1", "abstract", "prompt", model="m", base_url="http://x", client=mock_client
    )
    assert verdict.reason == "llm-parse-failure"
    assert verdict.relevance_score is None
    assert verdict.seed is None
    assert verdict.raw_response == "this is not json at all"


def test_classify_abstract_missing_fields_returns_sentinel() -> None:
    resp = _mock_openai_response('{"relevance_score": 50}')
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = resp
    verdict = classify_abstract(
        "W1", "abstract", "prompt", model="m", base_url="http://x", client=mock_client
    )
    assert verdict.reason == "llm-parse-failure"
    assert verdict.raw_response == '{"relevance_score": 50}'


def test_classify_abstract_timeout_returns_sentinel() -> None:
    """APITimeoutError after retries → reason='llm-timeout' sentinel."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = APITimeoutError(
        request=MagicMock()
    )
    verdict = classify_abstract(
        "W1", "input block", "prompt", model="m", base_url="http://x", client=mock_client
    )
    assert verdict.reason == "llm-timeout"
    assert verdict.relevance_score is None
    assert verdict.seed is None
    assert verdict.raw_response is None


def test_classify_abstract_connection_error_returns_sentinel() -> None:
    """APIConnectionError after retries → reason='llm-timeout' sentinel."""
    mock_client = MagicMock()
    mock_client.chat.completions.create.side_effect = APIConnectionError(
        request=MagicMock()
    )
    verdict = classify_abstract(
        "W1", "input block", "prompt", model="m", base_url="http://x", client=mock_client
    )
    assert verdict.reason == "llm-timeout"
    assert verdict.relevance_score is None
    assert verdict.seed is None


def test_format_screening_input_complete_work() -> None:
    work = _make_work(
        "W1",
        title="A Real Title",
        abstract="An abstract.",
        publication_year=2023,
    )
    rendered = format_screening_input(work)
    assert "Title: A Real Title" in rendered
    assert "Abstract: An abstract." in rendered
    assert "Year: 2023" in rendered
    # No authorships fixture data → "<unknown>"
    assert "Authors: <unknown>" in rendered


def test_format_screening_input_missing_fields_render_unknown() -> None:
    work = _make_work(
        "W1",
        title=None,
        abstract="Has abstract.",
        publication_year=None,
    )
    rendered = format_screening_input(work)
    assert "Title: <unknown>" in rendered
    assert "Year: <unknown>" in rendered


def test_classify_abstract_wrong_types_returns_sentinel() -> None:
    resp = _mock_openai_response('{"relevance_score": "high", "reason": "good"}')
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = resp
    verdict = classify_abstract(
        "W1", "abstract", "prompt", model="m", base_url="http://x", client=mock_client
    )
    assert verdict.reason == "llm-parse-failure"
    assert verdict.raw_response == '{"relevance_score": "high", "reason": "good"}'


# --- screen_works ---


def _mock_classify(
    results: dict[str, dict[str, Any]],
) -> Any:
    """Return a side_effect function for classify_abstract.

    results maps work_id to {"relevance_score": ..., "reason": ...}
    or "error" to return an llm-parse-failure sentinel verdict.
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
            return ScreeningVerdict(
                work_id=work_id,
                relevance_score=None,
                reason="llm-parse-failure",
                seed=None,
                raw_response="mock failure",
            )
        return ScreeningVerdict(
            work_id=work_id,
            relevance_score=entry["relevance_score"],
            reason=entry["reason"],
            seed=12345,
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
                    max_records=None,
                )
            )

    assert len(results) == 3
    by_id = {v.work_id: v for v in results}
    assert set(by_id) == {"W1", "W2", "W3"}
    # W1: above threshold
    assert by_id["W1"].relevance_score == 80
    assert by_id["W1"].reason == "relevant"
    # W2: skipped (no abstract)
    assert by_id["W2"].relevance_score is None
    assert by_id["W2"].reason == "no-abstract"
    # W3: below threshold but still emitted
    assert by_id["W3"].relevance_score == 30
    assert by_id["W3"].reason == "not relevant"


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
                    max_records=3,
                )
            )

    # Should get W1 (skipped), W2 (classified), W3 (skipped) = 3 total
    assert len(results) == 3
    assert {r.work_id for r in results} == {"W1", "W2", "W3"}


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
                    max_records=None,
                )
            )

    assert len(results) == 2
    by_id = {v.work_id: v for v in results}
    assert set(by_id) == {"W1", "W2"}
    # W1: LLM failure
    assert by_id["W1"].relevance_score is None
    assert by_id["W1"].reason == "llm-parse-failure"
    # W2: classified normally
    assert by_id["W2"].relevance_score == 75


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
        return ScreeningVerdict(
            work_id="W1",
            relevance_score=None,
            reason="llm-parse-failure",
            seed=None,
            raw_response="mock failure",
        )

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


def test_screening_verdict_extra_fields_forbidden() -> None:
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        ScreeningVerdict(
            work_id="W1",
            relevance_score=50,
            reason="ok",
            bonus_field="extra fields now forbidden",  # type: ignore[call-arg]
        )


# --- ScreeningMeta model ---


def test_screening_meta_serialization() -> None:
    run_meta = RunMeta(
        tool="laglitsynth.screening_abstracts.screen",
        run_at="2026-01-01T00:00:00+00:00",
        validation_skipped=0,
    )
    llm_meta = LlmMeta(
        model="gemma3:4b",
        temperature=0.8,
        prompt_sha256="a" * 64,
    )
    meta = ScreeningMeta(
        run=run_meta,
        llm=llm_meta,
        threshold=50,
        input_path="data/catalogue-dedup/deduplicated.jsonl",
        input_count=17,
        above_threshold_count=10,
        below_threshold_count=5,
        skipped_count=2,
    )
    data = meta.model_dump()
    assert data["run"]["tool"] == "laglitsynth.screening_abstracts.screen"
    assert data["above_threshold_count"] == 10
    assert data["llm"]["temperature"] == 0.8


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
    args.concurrency = 1

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
    args.concurrency = 1

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

    # Meta should exist with correct counts and nested run/llm structure
    meta = json.loads((out_dir / "screening-meta.json").read_text())
    assert meta["above_threshold_count"] == 1
    assert meta["below_threshold_count"] == 1
    assert meta["skipped_count"] == 1
    assert meta["run"]["tool"] == "laglitsynth.screening_abstracts.screen"
    assert meta["llm"]["temperature"] == 0.8


# --- New tests: seed, sentinel seed=None, prompt_sha256 ---


def test_seed_recorded_on_verdict(tmp_path: Path) -> None:
    """Patch random.randint to a known value; verify verdict.seed matches."""
    works = [_make_work("W1", abstract="An ocean abstract.")]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _mock_openai_response(
        '{"relevance_score": 75, "reason": "relevant"}'
    )

    with patch("laglitsynth.screening_abstracts.screen.random.randint", return_value=42):
        verdict = classify_abstract(
            "W1",
            "An ocean abstract.",
            "about oceans",
            model="m",
            base_url="http://x",
            client=mock_client,
        )

    assert verdict.seed == 42


def test_seed_none_on_sentinel_reasons(tmp_path: Path) -> None:
    """no-abstract and llm-parse-failure verdicts carry seed=None."""
    works = [
        _make_work("W1", abstract=None),
        _make_work("W2", abstract="Has abstract."),
    ]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    def raise_classify_error(*args: Any, **kwargs: Any) -> ScreeningVerdict:
        return ScreeningVerdict(
            work_id="W1",
            relevance_score=None,
            reason="llm-parse-failure",
            seed=None,
            raw_response="mock failure",
        )

    with (
        patch(
            "laglitsynth.screening_abstracts.screen.classify_abstract",
            side_effect=raise_classify_error,
        ),
        patch("laglitsynth.screening_abstracts.screen.OpenAI"),
    ):
        results = list(
            screen_works(
                tmp_path / "input.jsonl",
                "prompt",
                model="m",
                base_url="http://x",
                max_records=None,
            )
        )

    assert results[0].reason == "no-abstract"
    assert results[0].seed is None
    assert results[1].reason == "llm-parse-failure"
    assert results[1].seed is None


def test_prompt_sha256_matches(tmp_path: Path) -> None:
    """meta.llm.prompt_sha256 must equal sha256(SYSTEM_PROMPT + '\\n' + prompt)."""
    works = [_make_work("W1", abstract="An abstract.")]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    user_prompt = "about oceans"
    expected_digest = hashlib.sha256(
        (SYSTEM_PROMPT + "\n" + user_prompt).encode("utf-8")
    ).hexdigest()

    classify_results = {"W1": {"relevance_score": 80, "reason": "yes"}}
    out_dir = tmp_path / "out"

    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.prompt = user_prompt
    args.output_dir = out_dir
    args.model = "m"
    args.base_url = "http://x"
    args.screening_threshold = 50
    args.max_records = None
    args.dry_run = False
    args.concurrency = 1

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

    meta = json.loads((out_dir / "screening-meta.json").read_text())
    assert meta["llm"]["prompt_sha256"] == expected_digest
    assert len(meta["llm"]["prompt_sha256"]) == 64  # full hex digest
    assert meta["prompt"] == user_prompt


# --- Concurrent screening ---


def test_screen_works_concurrent_processes_all(tmp_path: Path) -> None:
    """With concurrency>1, every input work still gets exactly one verdict.

    Ordering is completion order (not catalogue order), so the test asserts
    the set of processed work_ids and per-work verdict values rather than
    positional equality.
    """
    works = [_make_work(f"W{i}", abstract=f"Abstract {i}") for i in range(8)]
    works.append(_make_work("W8", abstract=None))  # one sentinel
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    classify_results = {
        f"W{i}": {"relevance_score": 10 * i, "reason": f"r{i}"} for i in range(8)
    }

    with (
        patch(
            "laglitsynth.screening_abstracts.screen.classify_abstract",
            side_effect=_mock_classify(classify_results),
        ),
        patch("laglitsynth.screening_abstracts.screen.OpenAI"),
    ):
        from laglitsynth.screening_abstracts.screen import screen_works

        results = list(
            screen_works(
                tmp_path / "input.jsonl",
                "prompt",
                model="m",
                base_url="http://x",
                max_records=None,
                concurrency=4,
            )
        )

    assert len(results) == 9
    by_id = {v.work_id: v for v in results}
    assert set(by_id) == {f"W{i}" for i in range(9)}
    # Sentinel work:
    assert by_id["W8"].reason == "no-abstract"
    assert by_id["W8"].relevance_score is None
    # Abstract-backed works:
    for i in range(8):
        v = by_id[f"W{i}"]
        assert v.relevance_score == 10 * i
        assert v.reason == f"r{i}"


def test_run_writes_meta_before_loop_starts(tmp_path: Path) -> None:
    """Meta is written upfront so a mid-run reviewer-export sees it.

    Asserts that screening-meta.json exists by the time the LLM call
    on the first work begins, with prompt + LLM fingerprint populated
    and zeroed counts.
    """
    works = [_make_work(f"W{i}", abstract=f"a{i}") for i in range(3)]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    out_dir = tmp_path / "out"
    meta_path = out_dir / "screening-meta.json"

    captured: dict[str, Any] = {}

    def side_effect(
        work_id: str,
        formatted_input: str,
        prompt: str,
        *,
        model: str,
        base_url: str,
        client: Any,
    ) -> ScreeningVerdict:
        # On the first call, capture whether the meta exists.
        if "first_call_meta_present" not in captured:
            captured["first_call_meta_present"] = meta_path.exists()
            if meta_path.exists():
                captured["first_call_meta"] = json.loads(meta_path.read_text())
        return ScreeningVerdict(
            work_id=work_id, relevance_score=80, reason="ok", seed=1
        )

    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.prompt = "the criterion"
    args.output_dir = out_dir
    args.model = "m"
    args.base_url = "http://x"
    args.screening_threshold = 50
    args.max_records = None
    args.dry_run = False
    args.concurrency = 1

    with (
        patch("laglitsynth.screening_abstracts.screen._preflight"),
        patch(
            "laglitsynth.screening_abstracts.screen.classify_abstract",
            side_effect=side_effect,
        ),
        patch("laglitsynth.screening_abstracts.screen.OpenAI"),
    ):
        from laglitsynth.screening_abstracts.screen import run

        run(args)

    assert captured["first_call_meta_present"] is True
    upfront = captured["first_call_meta"]
    assert upfront["prompt"] == "the criterion"
    assert upfront["above_threshold_count"] == 0
    assert upfront["below_threshold_count"] == 0
    assert upfront["llm"]["model"] == "m"
    assert upfront["input_count"] == 3

    # Final meta has the real counts.
    final = json.loads(meta_path.read_text())
    assert final["above_threshold_count"] == 3


def test_run_streaming_append_partial_is_valid(tmp_path: Path) -> None:
    """A killed run leaves a valid partial verdicts.jsonl (clean-rerun only —
    resume is not supported, but the file shouldn't be truncated mid-line)."""
    works = [_make_work(f"W{i}", abstract=f"a{i}") for i in range(4)]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    call_count = {"n": 0}

    def side_effect(
        work_id: str,
        abstract: str,
        prompt: str,
        *,
        model: str,
        base_url: str,
        client: Any,
    ) -> ScreeningVerdict:
        call_count["n"] += 1
        if call_count["n"] == 3:
            raise RuntimeError("simulated crash")
        return ScreeningVerdict(
            work_id=work_id, relevance_score=50, reason="ok", seed=1
        )

    out_dir = tmp_path / "out"
    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.prompt = "p"
    args.output_dir = out_dir
    args.model = "m"
    args.base_url = "http://x"
    args.screening_threshold = 50
    args.max_records = None
    args.dry_run = False
    args.concurrency = 1

    with (
        patch("laglitsynth.screening_abstracts.screen._preflight"),
        patch(
            "laglitsynth.screening_abstracts.screen.classify_abstract",
            side_effect=side_effect,
        ),
        patch("laglitsynth.screening_abstracts.screen.OpenAI"),
        pytest.raises(RuntimeError, match="simulated crash"),
    ):
        from laglitsynth.screening_abstracts.screen import run

        run(args)

    # verdicts.jsonl should exist and every line should be valid JSON.
    lines = (out_dir / "verdicts.jsonl").read_text().splitlines()
    assert 0 < len(lines) < 4
    for line in lines:
        json.loads(line)  # raises if corrupted
