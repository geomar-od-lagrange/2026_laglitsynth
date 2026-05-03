"""Tests for the ``fulltext_eligibility`` stage."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from openai import APIConnectionError, APITimeoutError

from laglitsynth.fulltext_eligibility.eligibility import (
    _active_works,
    assess_works,
    classify_eligibility,
)
from laglitsynth.fulltext_eligibility.models import EligibilityVerdict
from laglitsynth.fulltext_extraction.models import ExtractedDocument
from laglitsynth.screening_abstracts.models import ScreeningVerdict

from conftest import (
    TEI_NS,
    _make_work,
    _mock_openai_response,
    _write_extractions_jsonl,
    _write_tei,
    _write_works_jsonl,
)


# --- fixtures and helpers ---


def _write_verdicts_jsonl(path: Path, verdicts: list[ScreeningVerdict]) -> None:
    with open(path, "w") as f:
        for v in verdicts:
            f.write(v.model_dump_json() + "\n")


def _write_malformed_tei(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not xml at all")


def _write_empty_body_tei(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<TEI xmlns="{TEI_NS}"><text><body></body></text></TEI>'
        ).encode()
    )


# --- classify_eligibility ---


class TestClassifyEligibility:
    def test_valid_eligible_true(self) -> None:
        resp = _mock_openai_response('{"eligible": true, "reason": "matches"}')
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        verdict = classify_eligibility(
            "W1", "prompt text", "full_text", model="m", client=mock_client, system_prompt="SP"
        )
        assert verdict.work_id == "W1"
        assert verdict.eligible is True
        assert verdict.reason == "matches"
        assert verdict.source_basis == "full_text"
        assert isinstance(verdict.seed, int)
        assert verdict.raw_response == '{"eligible": true, "reason": "matches"}'

    def test_valid_eligible_false(self) -> None:
        resp = _mock_openai_response('{"eligible": false, "reason": "review article"}')
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        verdict = classify_eligibility(
            "W1", "prompt text", "abstract_only", model="m", client=mock_client, system_prompt="SP"
        )
        assert verdict.eligible is False
        assert verdict.source_basis == "abstract_only"

    def test_malformed_json_returns_sentinel(self) -> None:
        resp = _mock_openai_response("not json at all")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        verdict = classify_eligibility(
            "W1", "prompt", "full_text", model="m", client=mock_client, system_prompt="SP"
        )
        assert verdict.reason == "llm-parse-failure"
        assert verdict.eligible is None
        assert verdict.seed is None
        assert verdict.raw_response == "not json at all"

    def test_missing_eligible_field_returns_sentinel(self) -> None:
        resp = _mock_openai_response('{"reason": "ok"}')
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        verdict = classify_eligibility(
            "W1", "prompt", "full_text", model="m", client=mock_client, system_prompt="SP"
        )
        assert verdict.reason == "llm-parse-failure"
        assert verdict.raw_response == '{"reason": "ok"}'

    def test_extra_fields_dropped(self) -> None:
        # LLMs at t>0 sprinkle extras; they should not fail the record.
        resp = _mock_openai_response(
            '{"eligible": true, "reason": "ok", "confidence": 95}'
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        verdict = classify_eligibility(
            "W1", "prompt", "full_text", model="m", client=mock_client, system_prompt="SP"
        )
        assert verdict.reason == "ok"
        assert verdict.eligible is True

    def test_reason_list_coerced(self) -> None:
        resp = _mock_openai_response(
            '{"eligible": true, "reason": ["a", "b"]}'
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        verdict = classify_eligibility(
            "W1", "prompt", "full_text", model="m", client=mock_client, system_prompt="SP"
        )
        assert verdict.reason == "a / b"

    def test_string_eligible_coerced_by_pydantic(self) -> None:
        # Pydantic v2 accepts "true"/"false" → bool in non-strict mode.
        resp = _mock_openai_response('{"eligible": "true", "reason": "ok"}')
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        verdict = classify_eligibility(
            "W1", "prompt", "full_text", model="m", client=mock_client, system_prompt="SP"
        )
        assert verdict.eligible is True
        assert verdict.reason == "ok"

    def test_timeout_returns_sentinel(self) -> None:
        """APITimeoutError after retries → reason='llm-timeout' sentinel."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = APITimeoutError(
            request=MagicMock()
        )
        verdict = classify_eligibility(
            "W1", "prompt", "full_text", model="m", client=mock_client, system_prompt="SP"
        )
        assert verdict.reason == "llm-timeout"
        assert verdict.eligible is None
        assert verdict.source_basis == "full_text"
        assert verdict.seed is None
        assert verdict.raw_response is None

    def test_connection_error_returns_sentinel(self) -> None:
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = APIConnectionError(
            request=MagicMock()
        )
        verdict = classify_eligibility(
            "W1", "prompt", "abstract_only", model="m", client=mock_client, system_prompt="SP"
        )
        assert verdict.reason == "llm-timeout"
        assert verdict.source_basis == "abstract_only"

    def test_seed_forwarded_to_client(self) -> None:
        resp = _mock_openai_response('{"eligible": true, "reason": "ok"}')
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.random.randint",
            return_value=42,
        ):
            verdict = classify_eligibility(
                "W1", "prompt", "full_text", model="m", client=mock_client, system_prompt="SP"
            )
        assert verdict.seed == 42


# --- assess_works cascade ---


def _mock_classify(
    results: dict[str, dict[str, Any] | str],
) -> Any:
    def side_effect(
        work_id: str,
        prompt: str,
        source_basis: str,
        *,
        model: str,
        client: Any,
        system_prompt: str,
    ) -> EligibilityVerdict:
        entry = results[work_id]
        if entry == "error":
            return EligibilityVerdict(
                work_id=work_id,
                eligible=None,
                source_basis=source_basis,
                reason="llm-parse-failure",
                seed=None,
                raw_response="mock failure",
            )
        assert isinstance(entry, dict)
        return EligibilityVerdict(
            work_id=work_id,
            eligible=entry["eligible"],
            source_basis=source_basis,
            reason=entry["reason"],
            seed=entry.get("seed", 12345),
        )

    return side_effect


class TestAssessWorksCascade:
    def test_full_text_branch(self, tmp_path: Path) -> None:
        extractions_path = tmp_path / "extraction.jsonl"
        output_dir = tmp_path / "ext_out"

        work = _make_work("W1", abstract="The abstract.")

        tei_path = "tei/W1.tei.xml"
        _write_tei(
            output_dir / tei_path,
            f'<div xmlns="{TEI_NS}"><head>Methods</head><p>Real content.</p></div>',
        )
        extracted = ExtractedDocument(
            work_id="W1",
            tei_path=tei_path,
            content_sha256="0" * 64,
            extracted_at="2026-04-17T00:00:00.000000+00:00",
        )
        _write_extractions_jsonl(extractions_path, [extracted])

        extractions = {extracted.work_id: extracted}

        mock_classify = MagicMock(
            side_effect=_mock_classify(
                {"W1": {"eligible": True, "reason": "matches"}}
            )
        )
        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            mock_classify,
        ):
            verdicts = list(
                assess_works(
                    [work],
                    extractions,
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
                    system_prompt="SP",
                )
            )

        assert len(verdicts) == 1
        assert verdicts[0].source_basis == "full_text"
        assert verdicts[0].eligible is True

        # The prompt should contain the flattened section text, not the abstract.
        call_kwargs = mock_classify.call_args
        prompt = call_kwargs.args[1]
        assert "Methods" in prompt
        assert "Real content." in prompt
        assert "full_text:" in prompt
        assert "The abstract." not in prompt

    def test_abstract_only_branch_when_extraction_missing(
        self, tmp_path: Path
    ) -> None:
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract text.")

        mock_classify = MagicMock(
            side_effect=_mock_classify(
                {"W1": {"eligible": False, "reason": "not a match"}}
            )
        )
        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            mock_classify,
        ):
            verdicts = list(
                assess_works(
                    [work],
                    {},
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
                    system_prompt="SP",
                )
            )

        assert len(verdicts) == 1
        assert verdicts[0].source_basis == "abstract_only"
        assert verdicts[0].eligible is False

        prompt = mock_classify.call_args.args[1]
        assert "abstract_only:" in prompt
        assert "Paper abstract text." in prompt

    def test_no_source_sentinel(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract=None)

        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility"
        ) as mock_classify:
            verdicts = list(
                assess_works(
                    [work],
                    {},
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
                    system_prompt="SP",
                )
            )

        assert len(verdicts) == 1
        assert verdicts[0].source_basis == "none"
        assert verdicts[0].eligible is None
        assert verdicts[0].reason == "no-source"
        assert verdicts[0].seed is None
        mock_classify.assert_not_called()

    def test_malformed_tei_no_abstract_fallback(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract.")

        tei_path = "tei/W1.tei.xml"
        _write_malformed_tei(output_dir / tei_path)

        extracted = ExtractedDocument(
            work_id="W1",
            tei_path=tei_path,
            content_sha256="0" * 64,
            extracted_at="2026-04-17T00:00:00.000000+00:00",
        )
        extractions = {extracted.work_id: extracted}

        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility"
        ) as mock_classify:
            verdicts = list(
                assess_works(
                    [work],
                    extractions,
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
                    system_prompt="SP",
                )
            )

        assert len(verdicts) == 1
        assert verdicts[0].reason == "tei-parse-failure"
        assert verdicts[0].eligible is None
        assert verdicts[0].source_basis == "full_text"
        assert verdicts[0].seed is None
        # No abstract fallback: malformed TEI is an operator-visible bug.
        mock_classify.assert_not_called()

    def test_empty_body_falls_back_to_abstract(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract.")

        tei_path = "tei/W1.tei.xml"
        _write_empty_body_tei(output_dir / tei_path)

        extracted = ExtractedDocument(
            work_id="W1",
            tei_path=tei_path,
            content_sha256="0" * 64,
            extracted_at="2026-04-17T00:00:00.000000+00:00",
        )
        extractions = {extracted.work_id: extracted}

        mock_classify = MagicMock(
            side_effect=_mock_classify(
                {"W1": {"eligible": True, "reason": "fine"}}
            )
        )
        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            mock_classify,
        ):
            verdicts = list(
                assess_works(
                    [work],
                    extractions,
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
                    system_prompt="SP",
                )
            )

        assert len(verdicts) == 1
        assert verdicts[0].source_basis == "abstract_only"

    def test_llm_parse_failure_recorded(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract.")

        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            side_effect=_mock_classify({"W1": "error"}),
        ):
            verdicts = list(
                assess_works(
                    [work],
                    {},
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
                    system_prompt="SP",
                )
            )

        assert len(verdicts) == 1
        assert verdicts[0].reason == "llm-parse-failure"
        assert verdicts[0].eligible is None
        assert verdicts[0].source_basis == "abstract_only"
        assert verdicts[0].seed is None

    def test_timeout_does_not_stop_loop(self, tmp_path: Path) -> None:
        """A timeout on one work yields a sentinel; subsequent works still run."""
        output_dir = tmp_path / "ext_out"
        works = [
            _make_work("W1", abstract="first abs"),
            _make_work("W2", abstract="second abs"),
        ]

        def side_effect(
            work_id: str,
            prompt: str,
            source_basis: str,
            *,
            model: str,
            client: Any,
            system_prompt: str,
        ) -> EligibilityVerdict:
            if work_id == "W1":
                return EligibilityVerdict(
                    work_id=work_id,
                    eligible=None,
                    source_basis=source_basis,
                    reason="llm-timeout",
                    seed=None,
                    raw_response=None,
                )
            return EligibilityVerdict(
                work_id=work_id,
                eligible=True,
                source_basis=source_basis,
                reason="ok",
                seed=42,
            )

        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            side_effect=side_effect,
        ):
            verdicts = list(
                assess_works(
                    works,
                    {},
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
                    system_prompt="SP",
                )
            )

        assert len(verdicts) == 2
        by_id = {v.work_id: v for v in verdicts}
        assert by_id["W1"].reason == "llm-timeout"
        assert by_id["W2"].reason == "ok"

    def test_seed_recorded_on_successful_verdict(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Abstract text.")

        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            side_effect=_mock_classify(
                {"W1": {"eligible": True, "reason": "yes", "seed": 99}}
            ),
        ):
            verdicts = list(
                assess_works(
                    [work],
                    {},
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
                    system_prompt="SP",
                )
            )
        assert verdicts[0].seed == 99


# --- EligibilityVerdict model ---


class TestEligibilityVerdict:
    def test_tri_state_eligible(self) -> None:
        EligibilityVerdict(work_id="W1", source_basis="none", eligible=None)
        EligibilityVerdict(work_id="W1", source_basis="full_text", eligible=True)
        EligibilityVerdict(work_id="W1", source_basis="full_text", eligible=False)

    def test_extra_fields_forbidden(self) -> None:
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            EligibilityVerdict(
                work_id="W1",
                source_basis="full_text",
                eligible=True,
                bonus="no",  # type: ignore[call-arg]
            )


# --- _preflight ---


class TestPreflight:
    def test_preflight_raises_on_connection_failure(self) -> None:
        from laglitsynth.fulltext_eligibility.eligibility import _preflight

        args = MagicMock()
        args.base_url = "http://localhost:99999"
        args.model = "nonexistent"

        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.OpenAI"
        ) as mock_cls:
            mock_cls.return_value.models.retrieve.side_effect = Exception(
                "connection refused"
            )
            with pytest.raises(SystemExit):
                _preflight(args)


# --- run() end-to-end ---


_TEST_CRITERIA_SPEC = {
    "id": "test-criteria",
    "system_prompt": "You are a test classifier. Respond with JSON.",
}


def _make_run_args(
    tmp_path: Path,
    *,
    catalogue: Path,
    screening_verdicts: Path,
    extractions: Path,
    dry_run: bool = False,
    skip_existing: bool = False,
    max_records: int | None = None,
    run_id: str = "test-run-id",
    screening_threshold: float = 50.0,
) -> argparse.Namespace:
    return argparse.Namespace(
        catalogue=catalogue,
        screening_verdicts=screening_verdicts,
        screening_threshold=screening_threshold,
        extractions=extractions,
        extraction_output_dir=extractions.parent,
        data_dir=tmp_path,
        run_id=run_id,
        eligibility_criteria=_TEST_CRITERIA_SPEC,
        model="m",
        base_url="http://x",
        max_records=max_records,
        skip_existing=skip_existing,
        dry_run=dry_run,
        config=None,
    )


def _resolved_out_dir(args: argparse.Namespace) -> Path:
    return Path(args.data_dir) / "fulltext-eligibility" / args.run_id


class TestRun:
    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        verdicts_path = tmp_path / "verdicts.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        _write_works_jsonl(catalogue, [_make_work("W1", abstract="abs")])
        _write_verdicts_jsonl(
            verdicts_path,
            [ScreeningVerdict(work_id="W1", relevance_score=80)],
        )
        extractions_path.write_text("")

        args = _make_run_args(
            tmp_path,
            catalogue=catalogue,
            screening_verdicts=verdicts_path,
            extractions=extractions_path,
            dry_run=True,
        )

        with (
            patch("laglitsynth.fulltext_eligibility.eligibility._preflight"),
            patch(
                "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
                side_effect=_mock_classify(
                    {"W1": {"eligible": True, "reason": "ok"}}
                ),
            ),
            patch("laglitsynth.fulltext_eligibility.eligibility.OpenAI"),
        ):
            from laglitsynth.fulltext_eligibility.eligibility import run

            run(args)

        out_dir = _resolved_out_dir(args)
        assert not out_dir.exists() or not any(out_dir.iterdir())

    def test_writes_expected_files(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        verdicts_path = tmp_path / "verdicts.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"

        works = [
            _make_work("W1", abstract="about oceans"),
            _make_work("W2", abstract="not relevant"),
            _make_work("W3", abstract=None),
        ]
        _write_works_jsonl(catalogue, works)
        _write_verdicts_jsonl(
            verdicts_path,
            [ScreeningVerdict(work_id=w.id, relevance_score=80) for w in works],
        )
        extractions_path.write_text("")

        args = _make_run_args(
            tmp_path,
            catalogue=catalogue,
            screening_verdicts=verdicts_path,
            extractions=extractions_path,
        )

        classify_results: dict[str, dict[str, Any] | str] = {
            "W1": {"eligible": True, "reason": "yes"},
            "W2": {"eligible": False, "reason": "no"},
        }

        with (
            patch("laglitsynth.fulltext_eligibility.eligibility._preflight"),
            patch(
                "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
                side_effect=_mock_classify(classify_results),
            ),
            patch("laglitsynth.fulltext_eligibility.eligibility.OpenAI"),
        ):
            from laglitsynth.fulltext_eligibility.eligibility import run

            run(args)

        out_dir = _resolved_out_dir(args)
        assert (out_dir / "verdicts.jsonl").exists()
        assert not (out_dir / "eligible.jsonl").exists()
        assert (out_dir / "eligibility-meta.json").exists()
        assert (out_dir / "config.yaml").exists()

        verdict_lines = [
            l
            for l in (out_dir / "verdicts.jsonl").read_text().splitlines()
            if l.strip()
        ]
        assert len(verdict_lines) == 3

        meta = json.loads((out_dir / "eligibility-meta.json").read_text())
        assert meta["input_count"] == 3
        assert meta["eligible_count"] == 1
        assert meta["excluded_count"] == 1
        assert meta["no_source_count"] == 1
        assert meta["tei_parse_failure_count"] == 0
        assert meta["llm_parse_failure_count"] == 0
        assert meta["by_source_basis"] == {"abstract_only": 2, "none": 1}
        assert meta["run"]["tool"] == "laglitsynth.fulltext_eligibility.assess"
        assert meta["llm"]["temperature"] == 0.8
        assert len(meta["llm"]["prompt_sha256"]) == 64

    def test_skip_existing_refuses_when_prompt_sha256_differs(
        self, tmp_path: Path
    ) -> None:
        """--skip-existing must raise SystemExit when recorded prompt_sha256 differs."""
        import json

        catalogue = tmp_path / "catalogue.jsonl"
        verdicts_path = tmp_path / "verdicts.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        _write_works_jsonl(catalogue, [_make_work("W1", abstract="first abstract")])
        _write_verdicts_jsonl(
            verdicts_path,
            [ScreeningVerdict(work_id="W1", relevance_score=80)],
        )
        extractions_path.write_text("")

        run_id = "stale-run"
        out_dir = tmp_path / "fulltext-eligibility" / run_id
        out_dir.mkdir(parents=True)

        # Write a meta file with a stale/wrong prompt_sha256.
        stale_meta = {
            "run": {
                "tool": "laglitsynth.fulltext_eligibility.assess",
                "run_at": "2026-01-01T00:00:00.000000+00:00",
                "validation_skipped": 0,
            },
            "llm": {
                "model": "gemma3:4b",
                "temperature": 0.8,
                "prompt_sha256": "0" * 64,  # deliberately wrong hash
            },
            "input_catalogue": str(catalogue),
            "input_extractions": str(extractions_path),
            "input_count": 1,
            "eligible_count": 0,
            "excluded_count": 1,
            "no_source_count": 0,
            "tei_parse_failure_count": 0,
            "llm_parse_failure_count": 0,
            "by_source_basis": {"abstract_only": 1},
        }
        (out_dir / "eligibility-meta.json").write_text(json.dumps(stale_meta))

        args = _make_run_args(
            tmp_path,
            catalogue=catalogue,
            screening_verdicts=verdicts_path,
            extractions=extractions_path,
            skip_existing=True,
            run_id=run_id,
        )

        with (
            patch("laglitsynth.fulltext_eligibility.eligibility._preflight"),
            patch("laglitsynth.fulltext_eligibility.eligibility.OpenAI"),
        ):
            from laglitsynth.fulltext_eligibility.eligibility import run

            with pytest.raises(SystemExit, match="prompt_sha256"):
                run(args)

    def test_skip_existing_processes_only_delta(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        verdicts_path = tmp_path / "verdicts.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        works = [
            _make_work("W1", abstract="first abstract"),
            _make_work("W2", abstract="second abstract"),
        ]
        _write_works_jsonl(catalogue, works)
        _write_verdicts_jsonl(
            verdicts_path,
            [ScreeningVerdict(work_id=w.id, relevance_score=80) for w in works],
        )
        extractions_path.write_text("")

        run_id = "resume-run"
        out_dir = tmp_path / "fulltext-eligibility" / run_id
        out_dir.mkdir(parents=True)
        # Prior verdict for W1 only.
        prior = EligibilityVerdict(
            work_id="W1",
            eligible=True,
            source_basis="abstract_only",
            reason="prior",
            seed=1,
        )
        (out_dir / "verdicts.jsonl").write_text(prior.model_dump_json() + "\n")

        args = _make_run_args(
            tmp_path,
            catalogue=catalogue,
            screening_verdicts=verdicts_path,
            extractions=extractions_path,
            skip_existing=True,
            run_id=run_id,
        )

        classify_results: dict[str, dict[str, Any] | str] = {
            "W2": {"eligible": False, "reason": "nope"},
        }

        mock_classify = MagicMock(side_effect=_mock_classify(classify_results))
        with (
            patch("laglitsynth.fulltext_eligibility.eligibility._preflight"),
            patch(
                "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
                mock_classify,
            ),
            patch("laglitsynth.fulltext_eligibility.eligibility.OpenAI"),
        ):
            from laglitsynth.fulltext_eligibility.eligibility import run

            run(args)

        # Only W2 was classified (W1 was skipped).
        assert mock_classify.call_count == 1
        assert mock_classify.call_args.args[0] == "W2"

        verdict_lines = [
            l
            for l in (out_dir / "verdicts.jsonl").read_text().splitlines()
            if l.strip()
        ]
        assert len(verdict_lines) == 2

        meta = json.loads((out_dir / "eligibility-meta.json").read_text())
        assert meta["eligible_count"] == 1
        assert meta["excluded_count"] == 1
        assert meta["no_source_count"] == 0
        assert meta["tei_parse_failure_count"] == 0
        assert meta["llm_parse_failure_count"] == 0


# --- _active_works join ---


class TestActiveWorksJoin:
    """Unit tests for the _active_works inline-join helper."""

    def test_active_works_threshold(self, tmp_path: Path) -> None:
        """Works with a score at or above the threshold pass; those below are filtered."""
        works = [
            _make_work("https://openalex.org/W1"),
            _make_work("https://openalex.org/W2"),
            _make_work("https://openalex.org/W3"),
        ]
        verdicts = [
            ScreeningVerdict(work_id="https://openalex.org/W1", relevance_score=80),
            ScreeningVerdict(work_id="https://openalex.org/W2", relevance_score=49),
            ScreeningVerdict(work_id="https://openalex.org/W3", relevance_score=50),
        ]
        catalogue_path = tmp_path / "catalogue.jsonl"
        verdicts_path = tmp_path / "verdicts.jsonl"
        _write_works_jsonl(catalogue_path, works)
        _write_verdicts_jsonl(verdicts_path, verdicts)

        result = list(_active_works(catalogue_path, verdicts_path, screening_threshold=50.0))
        ids = {w.id for w in result}
        assert ids == {
            "https://openalex.org/W1",
            "https://openalex.org/W3",
        }
        assert "https://openalex.org/W2" not in ids

    def test_null_score_sentinels_ride_through(self, tmp_path: Path) -> None:
        """Works with relevance_score=None (sentinel reasons) always pass, regardless of threshold."""
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
                work_id="https://openalex.org/W4",
                relevance_score=80,
            ),
            ScreeningVerdict(
                work_id="https://openalex.org/W5",
                relevance_score=10,
            ),
        ]
        catalogue_path = tmp_path / "catalogue.jsonl"
        verdicts_path = tmp_path / "verdicts.jsonl"
        _write_works_jsonl(catalogue_path, works)
        _write_verdicts_jsonl(verdicts_path, verdicts)

        result = list(_active_works(catalogue_path, verdicts_path, screening_threshold=50.0))
        ids = {w.id for w in result}
        # W1, W2, W3 have None score → always pass through.
        # W4 has score 80 ≥ 50 → passes.
        # W5 has score 10 < 50 → filtered out.
        assert ids == {
            "https://openalex.org/W1",
            "https://openalex.org/W2",
            "https://openalex.org/W3",
            "https://openalex.org/W4",
        }
        assert "https://openalex.org/W5" not in ids

    def test_works_without_verdicts_are_excluded(self, tmp_path: Path) -> None:
        """Works present in the catalogue but absent from verdicts are excluded."""
        works = [
            _make_work("https://openalex.org/W1"),
            _make_work("https://openalex.org/W2"),
        ]
        verdicts = [
            ScreeningVerdict(work_id="https://openalex.org/W1", relevance_score=90),
        ]
        catalogue_path = tmp_path / "catalogue.jsonl"
        verdicts_path = tmp_path / "verdicts.jsonl"
        _write_works_jsonl(catalogue_path, works)
        _write_verdicts_jsonl(verdicts_path, verdicts)

        result = list(_active_works(catalogue_path, verdicts_path, screening_threshold=50.0))
        assert len(result) == 1
        assert result[0].id == "https://openalex.org/W1"


# --- run() stderr output ---


def test_run_dir_printed_to_stderr_at_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """run() prints 'Run dir: <output_dir>' to stderr at the end of a normal (non-dry) run."""
    catalogue = tmp_path / "catalogue.jsonl"
    verdicts_path = tmp_path / "verdicts.jsonl"
    extractions_path = tmp_path / "extraction.jsonl"
    works = [_make_work("W1", abstract="about oceans")]
    _write_works_jsonl(catalogue, works)
    _write_verdicts_jsonl(
        verdicts_path,
        [ScreeningVerdict(work_id="W1", relevance_score=80)],
    )
    extractions_path.write_text("")

    args = _make_run_args(
        tmp_path,
        catalogue=catalogue,
        screening_verdicts=verdicts_path,
        extractions=extractions_path,
        run_id="test-stderr-run",
    )
    expected_dir = tmp_path / "fulltext-eligibility" / "test-stderr-run"

    with (
        patch("laglitsynth.fulltext_eligibility.eligibility._preflight"),
        patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            side_effect=_mock_classify({"W1": {"eligible": True, "reason": "ok"}}),
        ),
        patch("laglitsynth.fulltext_eligibility.eligibility.OpenAI"),
    ):
        from laglitsynth.fulltext_eligibility.eligibility import run

        run(args)

    err = capsys.readouterr().err
    assert f"Run dir: {expected_dir}" in err
    # The line must appear at the end (last non-empty line).
    last_line = [line for line in err.splitlines() if line.strip()][-1]
    assert last_line == f"Run dir: {expected_dir}"
