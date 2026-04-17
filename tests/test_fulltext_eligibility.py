"""Tests for the ``fulltext_eligibility`` stage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.fulltext_eligibility.eligibility import (
    assess_works,
    classify_eligibility,
)
from laglitsynth.fulltext_eligibility.models import EligibilityVerdict
from laglitsynth.fulltext_extraction.models import ExtractedDocument

TEI_NS = "http://www.tei-c.org/ns/1.0"


# --- fixtures and helpers ---


def _make_work(
    work_id: str = "https://openalex.org/W1",
    title: str = "Test Paper",
    abstract: str | None = "An abstract about ocean currents.",
) -> Work:
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


def _write_works_jsonl(path: Path, works: list[Work]) -> None:
    with open(path, "w") as f:
        for w in works:
            f.write(w.model_dump_json() + "\n")


def _write_extractions_jsonl(path: Path, records: list[ExtractedDocument]) -> None:
    with open(path, "w") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")


def _mock_openai_response(content: str) -> MagicMock:
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


def _write_tei(path: Path, body_content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<TEI xmlns="{TEI_NS}"><text><body>{body_content}</body></text></TEI>'
        ).encode()
    )


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
            "W1", "prompt text", "full_text", model="m", client=mock_client
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
            "W1", "prompt text", "abstract_only", model="m", client=mock_client
        )
        assert verdict.eligible is False
        assert verdict.source_basis == "abstract_only"

    def test_malformed_json_returns_sentinel(self) -> None:
        resp = _mock_openai_response("not json at all")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        verdict = classify_eligibility(
            "W1", "prompt", "full_text", model="m", client=mock_client
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
            "W1", "prompt", "full_text", model="m", client=mock_client
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
            "W1", "prompt", "full_text", model="m", client=mock_client
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
            "W1", "prompt", "full_text", model="m", client=mock_client
        )
        assert verdict.reason == "a / b"

    def test_string_eligible_coerced_by_pydantic(self) -> None:
        # Pydantic v2 accepts "true"/"false" → bool in non-strict mode.
        resp = _mock_openai_response('{"eligible": "true", "reason": "ok"}')
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        verdict = classify_eligibility(
            "W1", "prompt", "full_text", model="m", client=mock_client
        )
        assert verdict.eligible is True
        assert verdict.reason == "ok"

    def test_seed_forwarded_to_client(self) -> None:
        resp = _mock_openai_response('{"eligible": true, "reason": "ok"}')
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.random.randint",
            return_value=42,
        ):
            verdict = classify_eligibility(
                "W1", "prompt", "full_text", model="m", client=mock_client
            )
        assert verdict.seed == 42
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        assert call_kwargs["seed"] == 42
        assert call_kwargs["temperature"] == 0.8


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
        catalogue = tmp_path / "catalogue.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        output_dir = tmp_path / "ext_out"

        work = _make_work("W1", abstract="The abstract.")
        _write_works_jsonl(catalogue, [work])

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
                    catalogue,
                    extractions,
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
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
        catalogue = tmp_path / "catalogue.jsonl"
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract text.")
        _write_works_jsonl(catalogue, [work])

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
                    catalogue,
                    {},
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
                )
            )

        assert len(verdicts) == 1
        assert verdicts[0].source_basis == "abstract_only"
        assert verdicts[0].eligible is False

        prompt = mock_classify.call_args.args[1]
        assert "abstract_only:" in prompt
        assert "Paper abstract text." in prompt

    def test_no_source_sentinel(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract=None)
        _write_works_jsonl(catalogue, [work])

        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility"
        ) as mock_classify:
            verdicts = list(
                assess_works(
                    catalogue,
                    {},
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
                )
            )

        assert len(verdicts) == 1
        assert verdicts[0].source_basis == "none"
        assert verdicts[0].eligible is None
        assert verdicts[0].reason == "no-source"
        assert verdicts[0].seed is None
        mock_classify.assert_not_called()

    def test_malformed_tei_no_abstract_fallback(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract.")
        _write_works_jsonl(catalogue, [work])

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
                    catalogue,
                    extractions,
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
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
        catalogue = tmp_path / "catalogue.jsonl"
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract.")
        _write_works_jsonl(catalogue, [work])

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
                    catalogue,
                    extractions,
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
                )
            )

        assert len(verdicts) == 1
        assert verdicts[0].source_basis == "abstract_only"

    def test_llm_parse_failure_recorded(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract.")
        _write_works_jsonl(catalogue, [work])

        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            side_effect=_mock_classify({"W1": "error"}),
        ):
            verdicts = list(
                assess_works(
                    catalogue,
                    {},
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
                )
            )

        assert len(verdicts) == 1
        assert verdicts[0].reason == "llm-parse-failure"
        assert verdicts[0].eligible is None
        assert verdicts[0].source_basis == "abstract_only"
        assert verdicts[0].seed is None

    def test_seed_recorded_on_successful_verdict(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Abstract text.")
        _write_works_jsonl(catalogue, [work])

        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            side_effect=_mock_classify(
                {"W1": {"eligible": True, "reason": "yes", "seed": 99}}
            ),
        ):
            verdicts = list(
                assess_works(
                    catalogue,
                    {},
                    output_dir,
                    client=MagicMock(),
                    model="m",
                    max_records=None,
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


def _make_run_args(
    tmp_path: Path,
    *,
    catalogue: Path,
    extractions: Path,
    dry_run: bool = False,
    skip_existing: bool = False,
    max_records: int | None = None,
) -> MagicMock:
    args = MagicMock()
    args.catalogue = catalogue
    args.extractions = extractions
    args.extraction_output_dir = extractions.parent
    args.output_dir = tmp_path / "out"
    args.model = "m"
    args.base_url = "http://x"
    args.max_records = max_records
    args.skip_existing = skip_existing
    args.dry_run = dry_run
    return args


class TestRun:
    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        _write_works_jsonl(catalogue, [_make_work("W1", abstract="abs")])
        extractions_path.write_text("")

        args = _make_run_args(
            tmp_path,
            catalogue=catalogue,
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

        assert not (args.output_dir).exists() or not any(
            (args.output_dir).iterdir()
        )

    def test_writes_expected_files(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"

        works = [
            _make_work("W1", abstract="about oceans"),
            _make_work("W2", abstract="not relevant"),
            _make_work("W3", abstract=None),
        ]
        _write_works_jsonl(catalogue, works)
        extractions_path.write_text("")

        args = _make_run_args(
            tmp_path,
            catalogue=catalogue,
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

        out_dir = args.output_dir
        assert (out_dir / "verdicts.jsonl").exists()
        assert (out_dir / "eligible.jsonl").exists()
        assert (out_dir / "eligibility-meta.json").exists()

        verdict_lines = [
            l
            for l in (out_dir / "verdicts.jsonl").read_text().splitlines()
            if l.strip()
        ]
        assert len(verdict_lines) == 3

        eligible_lines = [
            l
            for l in (out_dir / "eligible.jsonl").read_text().splitlines()
            if l.strip()
        ]
        # Only W1 is eligible.
        assert len(eligible_lines) == 1
        assert json.loads(eligible_lines[0])["id"] == "W1"

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

    def test_skip_existing_processes_only_delta(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        works = [
            _make_work("W1", abstract="first abstract"),
            _make_work("W2", abstract="second abstract"),
        ]
        _write_works_jsonl(catalogue, works)
        extractions_path.write_text("")

        out_dir = tmp_path / "out"
        out_dir.mkdir()
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
            extractions=extractions_path,
            skip_existing=True,
        )
        args.output_dir = out_dir

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

        eligible_lines = [
            l
            for l in (out_dir / "eligible.jsonl").read_text().splitlines()
            if l.strip()
        ]
        assert len(eligible_lines) == 1
        assert json.loads(eligible_lines[0])["id"] == "W1"
