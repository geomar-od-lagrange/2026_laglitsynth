"""Tests for the screening_abstracts submodule."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from openai import APIConnectionError, APITimeoutError

from laglitsynth.screening_abstracts.screen import (
    USER_TEMPLATE,
    classify_abstract,
    format_screening_input,
    screen_works,
)
from laglitsynth.screening_abstracts.decisions import DEFAULT_DECISIONS_URL
from laglitsynth.screening_abstracts.models import ScreeningMeta, ScreeningVerdict
from laglitsynth.models import LlmMeta, RunMeta

from conftest import (
    TEST_RUN_ID,
    _make_work,
    _mock_openai_response,
    _write_manifest,
    _write_works_jsonl,
)

# An inlined screening-criteria spec (dict form, consumed directly by
# resolve_yaml_arg) standing in for the on-disk YAML in run()-level tests.
_TEST_CRITERIA_SPEC = {
    "id": "test-criteria",
    "system_prompt": "You are a test relevance classifier. Respond with JSON.",
}


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


def test_classify_abstract_sends_system_prompt_and_bare_user_message() -> None:
    """The loaded system_prompt is the system message; the formatted listing
    is the user message verbatim — no ``Criterion:`` prefix."""
    resp = _mock_openai_response('{"relevance_score": 70, "reason": "ok"}')
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = resp

    system_prompt = "SYSTEM: score relevance and return JSON."
    formatted_input = "Title: T\nAuthors: A\nYear: 2024\nAbstract: body"
    classify_abstract(
        "W1",
        formatted_input,
        system_prompt,
        model="m",
        base_url="http://x",
        client=mock_client,
    )

    _, kwargs = mock_client.chat.completions.create.call_args
    messages = kwargs["messages"]
    assert messages[0] == {"role": "system", "content": system_prompt}
    assert messages[1] == {"role": "user", "content": formatted_input}
    assert "Criterion:" not in messages[1]["content"]


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
        formatted_input: str,
        system_prompt: str,
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


# --- run() end-to-end with mocked LLM ---


def _run_args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    """Build a Namespace mirroring screen.build_subparser defaults."""
    base: dict[str, Any] = {
        "input": tmp_path / "input.jsonl",
        "screening_criteria": _TEST_CRITERIA_SPEC,
        "data_dir": tmp_path,
        "run_id": "test-run-id",
        "model": "m",
        "base_url": "http://x",
        "screening_threshold": 50.0,
        "max_records": None,
        "dry_run": False,
        "concurrency": 1,
        "config": None,
        "pass_all": False,
        "backend": "ollama",
        "api_key": None,
        "decisions_url": DEFAULT_DECISIONS_URL,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


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

    args = _run_args(tmp_path, dry_run=True)
    out_dir = tmp_path / "screening-abstracts" / "test-run-id"

    with (
        patch("laglitsynth.screening_abstracts.screen.preflight"),
        patch(
            "laglitsynth.screening_abstracts.screen.classify_abstract",
            side_effect=_mock_classify(classify_results),
        ),
        patch("laglitsynth.screening_abstracts.screen.OpenAI"),
    ):
        from laglitsynth.screening_abstracts.screen import run

        run(args)

    # No output files should be created
    assert not (out_dir / "verdicts.jsonl").exists()
    assert not (out_dir / "screening-meta.json").exists()
    assert not (out_dir / "config.yaml").exists()


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

    args = _run_args(tmp_path)
    out_dir = tmp_path / "screening-abstracts" / "test-run-id"

    with (
        patch("laglitsynth.screening_abstracts.screen.preflight"),
        patch(
            "laglitsynth.screening_abstracts.screen.classify_abstract",
            side_effect=_mock_classify(classify_results),
        ),
        patch("laglitsynth.screening_abstracts.screen.OpenAI"),
    ):
        from laglitsynth.screening_abstracts.screen import run

        run(args)

    # verdicts.jsonl, screening-meta.json, config.yaml should exist; no accepted/rejected split
    assert (out_dir / "verdicts.jsonl").exists()
    assert (out_dir / "screening-meta.json").exists()
    assert (out_dir / "config.yaml").exists()
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
    """meta.llm.prompt_sha256 must equal sha256(system_prompt + '\\n' + USER_TEMPLATE)."""
    works = [_make_work("W1", abstract="An abstract.")]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    system_prompt = _TEST_CRITERIA_SPEC["system_prompt"]
    expected_digest = hashlib.sha256(
        (system_prompt + "\n" + USER_TEMPLATE).encode("utf-8")
    ).hexdigest()

    classify_results = {"W1": {"relevance_score": 80, "reason": "yes"}}
    args = _run_args(tmp_path)
    out_dir = tmp_path / "screening-abstracts" / "test-run-id"

    with (
        patch("laglitsynth.screening_abstracts.screen.preflight"),
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
    assert meta["criterion"] == system_prompt


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

    out_dir = tmp_path / "screening-abstracts" / "test-run-id"
    meta_path = out_dir / "screening-meta.json"

    captured: dict[str, Any] = {}

    def side_effect(
        work_id: str,
        formatted_input: str,
        system_prompt: str,
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

    args = _run_args(tmp_path)

    with (
        patch("laglitsynth.screening_abstracts.screen.preflight"),
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
    assert upfront["criterion"] == _TEST_CRITERIA_SPEC["system_prompt"]
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
        formatted_input: str,
        system_prompt: str,
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

    out_dir = tmp_path / "screening-abstracts" / "test-run-id"
    args = _run_args(tmp_path)

    with (
        patch("laglitsynth.screening_abstracts.screen.preflight"),
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


# --- Run dirs, config.yaml, --run-id ---


def test_config_yaml_records_resolved_args(tmp_path: Path) -> None:
    works = [_make_work("W1", abstract="a")]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    args = _run_args(tmp_path, model="gemma3:4b")

    classify_results = {"W1": {"relevance_score": 80, "reason": "ok"}}
    with (
        patch("laglitsynth.screening_abstracts.screen.preflight"),
        patch(
            "laglitsynth.screening_abstracts.screen.classify_abstract",
            side_effect=_mock_classify(classify_results),
        ),
        patch("laglitsynth.screening_abstracts.screen.OpenAI"),
    ):
        from laglitsynth.screening_abstracts.screen import run

        run(args)

    import yaml as _yaml

    out_dir = tmp_path / "screening-abstracts" / "test-run-id"
    written = _yaml.safe_load((out_dir / "config.yaml").read_text())
    assert written["model"] == "gemma3:4b"
    assert written["screening_threshold"] == 50.0
    # screening_criteria is inlined as a mapping (not a path string).
    assert written["screening_criteria"]["system_prompt"] == (
        _TEST_CRITERIA_SPEC["system_prompt"]
    )
    # run_id excluded — replay must generate a fresh one.
    assert "run_id" not in written
    # config (the --config flag itself) excluded.
    assert "config" not in written


def test_config_seeds_defaults_explicit_cli_wins(tmp_path: Path) -> None:
    """End-to-end through cli.main: --config seeds defaults; explicit CLI flag wins."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "model: from-config\nscreening_threshold: 75\nconcurrency: 4\n",
        encoding="utf-8",
    )

    captured: dict[str, Any] = {}

    def fake_run(args: Any) -> None:
        captured["args"] = args

    argv = [
        "screening-abstracts",
        str(tmp_path / "input.jsonl"),
        "--config",
        str(cfg),
        "--model",
        "from-cli",
    ]
    from laglitsynth.cli import main

    with patch("laglitsynth.screening_abstracts.screen.run", fake_run):
        main(argv)

    args = captured["args"]
    assert args.model == "from-cli"  # CLI wins
    assert args.screening_threshold == 75  # config seeded
    assert args.concurrency == 4  # config seeded


def test_run_dir_printed_to_stderr_at_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """run() prints 'Run dir: <output_dir>' to stderr at the end of a normal (non-dry) run."""
    works = [_make_work("W1", abstract="Ocean currents.")]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    classify_results = {"W1": {"relevance_score": 80, "reason": "yes"}}
    args = _run_args(tmp_path)
    expected_dir = tmp_path / "screening-abstracts" / "test-run-id"

    with (
        patch("laglitsynth.screening_abstracts.screen.preflight"),
        patch(
            "laglitsynth.screening_abstracts.screen.classify_abstract",
            side_effect=_mock_classify(classify_results),
        ),
        patch("laglitsynth.screening_abstracts.screen.OpenAI"),
    ):
        from laglitsynth.screening_abstracts.screen import run

        run(args)

    err = capsys.readouterr().err
    assert f"Run dir: {expected_dir}" in err
    # The line must appear at the end (last non-empty line).
    last_line = [line for line in err.splitlines() if line.strip()][-1]
    assert last_line == f"Run dir: {expected_dir}"


# ---------------------------------------------------------------------------
# Conventional input path and run-id from the manifest
# ---------------------------------------------------------------------------


def _run_patched(args: argparse.Namespace) -> MagicMock:
    """Run ``screen.run`` with preflight/LLM mocked; return the preflight mock."""
    classify_results = {"W1": {"relevance_score": 80, "reason": "yes"}}
    with (
        patch("laglitsynth.screening_abstracts.screen.preflight") as mock_preflight,
        patch(
            "laglitsynth.screening_abstracts.screen.classify_abstract",
            side_effect=_mock_classify(classify_results),
        ),
        patch("laglitsynth.screening_abstracts.screen.OpenAI"),
    ):
        from laglitsynth.screening_abstracts.screen import run

        run(args)
    return mock_preflight


class TestDataDirResolution:
    def test_omitted_input_reads_the_conventional_dedup_output(
        self, tmp_path: Path
    ) -> None:
        dedup_output = tmp_path / "catalogue-dedup" / "deduplicated.jsonl"
        dedup_output.parent.mkdir()
        _write_works_jsonl(dedup_output, [_make_work("W1", abstract="Ocean currents.")])

        args = _run_args(tmp_path, input=None)
        _run_patched(args)

        assert args.input == dedup_output
        out_dir = tmp_path / "screening-abstracts" / "test-run-id"
        verdict_lines = (out_dir / "verdicts.jsonl").read_text().strip().splitlines()
        assert [json.loads(line)["work_id"] for line in verdict_lines] == ["W1"]

    def test_missing_dedup_output_exits_before_any_llm_call(
        self, tmp_path: Path
    ) -> None:
        args = _run_args(tmp_path, input=None)

        with (
            patch("laglitsynth.screening_abstracts.screen.preflight") as mock_preflight,
            patch("laglitsynth.screening_abstracts.screen.OpenAI"),
            pytest.raises(SystemExit, match="catalogue-dedup") as exc_info,
        ):
            from laglitsynth.screening_abstracts.screen import run

            run(args)

        assert "deduplicated.jsonl" in str(exc_info.value)
        mock_preflight.assert_not_called()
        assert not (tmp_path / "screening-abstracts").exists()

    def test_omitted_run_id_adopts_the_manifest(self, tmp_path: Path) -> None:
        _write_works_jsonl(
            tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")]
        )
        _write_manifest(tmp_path)

        args = _run_args(tmp_path, run_id=None)
        _run_patched(args)

        assert args.run_id == TEST_RUN_ID
        assert (
            tmp_path / "screening-abstracts" / TEST_RUN_ID / "verdicts.jsonl"
        ).exists()

    def test_explicit_run_id_wins_over_the_manifest(self, tmp_path: Path) -> None:
        _write_works_jsonl(
            tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")]
        )
        _write_manifest(tmp_path)

        args = _run_args(tmp_path, run_id="explicit-run-id")
        _run_patched(args)

        assert args.run_id == "explicit-run-id"
        assert (
            tmp_path / "screening-abstracts" / "explicit-run-id" / "verdicts.jsonl"
        ).exists()
        assert not (tmp_path / "screening-abstracts" / TEST_RUN_ID).exists()

    def test_no_manifest_and_no_run_id_exits_before_any_llm_call(
        self, tmp_path: Path
    ) -> None:
        _write_works_jsonl(
            tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")]
        )
        args = _run_args(tmp_path, run_id=None)

        with (
            patch("laglitsynth.screening_abstracts.screen.preflight") as mock_preflight,
            patch(
                "laglitsynth.screening_abstracts.screen.classify_abstract"
            ) as mock_classify,
            patch("laglitsynth.screening_abstracts.screen.OpenAI"),
            pytest.raises(SystemExit, match="manifest-init"),
        ):
            from laglitsynth.screening_abstracts.screen import run

            run(args)

        mock_preflight.assert_not_called()
        mock_classify.assert_not_called()
        assert not (tmp_path / "screening-abstracts").exists()

    def test_rerun_into_an_existing_run_dir_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_works_jsonl(
            tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")]
        )

        _run_patched(_run_args(tmp_path))
        assert "Reusing run dir" not in capsys.readouterr().err

        _run_patched(_run_args(tmp_path))
        assert "Reusing run dir" in capsys.readouterr().err

    def test_writes_no_manifest(self, tmp_path: Path) -> None:
        _write_works_jsonl(
            tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")]
        )

        _run_patched(_run_args(tmp_path))

        assert not (tmp_path / "manifest.json").exists()


# ---------------------------------------------------------------------------
# --pass-all: an unscreened sentinel per work, no LLM
# ---------------------------------------------------------------------------


class TestPassAll:
    def _run(self, args: argparse.Namespace) -> tuple[MagicMock, MagicMock]:
        """Run with preflight and the OpenAI client patched; return both mocks."""
        with (
            patch("laglitsynth.screening_abstracts.screen.preflight") as mock_preflight,
            patch("laglitsynth.screening_abstracts.screen.OpenAI") as mock_openai,
        ):
            from laglitsynth.screening_abstracts.screen import run

            run(args)
        return mock_preflight, mock_openai

    def _works(self, tmp_path: Path) -> list[str]:
        works = [
            _make_work("W1", abstract="Ocean currents."),
            _make_work("W2", abstract=None),
            _make_work("W3", abstract="Forest ecology."),
        ]
        _write_works_jsonl(tmp_path / "input.jsonl", works)
        return [w.id for w in works]

    def test_writes_unscreened_verdict_per_work(self, tmp_path: Path) -> None:
        ids = self._works(tmp_path)
        mock_preflight, mock_openai = self._run(_run_args(tmp_path, pass_all=True))

        mock_preflight.assert_not_called()
        mock_openai.assert_not_called()

        out_dir = tmp_path / "screening-abstracts" / "test-run-id"
        verdicts = [
            ScreeningVerdict.model_validate_json(line)
            for line in (out_dir / "verdicts.jsonl").read_text().splitlines()
        ]
        assert [v.work_id for v in verdicts] == ids
        for v in verdicts:
            assert v.reason == "unscreened"
            assert v.relevance_score is None
            assert v.seed is None
            assert v.raw_response is None

    def test_meta_has_no_llm_and_counts_all_skipped(self, tmp_path: Path) -> None:
        self._works(tmp_path)
        self._run(_run_args(tmp_path, pass_all=True))

        out_dir = tmp_path / "screening-abstracts" / "test-run-id"
        raw = json.loads((out_dir / "screening-meta.json").read_text())
        assert raw["llm"] is None
        meta = ScreeningMeta.model_validate(raw)
        assert meta.llm is None
        assert meta.criterion == ""
        assert meta.input_count == 3
        assert meta.skipped_count == meta.input_count
        assert meta.above_threshold_count == 0
        assert meta.below_threshold_count == 0
        assert meta.llm_parse_failure_count == 0
        assert meta.llm_timeout_count == 0

    def test_config_yaml_records_pass_all(self, tmp_path: Path) -> None:
        import yaml as _yaml

        self._works(tmp_path)
        self._run(_run_args(tmp_path, pass_all=True))

        out_dir = tmp_path / "screening-abstracts" / "test-run-id"
        written = _yaml.safe_load((out_dir / "config.yaml").read_text())
        assert written["pass_all"] is True

    def test_max_records_caps_the_verdicts(self, tmp_path: Path) -> None:
        ids = self._works(tmp_path)
        self._run(_run_args(tmp_path, pass_all=True, max_records=2))

        out_dir = tmp_path / "screening-abstracts" / "test-run-id"
        lines = (out_dir / "verdicts.jsonl").read_text().splitlines()
        assert [json.loads(line)["work_id"] for line in lines] == ids[:2]

    def test_stage5_join_keeps_unscreened_works(self, tmp_path: Path) -> None:
        from laglitsynth.fulltext_retrieval.retrieve import _active_works

        catalogue = tmp_path / "catalogue.jsonl"
        _write_works_jsonl(catalogue, [_make_work("W1"), _make_work("W2")])
        verdicts = tmp_path / "verdicts.jsonl"
        verdicts.write_text(
            ScreeningVerdict(work_id="W1", reason="unscreened").model_dump_json()
            + "\n"
            + ScreeningVerdict(work_id="W2", relevance_score=10).model_dump_json()
            + "\n"
        )

        active = [w.id for w in _active_works(catalogue, verdicts, 50.0)]
        assert active == ["W1"]


# ---------------------------------------------------------------------------
# --backend decisions: hosted decisions model, probability -> score
# ---------------------------------------------------------------------------

_DECISION_BLOCK = {
    "instructions": "Is this about ocean particle tracking?",
    "criteria": {"true": "Ocean flow tracking.", "false": "Anything else."},
}
_DECISIONS_CRITERIA_SPEC = {**_TEST_CRITERIA_SPEC, "decision": _DECISION_BLOCK}


class TestDecisionsBackend:
    # Probability the mock endpoint returns, keyed by a phrase in the state.
    _PROBS = {"Ocean currents.": 0.73, "Forest ecology.": 0.1}

    def _works(self, tmp_path: Path) -> None:
        _write_works_jsonl(
            tmp_path / "input.jsonl",
            [
                _make_work("W1", abstract="Ocean currents."),
                _make_work("W2", abstract=None),
                _make_work("W3", abstract="Forest ecology."),
            ],
        )

    def _args(self, tmp_path: Path, **overrides: Any) -> argparse.Namespace:
        base: dict[str, Any] = {
            "backend": "decisions",
            "api_key": "k",
            "model": "typesafe/jev",
            "screening_criteria": _DECISIONS_CRITERIA_SPEC,
        }
        base.update(overrides)
        return _run_args(tmp_path, **base)

    def _run(
        self, args: argparse.Namespace
    ) -> tuple[MagicMock, MagicMock, list[dict[str, Any]]]:
        """Run with the decisions endpoint mocked; return preflight, client factory, bodies."""
        import httpx

        bodies: list[dict[str, Any]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            bodies.append(body)
            p = next(v for k, v in self._PROBS.items() if k in body["state"])
            return httpx.Response(200, json={"answers": {"relevant": {"noul": p}}})

        def make_client(api_key: str) -> httpx.Client:
            return httpx.Client(transport=httpx.MockTransport(handler))

        with (
            patch("laglitsynth.screening_abstracts.screen.preflight") as mock_preflight,
            patch(
                "laglitsynth.screening_abstracts.screen.decisions_client",
                side_effect=make_client,
            ) as mock_client,
            patch("laglitsynth.screening_abstracts.screen.OpenAI"),
        ):
            from laglitsynth.screening_abstracts.screen import run

            run(args)
        return mock_preflight, mock_client, bodies

    def _out_dir(self, tmp_path: Path) -> Path:
        return tmp_path / "screening-abstracts" / "test-run-id"

    def test_writes_probability_scores_and_no_abstract(self, tmp_path: Path) -> None:
        self._works(tmp_path)
        mock_preflight, mock_client, bodies = self._run(self._args(tmp_path))

        mock_preflight.assert_not_called()
        mock_client.assert_called_once_with("k")
        assert len(bodies) == 2
        assert {b["model"] for b in bodies} == {"typesafe/jev"}
        assert bodies[0]["questions"]["relevant"]["instructions"] == (
            _DECISION_BLOCK["instructions"]
        )

        lines = (self._out_dir(tmp_path) / "verdicts.jsonl").read_text().splitlines()
        verdicts = {
            v.work_id: v for v in (ScreeningVerdict.model_validate_json(x) for x in lines)
        }
        assert set(verdicts) == {"W1", "W2", "W3"}
        assert verdicts["W1"].relevance_score == 73
        assert verdicts["W1"].reason is None
        assert verdicts["W1"].seed is None
        assert verdicts["W1"].raw_response == json.dumps({"noul": 0.73})
        assert verdicts["W3"].relevance_score == 10
        assert verdicts["W2"].reason == "no-abstract"
        assert verdicts["W2"].relevance_score is None

    def test_meta_records_question_and_null_temperature(self, tmp_path: Path) -> None:
        from laglitsynth.prompts import DecisionQuestion
        from laglitsynth.screening_abstracts.screen import USER_TEMPLATE

        self._works(tmp_path)
        self._run(self._args(tmp_path))

        raw = json.loads((self._out_dir(tmp_path) / "screening-meta.json").read_text())
        assert raw["llm"]["temperature"] is None
        assert raw["llm"]["model"] == "typesafe/jev"

        question = DecisionQuestion.model_validate(_DECISION_BLOCK)
        assert raw["criterion"] == question.render()
        expected = hashlib.sha256(
            (question.canonical_json() + "\n" + USER_TEMPLATE).encode("utf-8")
        ).hexdigest()
        assert raw["llm"]["prompt_sha256"] == expected

        meta = ScreeningMeta.model_validate(raw)
        assert meta.input_count == 3
        assert meta.above_threshold_count == 1
        assert meta.below_threshold_count == 1
        assert meta.skipped_count == 1

    def test_config_yaml_records_backend(self, tmp_path: Path) -> None:
        import yaml as _yaml

        self._works(tmp_path)
        self._run(self._args(tmp_path))

        written = _yaml.safe_load((self._out_dir(tmp_path) / "config.yaml").read_text())
        assert written["backend"] == "decisions"
        assert written["screening_criteria"]["decision"] == _DECISION_BLOCK

    def test_api_key_falls_back_to_env(self, tmp_path: Path) -> None:
        self._works(tmp_path)
        with patch(
            "laglitsynth.screening_abstracts.screen.load_env_var", return_value="from-env"
        ) as mock_env:
            _, mock_client, _ = self._run(self._args(tmp_path, api_key=None))
        mock_env.assert_called_once_with("OPENROUTER_API_KEY")
        mock_client.assert_called_once_with("from-env")

    def test_missing_api_key_exits_naming_the_env_var(self, tmp_path: Path) -> None:
        self._works(tmp_path)
        with (
            patch(
                "laglitsynth.screening_abstracts.screen.load_env_var", return_value=None
            ),
            pytest.raises(SystemExit, match="OPENROUTER_API_KEY"),
        ):
            self._run(self._args(tmp_path, api_key=None))
        assert not (tmp_path / "screening-abstracts").exists()

    def test_inline_criteria_without_decision_block_raises(self, tmp_path: Path) -> None:
        self._works(tmp_path)
        with pytest.raises(SystemExit, match="no 'decision' block"):
            self._run(self._args(tmp_path, screening_criteria=_TEST_CRITERIA_SPEC))
        assert not self._out_dir(tmp_path).exists()

    def test_criteria_file_without_decision_block_names_the_file(
        self, tmp_path: Path
    ) -> None:
        import yaml as _yaml

        self._works(tmp_path)
        criteria = tmp_path / "no-decision-criteria.yaml"
        criteria.write_text(_yaml.safe_dump(_TEST_CRITERIA_SPEC), encoding="utf-8")
        with pytest.raises(SystemExit, match="no-decision-criteria.yaml"):
            self._run(self._args(tmp_path, screening_criteria=str(criteria)))

    def test_ollama_backend_ignores_api_key(self, tmp_path: Path) -> None:
        """Default backend still preflights and never builds a decisions client."""
        self._works(tmp_path)
        args = _run_args(tmp_path, screening_criteria=_DECISIONS_CRITERIA_SPEC)
        classify_results = {
            "W1": {"relevance_score": 80, "reason": "yes"},
            "W3": {"relevance_score": 10, "reason": "no"},
        }
        with (
            patch("laglitsynth.screening_abstracts.screen.preflight") as mock_preflight,
            patch(
                "laglitsynth.screening_abstracts.screen.classify_abstract",
                side_effect=_mock_classify(classify_results),
            ),
            patch("laglitsynth.screening_abstracts.screen.OpenAI"),
            patch("laglitsynth.screening_abstracts.screen.decisions_client") as mock_dc,
            patch("laglitsynth.screening_abstracts.screen.load_env_var") as mock_env,
        ):
            from laglitsynth.screening_abstracts.screen import run

            run(args)
        mock_preflight.assert_called_once()
        mock_dc.assert_not_called()
        mock_env.assert_not_called()
        raw = json.loads((self._out_dir(tmp_path) / "screening-meta.json").read_text())
        assert raw["llm"]["temperature"] == 0.8
        assert raw["criterion"] == _TEST_CRITERIA_SPEC["system_prompt"]
