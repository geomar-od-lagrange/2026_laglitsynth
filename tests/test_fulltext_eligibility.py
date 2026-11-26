"""Tests for the ``fulltext_eligibility`` stage."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from laglitsynth import layout
from laglitsynth.fulltext_eligibility.eligibility import (
    _active_works,
    assess_works,
    classify_eligibility,
    criteria_sha256,
)
from laglitsynth.fulltext_eligibility.models import EligibilityVerdict
from laglitsynth.fulltext_extraction.models import ExtractedDocument
from laglitsynth.llm import LlmEndpoint
from laglitsynth.prompts import DecisionQuestion
from laglitsynth.screening_abstracts.models import ScreeningVerdict

from conftest import (
    TEI_NS,
    TEST_RUN_ID,
    _make_work,
    _write_extractions_jsonl,
    _write_manifest,
    _write_tei,
    _write_works_jsonl,
)


# --- fixtures and helpers ---

_BASE = "https://llm.example.org/v1"
_ENDPOINT = LlmEndpoint(
    base_url=_BASE, model="m", api_key="k", decisions_url=_BASE + "/decisions"
)
_DECISION_BLOCK = {
    "instructions": "Is this ocean particle tracking primary research?",
    "criteria": {"true": "Tracks particles in the ocean.", "false": "Anything else."},
}
_QUESTION = DecisionQuestion.model_validate(_DECISION_BLOCK)


@pytest.fixture(autouse=True)
def _no_sleep() -> Iterator[None]:
    """Decision retries back off with ``time.sleep``; don't actually wait."""
    with patch("laglitsynth.llm.time.sleep"):
        yield


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


def _write_full_text_for(
    extractions_path: Path, output_dir: Path, work_ids: list[str]
) -> None:
    """Write a renderable TEI + extraction record for each work id.

    Stage 7 is full-text-only, so run()-level tests need each work that
    should be assessed to carry an extraction with non-empty TEI.
    """
    records: list[ExtractedDocument] = []
    for wid in work_ids:
        tei_path = f"tei/{wid.rsplit('/', 1)[-1]}.tei.xml"
        _write_tei(
            output_dir / tei_path,
            f'<div xmlns="{TEI_NS}"><head>Methods</head><p>Body text.</p></div>',
        )
        records.append(
            ExtractedDocument(
                work_id=wid,
                tei_path=tei_path,
                content_sha256="0" * 64,
                extracted_at="2026-04-17T00:00:00.000000+00:00",
            )
        )
    _write_extractions_jsonl(extractions_path, records)


# --- classify_eligibility ---


def _client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _answer(p: float, name: str = "eligible") -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"answers": [{"type": "predicate", "name": name, "probability": p}]}
        )

    return handler


def _classify(client: httpx.Client, text: str = "full text") -> EligibilityVerdict:
    return classify_eligibility(
        "W1", text, endpoint=_ENDPOINT, client=client, question=_QUESTION
    )


class TestClassifyEligibility:
    @pytest.mark.parametrize(("p", "eligible"), [(0.49, False), (0.5, True), (1.0, True)])
    def test_probability_cut_at_half(self, p: float, eligible: bool) -> None:
        verdict = _classify(_client(_answer(p)))
        assert verdict.work_id == "W1"
        assert verdict.probability == p
        assert verdict.eligible is eligible
        assert verdict.reason is None
        assert verdict.raw_response is not None

    def test_request_sends_full_text_as_input_with_name_eligible(self) -> None:
        bodies: list[dict[str, Any]] = []
        urls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            bodies.append(json.loads(request.content))
            urls.append(str(request.url))
            return _answer(0.7)(request)

        _classify(_client(handler), text="Methods\nWe track particles.")
        assert urls == [_BASE + "/decisions"]
        assert bodies[0]["input"] == "Methods\nWe track particles."
        assert bodies[0]["questions"] == [
            {"type": "predicate", "name": "eligible", "instructions": _QUESTION.render()}
        ]

    def test_malformed_body_is_parse_failure(self) -> None:
        verdict = _classify(_client(lambda r: httpx.Response(200, text="not json")))
        assert verdict.reason == "llm-parse-failure"
        assert verdict.probability is None
        assert verdict.eligible is None
        assert verdict.raw_response == "not json"

    def test_answer_for_another_name_is_parse_failure(self) -> None:
        verdict = _classify(_client(_answer(0.9, name="relevant")))
        assert verdict.reason == "llm-parse-failure"
        assert verdict.probability is None

    def test_5xx_is_timeout(self) -> None:
        verdict = _classify(_client(lambda r: httpx.Response(503, text="busy")))
        assert verdict.reason == "llm-timeout"
        assert verdict.probability is None
        assert verdict.eligible is None
        assert verdict.raw_response is None

    def test_400_is_rejected_with_body(self) -> None:
        verdict = _classify(_client(lambda r: httpx.Response(400, text="context too long")))
        assert verdict.reason == "llm-rejected"
        assert verdict.probability is None
        assert verdict.eligible is None
        assert verdict.raw_response == "context too long"

    def test_401_exits_naming_url(self) -> None:
        with pytest.raises(SystemExit, match="/decisions"):
            _classify(_client(lambda r: httpx.Response(401, text="bad key")))


# --- assess_works cascade ---


def _mock_classify(
    results: dict[str, dict[str, Any] | str],
) -> Any:
    def side_effect(
        work_id: str,
        prompt: str,
        *,
        endpoint: LlmEndpoint,
        client: Any,
        question: DecisionQuestion,
    ) -> EligibilityVerdict:
        entry = results[work_id]
        if entry == "error":
            return EligibilityVerdict(
                work_id=work_id,
                eligible=None,
                reason="llm-parse-failure",
                raw_response="mock failure",
            )
        assert isinstance(entry, dict)
        probability = entry.get("probability", 0.9 if entry["eligible"] else 0.1)
        return EligibilityVerdict(
            work_id=work_id,
            probability=probability,
            eligible=entry["eligible"],
            raw_response="{}",
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
                {"W1": {"eligible": True}}
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
                    endpoint=_ENDPOINT,
                    max_records=None,
                    question=_QUESTION,
                )
            )

        assert len(verdicts) == 1
        assert verdicts[0].eligible is True

        # The input is the rendered full text itself, not the abstract.
        text = mock_classify.call_args.args[1]
        assert text == "Methods\nReal content."

    def test_no_extraction_is_not_assessed(self, tmp_path: Path) -> None:
        """A work without an extraction gets no row and never calls the LLM."""
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract text.")

        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility"
        ) as mock_classify:
            verdicts = list(
                assess_works(
                    [work],
                    {},
                    output_dir,
                    client=MagicMock(),
                    endpoint=_ENDPOINT,
                    max_records=None,
                    question=_QUESTION,
                )
            )

        assert verdicts == []
        mock_classify.assert_not_called()

    def test_malformed_tei_records_sentinel(self, tmp_path: Path) -> None:
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
                    endpoint=_ENDPOINT,
                    max_records=None,
                    question=_QUESTION,
                )
            )

        assert len(verdicts) == 1
        assert verdicts[0].reason == "tei-parse-failure"
        assert verdicts[0].eligible is None
        assert verdicts[0].probability is None
        # Malformed TEI on a paper we have a PDF for is an operator-visible bug.
        mock_classify.assert_not_called()

    def test_empty_body_records_sentinel(self, tmp_path: Path) -> None:
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

        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility"
        ) as mock_classify:
            verdicts = list(
                assess_works(
                    [work],
                    extractions,
                    output_dir,
                    client=MagicMock(),
                    endpoint=_ENDPOINT,
                    max_records=None,
                    question=_QUESTION,
                )
            )

        # Empty body (valid XML, no content): no abstract fallback — a paper
        # we have a PDF for but extracted nothing usable is a broken extraction.
        assert len(verdicts) == 1
        assert verdicts[0].reason == "tei-parse-failure"
        assert verdicts[0].eligible is None
        assert verdicts[0].probability is None
        mock_classify.assert_not_called()

    def test_llm_parse_failure_recorded(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract.")

        tei_path = "tei/W1.tei.xml"
        _write_tei(
            output_dir / tei_path,
            f'<div xmlns="{TEI_NS}"><head>Methods</head><p>Body.</p></div>',
        )
        extracted = ExtractedDocument(
            work_id="W1",
            tei_path=tei_path,
            content_sha256="0" * 64,
            extracted_at="2026-04-17T00:00:00.000000+00:00",
        )
        extractions = {extracted.work_id: extracted}

        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            side_effect=_mock_classify({"W1": "error"}),
        ):
            verdicts = list(
                assess_works(
                    [work],
                    extractions,
                    output_dir,
                    client=MagicMock(),
                    endpoint=_ENDPOINT,
                    max_records=None,
                    question=_QUESTION,
                )
            )

        assert len(verdicts) == 1
        assert verdicts[0].reason == "llm-parse-failure"
        assert verdicts[0].eligible is None

    def test_timeout_does_not_stop_loop(self, tmp_path: Path) -> None:
        """A timeout on one work yields a sentinel; subsequent works still run."""
        output_dir = tmp_path / "ext_out"
        works = [
            _make_work("W1", abstract="first abs"),
            _make_work("W2", abstract="second abs"),
        ]

        extractions: dict[str, ExtractedDocument] = {}
        for w in works:
            tei_path = f"tei/{w.id}.tei.xml"
            _write_tei(
                output_dir / tei_path,
                f'<div xmlns="{TEI_NS}"><head>H</head><p>body</p></div>',
            )
            extractions[w.id] = ExtractedDocument(
                work_id=w.id,
                tei_path=tei_path,
                content_sha256="0" * 64,
                extracted_at="2026-04-17T00:00:00.000000+00:00",
            )

        def side_effect(
            work_id: str,
            prompt: str,
            *,
            endpoint: LlmEndpoint,
            client: Any,
            question: DecisionQuestion,
        ) -> EligibilityVerdict:
            if work_id == "W1":
                return EligibilityVerdict(
                    work_id=work_id,
                    eligible=None,
                    reason="llm-timeout",
                    raw_response=None,
                )
            return EligibilityVerdict(
                work_id=work_id,
                probability=0.8,
                eligible=True,
            )

        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            side_effect=side_effect,
        ):
            verdicts = list(
                assess_works(
                    works,
                    extractions,
                    output_dir,
                    client=MagicMock(),
                    endpoint=_ENDPOINT,
                    max_records=None,
                    question=_QUESTION,
                )
            )

        assert len(verdicts) == 2
        by_id = {v.work_id: v for v in verdicts}
        assert by_id["W1"].reason == "llm-timeout"
        assert by_id["W2"].eligible is True
        assert by_id["W2"].reason is None


# --- EligibilityVerdict model ---


class TestEligibilityVerdict:
    def test_tri_state_eligible(self) -> None:
        EligibilityVerdict(work_id="W1", eligible=None)
        EligibilityVerdict(work_id="W1", eligible=True)
        EligibilityVerdict(work_id="W1", eligible=False)

    def test_extra_fields_forbidden(self) -> None:
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            EligibilityVerdict(
                work_id="W1",
                eligible=True,
                bonus="no",  # type: ignore[call-arg]
            )

    def test_source_basis_key_rejected(self) -> None:
        """source_basis is gone; supplying it is now an unknown-field error."""
        from pydantic import ValidationError as PydanticValidationError

        with pytest.raises(PydanticValidationError):
            EligibilityVerdict(
                work_id="W1",
                eligible=True,
                source_basis="full_text",  # type: ignore[call-arg]
            )


# --- run() end-to-end ---


_TEST_CRITERIA_SPEC: dict[str, Any] = {"id": "test-criteria", "decision": _DECISION_BLOCK}


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
    concurrency: int = 1,
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
        base_url=_BASE,
        api_key="k",
        decisions_url=None,
        max_records=max_records,
        skip_existing=skip_existing,
        dry_run=dry_run,
        config=None,
        concurrency=concurrency,
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
        _write_full_text_for(extractions_path, extractions_path.parent, ["W1"])

        args = _make_run_args(
            tmp_path,
            catalogue=catalogue,
            screening_verdicts=verdicts_path,
            extractions=extractions_path,
            dry_run=True,
        )

        with (
            patch(
                "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
                side_effect=_mock_classify(
                    {"W1": {"eligible": True}}
                ),
            ),
            patch("laglitsynth.fulltext_eligibility.eligibility.decisions_client"),
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
        # W1, W2 have full text; W3 has no extraction → not assessed, not counted.
        _write_full_text_for(extractions_path, extractions_path.parent, ["W1", "W2"])

        args = _make_run_args(
            tmp_path,
            catalogue=catalogue,
            screening_verdicts=verdicts_path,
            extractions=extractions_path,
        )

        classify_results: dict[str, dict[str, Any] | str] = {
            "W1": {"eligible": True},
            "W2": {"eligible": False},
        }

        with (
            patch(
                "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
                side_effect=_mock_classify(classify_results),
            ),
            patch("laglitsynth.fulltext_eligibility.eligibility.decisions_client"),
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
        # W3 has no extraction: full-text-only means it is not assessed.
        assert len(verdict_lines) == 2

        meta = json.loads((out_dir / "eligibility-meta.json").read_text())
        assert meta["input_count"] == 2
        assert meta["eligible_count"] == 1
        assert meta["excluded_count"] == 1
        assert "no_source_count" not in meta
        assert meta["tei_parse_failure_count"] == 0
        assert meta["llm_parse_failure_count"] == 0
        assert "by_source_basis" not in meta
        assert meta["input_screening_verdicts"] == str(verdicts_path)
        assert meta["run"]["tool"] == "laglitsynth.fulltext_eligibility.assess"
        assert meta["llm"]["base_url"] == _BASE
        assert meta["llm"]["decisions_url"] == _BASE + "/decisions"
        assert "temperature" not in meta["llm"]
        assert meta["llm_rejected_count"] == 0
        assert meta["llm"]["prompt_sha256"] == criteria_sha256(_TEST_CRITERIA_SPEC)
        assert meta["criterion"] == _QUESTION.render()

        verdicts = [
            EligibilityVerdict.model_validate_json(line) for line in verdict_lines
        ]
        assert {v.work_id: v.probability for v in verdicts} == {"W1": 0.9, "W2": 0.1}

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
        _write_full_text_for(extractions_path, extractions_path.parent, ["W1"])

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
                "model": "m",
                "base_url": _BASE,
                "decisions_url": _BASE + "/decisions",
                "prompt_sha256": "0" * 64,  # deliberately wrong hash
            },
            "input_catalogue": str(catalogue),
            "input_screening_verdicts": str(verdicts_path),
            "input_extractions": str(extractions_path),
            "input_count": 1,
            "eligible_count": 0,
            "excluded_count": 1,
            "tei_parse_failure_count": 0,
            "llm_parse_failure_count": 0,
            "llm_timeout_count": 0,
        }
        # Validate the dict is a real valid EligibilityMeta shape; if the model
        # changes, this assertion fails loudly instead of silently.
        from laglitsynth.fulltext_eligibility.models import EligibilityMeta

        EligibilityMeta.model_validate(stale_meta)
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
            patch("laglitsynth.fulltext_eligibility.eligibility.decisions_client"),
        ):
            from laglitsynth.fulltext_eligibility.eligibility import run

            with pytest.raises(SystemExit, match="prompt_sha256"):
                run(args)

    def test_validation_skipped_counts_invalid_catalogue_lines(
        self, tmp_path: Path
    ) -> None:
        """meta.run.validation_skipped reflects malformed lines in catalogue and verdicts."""
        # One valid work with a valid screening verdict; one malformed line in
        # each of catalogue and verdicts.  Two skipped total.
        catalogue = tmp_path / "catalogue.jsonl"
        verdicts_path = tmp_path / "verdicts.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"

        work = _make_work("W1", abstract="about oceans")

        with open(catalogue, "w") as f:
            f.write(work.model_dump_json() + "\n")
            f.write('{"not_a_real_field": "x"}\n')

        valid_sv = ScreeningVerdict(work_id="W1", relevance_score=80)
        with open(verdicts_path, "w") as f:
            f.write(valid_sv.model_dump_json() + "\n")
            f.write('{"not_a_real_field": "y"}\n')

        _write_full_text_for(extractions_path, extractions_path.parent, ["W1"])

        args = _make_run_args(
            tmp_path,
            catalogue=catalogue,
            screening_verdicts=verdicts_path,
            extractions=extractions_path,
            run_id="skipped-test-run",
        )

        with (
            patch(
                "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
                side_effect=_mock_classify({"W1": {"eligible": True}}),
            ),
            patch("laglitsynth.fulltext_eligibility.eligibility.decisions_client"),
        ):
            from laglitsynth.fulltext_eligibility.eligibility import run

            run(args)

        import json as _json

        out_dir = _resolved_out_dir(args)
        meta = _json.loads((out_dir / "eligibility-meta.json").read_text())
        assert meta["run"]["validation_skipped"] == 2

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
        _write_full_text_for(extractions_path, extractions_path.parent, ["W1", "W2"])

        run_id = "resume-run"
        out_dir = tmp_path / "fulltext-eligibility" / run_id
        out_dir.mkdir(parents=True)
        # Prior verdict for W1 only.
        prior = EligibilityVerdict(
            work_id="W1",
            probability=0.9,
            eligible=True,
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
            "W2": {"eligible": False},
        }

        mock_classify = MagicMock(side_effect=_mock_classify(classify_results))
        with (
            patch(
                "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
                mock_classify,
            ),
            patch("laglitsynth.fulltext_eligibility.eligibility.decisions_client"),
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
        assert "no_source_count" not in meta
        assert meta["tei_parse_failure_count"] == 0
        assert meta["llm_parse_failure_count"] == 0


def _one_work_inputs(tmp_path: Path) -> argparse.Namespace:
    catalogue = tmp_path / "catalogue.jsonl"
    verdicts_path = tmp_path / "verdicts.jsonl"
    extractions_path = tmp_path / "extraction.jsonl"
    _write_works_jsonl(catalogue, [_make_work("W1", abstract="abs")])
    _write_verdicts_jsonl(verdicts_path, [ScreeningVerdict(work_id="W1", relevance_score=80)])
    _write_full_text_for(extractions_path, extractions_path.parent, ["W1"])
    return _make_run_args(
        tmp_path,
        catalogue=catalogue,
        screening_verdicts=verdicts_path,
        extractions=extractions_path,
    )


class TestRunOnMockEndpoint:
    """run() against a mocked decisions endpoint, without patching classify."""

    def _run(
        self, args: argparse.Namespace, handler: Callable[[httpx.Request], httpx.Response]
    ) -> dict[str, Any]:
        with patch(
            "laglitsynth.fulltext_eligibility.eligibility.decisions_client",
            return_value=_client(handler),
        ):
            from laglitsynth.fulltext_eligibility.eligibility import run

            run(args)
        meta: dict[str, Any] = json.loads(
            (_resolved_out_dir(args) / "eligibility-meta.json").read_text()
        )
        return meta

    def test_rejected_full_text_is_counted(self, tmp_path: Path) -> None:
        args = _one_work_inputs(tmp_path)
        meta = self._run(args, lambda r: httpx.Response(400, text="too long"))
        assert meta["llm_rejected_count"] == 1
        assert meta["eligible_count"] == 0
        line = (_resolved_out_dir(args) / "verdicts.jsonl").read_text().splitlines()[0]
        verdict = EligibilityVerdict.model_validate_json(line)
        assert verdict.reason == "llm-rejected"
        assert verdict.probability is None

    def test_decided_verdict_carries_probability(self, tmp_path: Path) -> None:
        args = _one_work_inputs(tmp_path)
        meta = self._run(args, _answer(0.62))
        assert meta["eligible_count"] == 1
        line = (_resolved_out_dir(args) / "verdicts.jsonl").read_text().splitlines()[0]
        verdict = EligibilityVerdict.model_validate_json(line)
        assert verdict.probability == 0.62
        assert verdict.eligible is True

    def test_criteria_without_decision_block_exits(self, tmp_path: Path) -> None:
        args = _one_work_inputs(tmp_path)
        args.eligibility_criteria = {"id": "x", "system_prompt": "old shape"}
        with pytest.raises(SystemExit, match="no 'decision' block"):
            self._run(args, _answer(0.9))
        assert not _resolved_out_dir(args).exists()


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
    _write_full_text_for(extractions_path, extractions_path.parent, ["W1"])

    args = _make_run_args(
        tmp_path,
        catalogue=catalogue,
        screening_verdicts=verdicts_path,
        extractions=extractions_path,
        run_id="test-stderr-run",
    )
    expected_dir = tmp_path / "fulltext-eligibility" / "test-stderr-run"

    with (
        patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            side_effect=_mock_classify({"W1": {"eligible": True}}),
        ),
        patch("laglitsynth.fulltext_eligibility.eligibility.decisions_client"),
    ):
        from laglitsynth.fulltext_eligibility.eligibility import run

        run(args)

    err = capsys.readouterr().err
    assert f"Run dir: {expected_dir}" in err
    # The line must appear at the end (last non-empty line).
    last_line = [line for line in err.splitlines() if line.strip()][-1]
    assert last_line == f"Run dir: {expected_dir}"


# --- --concurrency ---


def test_concurrency_default_is_one() -> None:
    """Stage 7 --concurrency defaults to 1."""
    import argparse as _argparse

    from laglitsynth.fulltext_eligibility.eligibility import build_subparser

    p = _argparse.ArgumentParser()
    subs = p.add_subparsers()
    build_subparser(subs)
    args = p.parse_args(
        [
            "fulltext-eligibility",
            "--catalogue", "c.jsonl",
            "--screening-verdicts", "v.jsonl",
            "--extractions", "e.jsonl",
        ]
    )
    assert args.concurrency == 1


def test_concurrency_flag_threaded_to_assess_works(tmp_path: Path) -> None:
    """--concurrency N is forwarded to assess_works as concurrency=N."""
    catalogue = tmp_path / "catalogue.jsonl"
    verdicts_path = tmp_path / "verdicts.jsonl"
    extractions_path = tmp_path / "extraction.jsonl"
    works = [_make_work("W1", abstract="abs")]
    _write_works_jsonl(catalogue, works)
    _write_verdicts_jsonl(
        verdicts_path, [ScreeningVerdict(work_id="W1", relevance_score=80)]
    )
    _write_full_text_for(extractions_path, extractions_path.parent, ["W1"])

    args = _make_run_args(
        tmp_path,
        catalogue=catalogue,
        screening_verdicts=verdicts_path,
        extractions=extractions_path,
        concurrency=3,
    )

    captured: dict[str, Any] = {}
    original_assess = __import__(
        "laglitsynth.fulltext_eligibility.eligibility", fromlist=["assess_works"]
    ).assess_works

    def spy_assess(*a: Any, **kw: Any) -> Any:
        captured["concurrency"] = kw.get("concurrency")
        return original_assess(*a, **kw)

    with (
        patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            side_effect=_mock_classify({"W1": {"eligible": True}}),
        ),
        patch("laglitsynth.fulltext_eligibility.eligibility.decisions_client"),
        patch(
            "laglitsynth.fulltext_eligibility.eligibility.assess_works",
            side_effect=spy_assess,
        ),
    ):
        from laglitsynth.fulltext_eligibility.eligibility import run

        run(args)

    assert captured["concurrency"] == 3


def test_concurrency_one_uses_sequential_path(tmp_path: Path) -> None:
    """concurrency=1 does not create a ThreadPoolExecutor."""
    from concurrent.futures import ThreadPoolExecutor as TPE

    catalogue = tmp_path / "catalogue.jsonl"
    verdicts_path = tmp_path / "verdicts.jsonl"
    extractions_path = tmp_path / "extraction.jsonl"
    works = [_make_work("W1", abstract="abs")]
    _write_works_jsonl(catalogue, works)
    _write_verdicts_jsonl(
        verdicts_path, [ScreeningVerdict(work_id="W1", relevance_score=80)]
    )
    _write_full_text_for(extractions_path, extractions_path.parent, ["W1"])

    args = _make_run_args(
        tmp_path,
        catalogue=catalogue,
        screening_verdicts=verdicts_path,
        extractions=extractions_path,
        concurrency=1,
    )

    with (
        patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            side_effect=_mock_classify({"W1": {"eligible": True}}),
        ),
        patch("laglitsynth.fulltext_eligibility.eligibility.decisions_client"),
        patch("laglitsynth.concurrency.ThreadPoolExecutor", wraps=TPE) as mock_tpe,
    ):
        from laglitsynth.fulltext_eligibility.eligibility import run

        run(args)

    mock_tpe.assert_not_called()


# --- run() conventional input paths ---


class TestConventionalPaths:
    def _layout(self, data_dir: Path, run_id: str = TEST_RUN_ID) -> None:
        catalogue = layout.deduplicated_path(data_dir)
        catalogue.parent.mkdir(parents=True, exist_ok=True)
        _write_works_jsonl(catalogue, [_make_work("W1", abstract="about oceans")])
        verdicts_path = layout.screening_verdicts_path(data_dir, run_id)
        verdicts_path.parent.mkdir(parents=True, exist_ok=True)
        _write_verdicts_jsonl(
            verdicts_path, [ScreeningVerdict(work_id="W1", relevance_score=80)]
        )
        extractions = layout.extractions_path(data_dir)
        extractions.parent.mkdir(parents=True, exist_ok=True)
        _write_full_text_for(extractions, extractions.parent, ["W1"])

    def _bare_args(self, data_dir: Path) -> argparse.Namespace:
        args = _make_run_args(
            data_dir,
            catalogue=Path("unused"),
            screening_verdicts=Path("unused"),
            extractions=Path("unused"),
        )
        args.catalogue = None
        args.screening_verdicts = None
        args.extractions = None
        args.extraction_output_dir = None
        args.run_id = None
        return args

    def _run(self, args: argparse.Namespace) -> None:
        with (
            patch(
                "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
                side_effect=_mock_classify({"W1": {"eligible": True}}),
            ),
            patch("laglitsynth.fulltext_eligibility.eligibility.decisions_client"),
        ):
            from laglitsynth.fulltext_eligibility.eligibility import run

            run(args)

    def test_omitted_flags_resolve_to_conventional_paths(self, tmp_path: Path) -> None:
        self._layout(tmp_path)
        _write_manifest(tmp_path)

        self._run(self._bare_args(tmp_path))

        out_dir = tmp_path / layout.ELIGIBILITY_SUBDIR / TEST_RUN_ID
        meta = json.loads((out_dir / "eligibility-meta.json").read_text())
        assert meta["input_catalogue"] == str(layout.deduplicated_path(tmp_path))
        assert meta["input_screening_verdicts"] == str(
            layout.screening_verdicts_path(tmp_path, TEST_RUN_ID)
        )
        assert meta["input_extractions"] == str(layout.extractions_path(tmp_path))
        assert meta["input_count"] == 1

    def test_explicit_flags_win(self, tmp_path: Path) -> None:
        self._layout(tmp_path)
        _write_manifest(tmp_path, run_id="from-manifest")

        explicit_catalogue = tmp_path / "explicit-catalogue.jsonl"
        _write_works_jsonl(explicit_catalogue, [_make_work("W1", abstract="abs")])
        explicit_verdicts = tmp_path / "explicit-verdicts.jsonl"
        _write_verdicts_jsonl(
            explicit_verdicts, [ScreeningVerdict(work_id="W1", relevance_score=80)]
        )
        args = self._bare_args(tmp_path)
        args.catalogue = explicit_catalogue
        args.screening_verdicts = explicit_verdicts
        args.run_id = "explicit-run-id"
        self._run(args)

        out_dir = tmp_path / layout.ELIGIBILITY_SUBDIR / "explicit-run-id"
        assert not (tmp_path / layout.ELIGIBILITY_SUBDIR / "from-manifest").exists()
        meta = json.loads((out_dir / "eligibility-meta.json").read_text())
        assert meta["input_catalogue"] == str(explicit_catalogue)
        assert meta["input_screening_verdicts"] == str(explicit_verdicts)

    def test_missing_extractions_names_upstream_and_flag(self, tmp_path: Path) -> None:
        self._layout(tmp_path)
        layout.extractions_path(tmp_path).unlink()
        _write_manifest(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            self._run(self._bare_args(tmp_path))
        msg = str(exc_info.value)
        assert "run fulltext-extraction first" in msg
        assert "--extractions" in msg

    def test_missing_catalogue_names_upstream_and_flag(self, tmp_path: Path) -> None:
        _write_manifest(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            self._run(self._bare_args(tmp_path))
        msg = str(exc_info.value)
        assert "run catalogue-dedup first" in msg
        assert "--catalogue" in msg

    def test_no_manifest_and_no_run_id_exits(self, tmp_path: Path) -> None:
        self._layout(tmp_path)

        with pytest.raises(SystemExit) as exc_info:
            self._run(self._bare_args(tmp_path))
        assert "--run-id" in str(exc_info.value)
        assert not (tmp_path / layout.ELIGIBILITY_SUBDIR).exists()
