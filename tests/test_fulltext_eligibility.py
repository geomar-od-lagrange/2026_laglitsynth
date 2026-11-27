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
    assess_works,
    classify_eligibility,
    criteria_sha256,
)
from laglitsynth.fulltext_eligibility.models import EligibilityVerdict
from laglitsynth.fulltext_extraction.models import ExtractedDocument
from laglitsynth.llm import LlmEndpoint
from laglitsynth.prompts import DecisionQuestion

from conftest import (
    TEI_NS,
    TEST_ANALYSIS_RUN_ID,
    _make_work,
    _write_corpus,
    _write_extractions_jsonl,
    _write_full_text_for,
    _write_tei,
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
            pdf_sha256="0" * 64,
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
            pdf_sha256="0" * 64,
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
            pdf_sha256="0" * 64,
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
            pdf_sha256="0" * 64,
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
                pdf_sha256="0" * 64,
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


def _inputs(
    tmp_path: Path,
    work_ids: list[str],
    *,
    full_text: list[str] | None = None,
) -> tuple[Path, Path]:
    """Write a corpus of ``work_ids`` and stage 6 output for ``full_text``.

    ``full_text`` defaults to every work. Returns the corpus dir and the
    extractions path.
    """
    corpus = tmp_path / "corpus"
    _write_corpus(corpus, [_make_work(w, abstract="abs") for w in work_ids])
    extractions = tmp_path / "fulltext-extraction" / "extraction.jsonl"
    extractions.parent.mkdir(parents=True, exist_ok=True)
    _write_full_text_for(extractions, full_text if full_text is not None else work_ids, corpus)
    return corpus, extractions


def _make_run_args(
    tmp_path: Path,
    *,
    corpus_dir: Path,
    extractions: Path,
    dry_run: bool = False,
    skip_existing: bool = False,
    max_records: int | None = None,
    run_id: str | None = "test-run-id",
    concurrency: int = 1,
) -> argparse.Namespace:
    return argparse.Namespace(
        corpus_dir=corpus_dir,
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


def _one_work_inputs(tmp_path: Path, **kwargs: Any) -> argparse.Namespace:
    corpus, extractions = _inputs(tmp_path, ["W1"])
    return _make_run_args(tmp_path, corpus_dir=corpus, extractions=extractions, **kwargs)


def _resolved_out_dir(args: argparse.Namespace) -> Path:
    return Path(args.data_dir) / "fulltext-eligibility" / args.run_id


def _run_mocked(
    args: argparse.Namespace, results: dict[str, dict[str, Any] | str]
) -> MagicMock:
    """Run stage 7 with ``classify_eligibility`` mocked; return the mock."""
    mock_classify = MagicMock(side_effect=_mock_classify(results))
    with (
        patch(
            "laglitsynth.fulltext_eligibility.eligibility.classify_eligibility",
            mock_classify,
        ),
        patch("laglitsynth.fulltext_eligibility.eligibility.decisions_client"),
    ):
        from laglitsynth.fulltext_eligibility.eligibility import run

        run(args)
    return mock_classify


def _meta(args: argparse.Namespace) -> dict[str, Any]:
    meta: dict[str, Any] = json.loads(
        (_resolved_out_dir(args) / "eligibility-meta.json").read_text()
    )
    return meta


def _stale_meta(corpus_id: str, prompt_sha256: str) -> dict[str, Any]:
    """A valid ``EligibilityMeta`` dict for the resume guards."""
    from laglitsynth.fulltext_eligibility.models import EligibilityMeta

    meta = {
        "run": {
            "tool": "laglitsynth.fulltext_eligibility.assess",
            "run_at": "2026-01-01T00:00:00.000000+00:00",
            "validation_skipped": 0,
        },
        "llm": {
            "model": "m",
            "base_url": _BASE,
            "decisions_url": _BASE + "/decisions",
            "reasoning_effort": None,
            "prompt_sha256": prompt_sha256,
        },
        "corpus_id": corpus_id,
        "input_extractions": "extraction.jsonl",
        "input_count": 1,
        "eligible_count": 0,
        "excluded_count": 1,
        "tei_parse_failure_count": 0,
        "llm_parse_failure_count": 0,
        "llm_timeout_count": 0,
    }
    # Fails loudly if the model changes under this fixture.
    EligibilityMeta.model_validate(meta)
    return meta


class TestRun:
    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
        args = _one_work_inputs(tmp_path, dry_run=True)
        _run_mocked(args, {"W1": {"eligible": True}})

        out_dir = _resolved_out_dir(args)
        assert not out_dir.exists() or not any(out_dir.iterdir())

    def test_writes_expected_files(self, tmp_path: Path) -> None:
        # W1, W2 have full text; W3 has no extraction → not assessed, not counted.
        corpus, extractions = _inputs(tmp_path, ["W1", "W2", "W3"], full_text=["W1", "W2"])
        args = _make_run_args(tmp_path, corpus_dir=corpus, extractions=extractions)

        _run_mocked(args, {"W1": {"eligible": True}, "W2": {"eligible": False}})

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
        assert len(verdict_lines) == 2

        meta = _meta(args)
        assert meta["input_count"] == 2
        assert meta["eligible_count"] == 1
        assert meta["excluded_count"] == 1
        assert meta["tei_parse_failure_count"] == 0
        assert meta["llm_parse_failure_count"] == 0
        assert "input_catalogue" not in meta
        assert "input_screening_verdicts" not in meta
        assert meta["corpus_id"] == json.loads((corpus / "corpus-meta.json").read_text())[
            "corpus_id"
        ]
        assert meta["run"]["tool"] == "laglitsynth.fulltext_eligibility.assess"
        assert meta["llm"]["base_url"] == _BASE
        assert meta["llm"]["decisions_url"] == _BASE + "/decisions"
        assert meta["llm"]["reasoning_effort"] is None
        assert "temperature" not in meta["llm"]
        assert meta["llm_rejected_count"] == 0
        assert meta["llm"]["prompt_sha256"] == criteria_sha256(_TEST_CRITERIA_SPEC)
        assert meta["criterion"] == _QUESTION.render()

        verdicts = [
            EligibilityVerdict.model_validate_json(line) for line in verdict_lines
        ]
        assert {v.work_id: v.probability for v in verdicts} == {"W1": 0.9, "W2": 0.1}

    def test_works_come_from_corpus(self, tmp_path: Path) -> None:
        """Only corpus works are assessed, even when stage 6 holds others."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus, [_make_work("W1")])
        extractions = tmp_path / "fulltext-extraction" / "extraction.jsonl"
        extractions.parent.mkdir(parents=True)
        _write_full_text_for(extractions, ["W1", "W9"], corpus)
        args = _make_run_args(tmp_path, corpus_dir=corpus, extractions=extractions)

        mock_classify = _run_mocked(args, {"W1": {"eligible": True}})

        assert [c.args[0] for c in mock_classify.call_args_list] == ["W1"]
        assert _meta(args)["input_count"] == 1

    def test_extraction_meta_corpus_mismatch_exits(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus, [_make_work("W1")])
        extractions = tmp_path / "fulltext-extraction" / "extraction.jsonl"
        extractions.parent.mkdir(parents=True)
        _write_full_text_for(extractions, ["W1"], corpus, corpus_id="another-corpus")
        args = _make_run_args(tmp_path, corpus_dir=corpus, extractions=extractions)

        with pytest.raises(SystemExit) as exc_info:
            _run_mocked(args, {})
        msg = str(exc_info.value)
        assert "extraction-meta.json" in msg
        assert "re-run fulltext-extraction" in msg
        assert not _resolved_out_dir(args).exists()

    def test_missing_extraction_meta_exits(self, tmp_path: Path) -> None:
        args = _one_work_inputs(tmp_path)
        (args.extractions.parent / "extraction-meta.json").unlink()

        with pytest.raises(SystemExit, match="run fulltext-extraction first"):
            _run_mocked(args, {})

    def test_stale_pdf_sha256_extraction_ignored(self, tmp_path: Path) -> None:
        """An extraction made from another PDF than the corpus copy is not used."""
        corpus = tmp_path / "corpus"
        _write_corpus(corpus, [_make_work("W1"), _make_work("W2")])
        extractions = tmp_path / "fulltext-extraction" / "extraction.jsonl"
        extractions.parent.mkdir(parents=True)
        _write_full_text_for(
            extractions, ["W1", "W2"], corpus, pdf_sha256={"W2": "e" * 64}
        )
        args = _make_run_args(tmp_path, corpus_dir=corpus, extractions=extractions)

        mock_classify = _run_mocked(args, {"W1": {"eligible": True}})

        assert [c.args[0] for c in mock_classify.call_args_list] == ["W1"]
        assert _meta(args)["input_count"] == 1

    def test_skip_existing_refuses_when_prompt_sha256_differs(
        self, tmp_path: Path
    ) -> None:
        """--skip-existing must raise SystemExit when recorded prompt_sha256 differs."""
        args = _one_work_inputs(tmp_path, skip_existing=True, run_id="stale-run")
        corpus_id = json.loads((args.corpus_dir / "corpus-meta.json").read_text())["corpus_id"]
        out_dir = _resolved_out_dir(args)
        out_dir.mkdir(parents=True)
        (out_dir / "eligibility-meta.json").write_text(
            json.dumps(_stale_meta(corpus_id, "0" * 64))
        )

        with pytest.raises(SystemExit, match="prompt_sha256"):
            _run_mocked(args, {})

    def test_skip_existing_refuses_when_corpus_id_differs(self, tmp_path: Path) -> None:
        args = _one_work_inputs(tmp_path, skip_existing=True, run_id="stale-run")
        out_dir = _resolved_out_dir(args)
        out_dir.mkdir(parents=True)
        (out_dir / "eligibility-meta.json").write_text(
            json.dumps(_stale_meta("another-corpus", criteria_sha256(_TEST_CRITERIA_SPEC)))
        )

        with pytest.raises(SystemExit, match="another-corpus"):
            _run_mocked(args, {})

    def test_validation_skipped_counts_invalid_extraction_lines(
        self, tmp_path: Path
    ) -> None:
        """meta.run.validation_skipped reflects malformed lines in the extractions."""
        args = _one_work_inputs(tmp_path, run_id="skipped-test-run")
        with open(args.extractions, "a") as f:
            f.write('{"not_a_real_field": "x"}\n')

        _run_mocked(args, {"W1": {"eligible": True}})

        assert _meta(args)["run"]["validation_skipped"] == 1

    def test_skip_existing_processes_only_delta(self, tmp_path: Path) -> None:
        corpus, extractions = _inputs(tmp_path, ["W1", "W2"])
        args = _make_run_args(
            tmp_path,
            corpus_dir=corpus,
            extractions=extractions,
            skip_existing=True,
            run_id="resume-run",
        )
        out_dir = _resolved_out_dir(args)
        out_dir.mkdir(parents=True)
        # Prior verdict for W1 only.
        prior = EligibilityVerdict(work_id="W1", probability=0.9, eligible=True)
        (out_dir / "verdicts.jsonl").write_text(prior.model_dump_json() + "\n")

        mock_classify = _run_mocked(args, {"W2": {"eligible": False}})

        # Only W2 was classified (W1 was skipped).
        assert mock_classify.call_count == 1
        assert mock_classify.call_args.args[0] == "W2"

        verdict_lines = [
            l
            for l in (out_dir / "verdicts.jsonl").read_text().splitlines()
            if l.strip()
        ]
        assert len(verdict_lines) == 2

        meta = _meta(args)
        assert meta["eligible_count"] == 1
        assert meta["excluded_count"] == 1
        assert meta["tei_parse_failure_count"] == 0
        assert meta["llm_parse_failure_count"] == 0

    def test_skip_existing_retries_sentinels(self, tmp_path: Path) -> None:
        corpus, extractions = _inputs(tmp_path, ["W1", "W2", "W3"])
        args = _make_run_args(
            tmp_path,
            corpus_dir=corpus,
            extractions=extractions,
            skip_existing=True,
            run_id="resume-run",
        )
        out_dir = _resolved_out_dir(args)
        out_dir.mkdir(parents=True)
        prior = [
            EligibilityVerdict(work_id="W1", probability=0.9, eligible=True),
            EligibilityVerdict(work_id="W2", reason="llm-rejected", raw_response="budget"),
            EligibilityVerdict(work_id="W3", reason="llm-timeout"),
        ]
        (out_dir / "verdicts.jsonl").write_text(
            "".join(v.model_dump_json() + "\n" for v in prior)
        )

        mock_classify = _run_mocked(
            args, {"W2": {"eligible": False}, "W3": {"eligible": True}}
        )

        assert sorted(c.args[0] for c in mock_classify.call_args_list) == ["W2", "W3"]
        verdicts = [
            EligibilityVerdict.model_validate_json(line)
            for line in (out_dir / "verdicts.jsonl").read_text().splitlines()
        ]
        assert sorted(v.work_id for v in verdicts) == ["W1", "W2", "W3"]
        assert all(v.reason is None for v in verdicts)
        meta = _meta(args)
        assert meta["eligible_count"] == 2
        assert meta["excluded_count"] == 1
        assert meta["llm_rejected_count"] == 0
        assert meta["llm_timeout_count"] == 0

    def test_skip_existing_dry_run_keeps_sentinel_rows(self, tmp_path: Path) -> None:
        corpus, extractions = _inputs(tmp_path, ["W1"])
        args = _make_run_args(
            tmp_path,
            corpus_dir=corpus,
            extractions=extractions,
            skip_existing=True,
            dry_run=True,
            run_id="resume-run",
        )
        out_dir = _resolved_out_dir(args)
        out_dir.mkdir(parents=True)
        before = EligibilityVerdict(work_id="W1", reason="llm-rejected").model_dump_json() + "\n"
        (out_dir / "verdicts.jsonl").write_text(before)

        mock_classify = _run_mocked(args, {"W1": {"eligible": True}})

        assert mock_classify.call_count == 1
        assert (out_dir / "verdicts.jsonl").read_text() == before

    def _resume_args(
        self,
        tmp_path: Path,
        work_ids: list[str],
        prior: list[EligibilityVerdict],
        **kwargs: Any,
    ) -> argparse.Namespace:
        corpus, extractions = _inputs(tmp_path, work_ids)
        args = _make_run_args(
            tmp_path,
            corpus_dir=corpus,
            extractions=extractions,
            skip_existing=True,
            run_id="resume-run",
            **kwargs,
        )
        out_dir = _resolved_out_dir(args)
        out_dir.mkdir(parents=True)
        (out_dir / "verdicts.jsonl").write_text(
            "".join(v.model_dump_json() + "\n" for v in prior)
        )
        return args

    def _verdicts(self, args: argparse.Namespace) -> dict[str, EligibilityVerdict]:
        lines = (_resolved_out_dir(args) / "verdicts.jsonl").read_text().splitlines()
        verdicts = [EligibilityVerdict.model_validate_json(line) for line in lines]
        by_id = {v.work_id: v for v in verdicts}
        assert len(by_id) == len(verdicts)
        return by_id

    def test_skip_existing_max_records_keeps_unsent_sentinels(
        self, tmp_path: Path
    ) -> None:
        prior = [
            EligibilityVerdict(work_id=w, reason="llm-rejected", raw_response="budget")
            for w in ["W1", "W2", "W3"]
        ]
        args = self._resume_args(tmp_path, ["W1", "W2", "W3"], prior, max_records=1)

        mock_classify = _run_mocked(args, {"W1": {"eligible": True}})

        assert [c.args[0] for c in mock_classify.call_args_list] == ["W1"]
        verdicts = self._verdicts(args)
        assert verdicts["W1"].eligible is True
        assert verdicts["W2"].reason == "llm-rejected"
        assert verdicts["W3"].raw_response == "budget"
        meta = _meta(args)
        assert meta["eligible_count"] == 1
        assert meta["llm_rejected_count"] == 2

    def test_skip_existing_retries_tei_parse_failure(self, tmp_path: Path) -> None:
        prior = [EligibilityVerdict(work_id="W1", reason="tei-parse-failure")]
        args = self._resume_args(tmp_path, ["W1"], prior)

        mock_classify = _run_mocked(args, {"W1": {"eligible": True}})

        assert mock_classify.call_count == 1
        assert self._verdicts(args)["W1"].eligible is True
        assert _meta(args)["tei_parse_failure_count"] == 0

    def test_skip_existing_keeps_sentinel_outside_active_set(
        self, tmp_path: Path
    ) -> None:
        # W9 is not in the corpus: its sentinel row is never re-sent and stays.
        prior = [EligibilityVerdict(work_id="W9", reason="llm-timeout")]
        args = self._resume_args(tmp_path, ["W1"], prior)

        mock_classify = _run_mocked(args, {"W1": {"eligible": False}})

        assert [c.args[0] for c in mock_classify.call_args_list] == ["W1"]
        assert self._verdicts(args)["W9"].reason == "llm-timeout"
        assert _meta(args)["llm_timeout_count"] == 1

    def test_skip_existing_keeps_invalid_lines(self, tmp_path: Path) -> None:
        prior = [EligibilityVerdict(work_id="W1", reason="llm-rejected")]
        args = self._resume_args(tmp_path, ["W1"], prior)
        path = _resolved_out_dir(args) / "verdicts.jsonl"
        with open(path, "a") as f:
            f.write('{"not_a_verdict": 1}\n')

        _run_mocked(args, {"W1": {"eligible": True}})

        lines = path.read_text().splitlines()
        assert lines[0] == '{"not_a_verdict": 1}'
        assert EligibilityVerdict.model_validate_json(lines[1]).eligible is True


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
        return _meta(args)

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


# --- run() stderr output ---


def test_run_dir_printed_to_stderr_at_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """run() prints 'Run dir: <output_dir>' to stderr at the end of a normal (non-dry) run."""
    args = _one_work_inputs(tmp_path, run_id="test-stderr-run")
    expected_dir = tmp_path / "fulltext-eligibility" / "test-stderr-run"

    _run_mocked(args, {"W1": {"eligible": True}})

    err = capsys.readouterr().err
    assert f"Run dir: {expected_dir}" in err
    # The line must appear at the end (last non-empty line).
    last_line = [line for line in err.splitlines() if line.strip()][-1]
    assert last_line == f"Run dir: {expected_dir}"


# --- --concurrency ---


def test_concurrency_default_is_one() -> None:
    """Stage 7 --concurrency defaults to 1."""
    from laglitsynth.fulltext_eligibility.eligibility import build_subparser

    p = argparse.ArgumentParser()
    subs = p.add_subparsers()
    build_subparser(subs)
    args = p.parse_args(["fulltext-eligibility", "--extractions", "e.jsonl"])
    assert args.concurrency == 1


def test_removed_screening_flags_are_rejected() -> None:
    from laglitsynth.fulltext_eligibility.eligibility import build_subparser

    p = argparse.ArgumentParser()
    subs = p.add_subparsers()
    build_subparser(subs)
    for flag in ("--catalogue", "--screening-verdicts", "--screening-threshold"):
        with pytest.raises(SystemExit):
            p.parse_args(["fulltext-eligibility", flag, "x"])


def test_concurrency_flag_threaded_to_assess_works(tmp_path: Path) -> None:
    """--concurrency N is forwarded to assess_works as concurrency=N."""
    args = _one_work_inputs(tmp_path, concurrency=3)

    captured: dict[str, Any] = {}
    original_assess = assess_works

    def spy_assess(*a: Any, **kw: Any) -> Any:
        captured["concurrency"] = kw.get("concurrency")
        return original_assess(*a, **kw)

    with patch(
        "laglitsynth.fulltext_eligibility.eligibility.assess_works",
        side_effect=spy_assess,
    ):
        _run_mocked(args, {"W1": {"eligible": True}})

    assert captured["concurrency"] == 3


def test_concurrency_one_uses_sequential_path(tmp_path: Path) -> None:
    """concurrency=1 does not create a ThreadPoolExecutor."""
    from concurrent.futures import ThreadPoolExecutor as TPE

    args = _one_work_inputs(tmp_path, concurrency=1)

    with patch("laglitsynth.concurrency.ThreadPoolExecutor", wraps=TPE) as mock_tpe:
        _run_mocked(args, {"W1": {"eligible": True}})

    mock_tpe.assert_not_called()


# --- run() conventional input paths ---


class TestConventionalPaths:
    def _layout(self, data_dir: Path) -> None:
        corpus = layout.corpus_dir(data_dir)
        _write_corpus(corpus, [_make_work("W1", abstract="about oceans")])
        extractions = layout.extractions_path(data_dir)
        extractions.parent.mkdir(parents=True, exist_ok=True)
        _write_full_text_for(extractions, ["W1"], corpus)

    def _bare_args(self, data_dir: Path) -> argparse.Namespace:
        args = _make_run_args(
            data_dir, corpus_dir=Path("unused"), extractions=Path("unused")
        )
        args.corpus_dir = None
        args.extractions = None
        args.extraction_output_dir = None
        args.run_id = None
        return args

    def test_omitted_flags_resolve_to_conventional_paths(self, tmp_path: Path) -> None:
        self._layout(tmp_path)

        _run_mocked(self._bare_args(tmp_path), {"W1": {"eligible": True}})

        out_dir = tmp_path / layout.ELIGIBILITY_SUBDIR / TEST_ANALYSIS_RUN_ID
        meta = json.loads((out_dir / "eligibility-meta.json").read_text())
        assert meta["input_extractions"] == str(layout.extractions_path(tmp_path))
        assert meta["input_count"] == 1

    def test_run_id_defaults_to_corpus_analysis_run_id(self, tmp_path: Path) -> None:
        corpus = layout.corpus_dir(tmp_path)
        _write_corpus(corpus, [_make_work("W1")], analysis_run_id="analysis-xyz")
        extractions = layout.extractions_path(tmp_path)
        extractions.parent.mkdir(parents=True, exist_ok=True)
        _write_full_text_for(extractions, ["W1"], corpus)

        _run_mocked(self._bare_args(tmp_path), {"W1": {"eligible": True}})

        assert (tmp_path / layout.ELIGIBILITY_SUBDIR / "analysis-xyz").is_dir()
        assert sorted(p.name for p in (tmp_path / layout.ELIGIBILITY_SUBDIR).iterdir()) == [
            "analysis-xyz"
        ]

    def test_explicit_run_id_wins(self, tmp_path: Path) -> None:
        self._layout(tmp_path)
        args = self._bare_args(tmp_path)
        args.run_id = "explicit-run-id"

        _run_mocked(args, {"W1": {"eligible": True}})

        assert (tmp_path / layout.ELIGIBILITY_SUBDIR / "explicit-run-id").is_dir()
        assert not (tmp_path / layout.ELIGIBILITY_SUBDIR / TEST_ANALYSIS_RUN_ID).exists()

    def test_missing_extractions_names_upstream_and_flag(self, tmp_path: Path) -> None:
        self._layout(tmp_path)
        layout.extractions_path(tmp_path).unlink()

        with pytest.raises(SystemExit) as exc_info:
            _run_mocked(self._bare_args(tmp_path), {})
        msg = str(exc_info.value)
        assert "run fulltext-extraction first" in msg
        assert "--extractions" in msg

    def test_missing_corpus_names_upstream_and_flag(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _run_mocked(self._bare_args(tmp_path), {})
        msg = str(exc_info.value)
        assert "run corpus-assemble first" in msg
        assert "--corpus-dir" in msg
        assert not (tmp_path / layout.ELIGIBILITY_SUBDIR).exists()
