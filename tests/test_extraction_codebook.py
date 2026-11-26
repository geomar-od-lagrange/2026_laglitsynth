"""Tests for the ``extraction_codebook`` stage.

The codebook YAML loader and dynamic-payload-model behaviours are
covered in ``test_extraction_codebook_loader.py``. This file focuses on
the ``extract_codebook`` LLM-call path, the cascade in ``extract_works``,
and the end-to-end ``run()`` wiring (run dirs, config.yaml, meta).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)
from pydantic import BaseModel

from laglitsynth import layout
from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.extraction_codebook.codebook import CodebookContext
from laglitsynth.extraction_codebook.extract import (
    USER_TEMPLATE,
    _active_eligible_works,
    extract_codebook,
    extract_works,
)
from laglitsynth.fulltext_eligibility.eligibility import criteria_sha256
from laglitsynth.fulltext_eligibility.models import EligibilityVerdict
from laglitsynth.fulltext_extraction.models import ExtractedDocument
from laglitsynth.llm import LlmEndpoint

from conftest import (
    TEI_NS,
    TEST_ANALYSIS_RUN_ID,
    _make_work,
    _mock_openai_response,
    _write_corpus,
    _write_extractions_jsonl,
    _write_full_text_for,
    _write_tei,
)

DEFAULT_CODEBOOK_PATH = Path("examples/codebooks/lagrangian-oceanography.yaml")
_BASE = "https://llm.example.org/v1"
_ENDPOINT = LlmEndpoint(base_url=_BASE, model="m", api_key="k", decisions_url=None)


def _api_response(status: int, text: str = '{"error": "x"}') -> httpx.Response:
    return httpx.Response(status, text=text, request=httpx.Request("POST", _BASE))


def _write_eligibility_verdicts_jsonl(
    path: Path, verdicts: list[EligibilityVerdict]
) -> None:
    with open(path, "w") as f:
        for v in verdicts:
            f.write(v.model_dump_json() + "\n")


@pytest.fixture(scope="module")
def ctx() -> CodebookContext:
    return CodebookContext.from_spec(DEFAULT_CODEBOOK_PATH)


def _write_malformed_tei(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not xml at all")


def _valid_payload_json(payload_field_names: tuple[str, ...] | list[str]) -> str:
    return json.dumps({name: f"v:{name}" for name in payload_field_names})


def _call_extract(
    ctx: CodebookContext,
    *,
    work_id: str = "W1",
    user_text: str = "body",
    client: Any,
) -> BaseModel:
    return extract_codebook(  # type: ignore[return-value]
        work_id,
        user_text,
        client=client,
        endpoint=_ENDPOINT,
        ctx=ctx,
    )


def _call_extract_works(
    ctx: CodebookContext,
    works: list[Work],
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    *,
    client: Any,
    max_records: int | None = None,
) -> list[BaseModel]:
    return list(
        extract_works(  # type: ignore[arg-type]
            works,
            extractions,
            extraction_output_dir,
            client=client,
            endpoint=_ENDPOINT,
            max_records=max_records,
            ctx=ctx,
        )
    )


# --- extract_codebook ---


class TestExtractCodebook:
    def test_valid_payload_produces_record(self, ctx: CodebookContext) -> None:
        resp = _mock_openai_response(
            _valid_payload_json(ctx.payload_field_names)
        )
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        record = _call_extract(ctx, client=client)
        assert record.work_id == "W1"  # type: ignore[attr-defined]
        assert record.reason is None  # type: ignore[attr-defined]
        assert "seed" not in record.model_dump()
        assert "truncated" not in record.model_dump()
        assert record.integration_scheme == "v:integration_scheme"  # type: ignore[attr-defined]

    def test_create_gets_no_temperature_or_seed(self, ctx: CodebookContext) -> None:
        resp = _mock_openai_response(
            _valid_payload_json(ctx.payload_field_names)
        )
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        _call_extract(ctx, client=client)
        kwargs = client.chat.completions.create.call_args.kwargs
        assert set(kwargs) == {"model", "messages", "response_format"}
        assert kwargs["model"] == "m"
        assert kwargs["response_format"] == {"type": "json_object"}

    def test_create_gets_reasoning_effort_when_set(self, ctx: CodebookContext) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_openai_response(
            _valid_payload_json(ctx.payload_field_names)
        )
        extract_codebook(
            "W1",
            "body",
            client=client,
            endpoint=_ENDPOINT.model_copy(update={"reasoning_effort": "high"}),
            ctx=ctx,
        )
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["reasoning_effort"] == "high"

    def test_bad_request_yields_llm_rejected_with_body(
        self, ctx: CodebookContext
    ) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = BadRequestError(
            "too long", response=_api_response(400, "context too long"), body=None
        )
        record = _call_extract(ctx, client=client)
        assert record.reason == "llm-rejected"  # type: ignore[attr-defined]
        assert record.raw_response == "context too long"  # type: ignore[attr-defined]
        for name in ctx.payload_field_names:
            assert getattr(record, name) is None

    def test_rate_limit_yields_llm_timeout(self, ctx: CodebookContext) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = RateLimitError(
            "slow down", response=_api_response(429), body=None
        )
        record = _call_extract(ctx, client=client)
        assert record.reason == "llm-timeout"  # type: ignore[attr-defined]

    def test_authentication_error_exits_naming_url(self, ctx: CodebookContext) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = AuthenticationError(
            "bad key", response=_api_response(401), body=None
        )
        with pytest.raises(SystemExit, match=_BASE):
            _call_extract(ctx, client=client)

    def test_timeout_yields_llm_timeout_sentinel(self, ctx: CodebookContext) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = APITimeoutError(request=MagicMock())
        record = _call_extract(ctx, client=client)
        assert record.reason == "llm-timeout"  # type: ignore[attr-defined]
        assert record.raw_response is None  # type: ignore[attr-defined]
        for name in ctx.payload_field_names:
            assert getattr(record, name) is None

    def test_connection_error_yields_llm_timeout_sentinel(
        self, ctx: CodebookContext
    ) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())
        record = _call_extract(ctx, client=client)
        assert record.reason == "llm-timeout"  # type: ignore[attr-defined]

    def test_bad_json_yields_llm_parse_failure(
        self, ctx: CodebookContext
    ) -> None:
        resp = _mock_openai_response("not json at all")
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        record = _call_extract(ctx, client=client)
        assert record.reason == "llm-parse-failure"  # type: ignore[attr-defined]
        for name in ctx.payload_field_names:
            assert getattr(record, name) is None

    def test_partial_json_missing_keys_parses_with_none_defaults(
        self, ctx: CodebookContext
    ) -> None:
        resp = _mock_openai_response('{"integration_scheme": "RK4"}')
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        record = _call_extract(ctx, client=client)
        assert record.reason is None  # type: ignore[attr-defined]
        assert record.integration_scheme == "RK4"  # type: ignore[attr-defined]
        assert record.time_step_strategy is None  # type: ignore[attr-defined]
        assert record.sub_discipline is None  # type: ignore[attr-defined]

    def test_raw_response_captured_on_success(
        self, ctx: CodebookContext
    ) -> None:
        content = '{"integration_scheme": "RK4"}'
        resp = _mock_openai_response(content)
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        record = _call_extract(ctx, client=client)
        assert record.raw_response == content  # type: ignore[attr-defined]

    def test_raw_response_captured_on_llm_parse_failure(
        self, ctx: CodebookContext
    ) -> None:
        content = "this is not json at all"
        resp = _mock_openai_response(content)
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        record = _call_extract(ctx, client=client)
        assert record.reason == "llm-parse-failure"  # type: ignore[attr-defined]
        assert record.raw_response == content  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        ("content", "finish_reason"), [("", "stop"), (None, "length"), ("{}", "length")]
    )
    def test_empty_or_unfinished_answer_yields_llm_parse_failure(
        self, ctx: CodebookContext, content: str | None, finish_reason: str
    ) -> None:
        client = MagicMock()
        client.chat.completions.create.return_value = _mock_openai_response(
            content, finish_reason
        )
        record = _call_extract(ctx, client=client)
        assert record.reason == "llm-parse-failure"  # type: ignore[attr-defined]
        for name in ctx.payload_field_names:
            assert getattr(record, name) is None


# --- extract_works cascade ---


class TestExtractWorksCascade:
    def test_full_text_branch(self, tmp_path: Path, ctx: CodebookContext) -> None:
        extractions_path = tmp_path / "extraction.jsonl"
        ext_out = tmp_path / "ext_out"

        work = _make_work("W1", abstract="The abstract.")

        tei_path = "tei/W1.tei.xml"
        _write_tei(
            ext_out / tei_path,
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

        resp = _mock_openai_response(
            _valid_payload_json(ctx.payload_field_names)
        )
        client = MagicMock()
        client.chat.completions.create.return_value = resp

        records = _call_extract_works(
            ctx,
            [work],
            {extracted.work_id: extracted},
            ext_out,
            client=client,
        )

        assert len(records) == 1
        assert records[0].reason is None  # type: ignore[attr-defined]
        # The user message should contain the full-text section, not the abstract.
        user_msg = client.chat.completions.create.call_args[1]["messages"][1]["content"]
        assert "Methods" in user_msg
        assert "Real content." in user_msg
        assert "full_text:" in user_msg
        assert "The abstract." not in user_msg

    def test_no_extraction_is_not_extracted(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        """An eligible work without an extraction gets no row and no LLM call."""
        ext_out = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract text.")

        client = MagicMock()
        records = _call_extract_works(
            ctx, [work], {}, ext_out, client=client
        )
        assert records == []
        client.chat.completions.create.assert_not_called()

    def test_malformed_tei_records_sentinel(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        ext_out = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract.")

        tei_path = "tei/W1.tei.xml"
        _write_malformed_tei(ext_out / tei_path)
        extracted = ExtractedDocument(
            work_id="W1",
            tei_path=tei_path,
            content_sha256="0" * 64,
            pdf_sha256="0" * 64,
            extracted_at="2026-04-17T00:00:00.000000+00:00",
        )
        client = MagicMock()
        records = _call_extract_works(
            ctx,
            [work],
            {extracted.work_id: extracted},
            ext_out,
            client=client,
        )
        assert len(records) == 1
        assert records[0].reason == "tei-parse-failure"  # type: ignore[attr-defined]
        client.chat.completions.create.assert_not_called()

    def test_long_body_sent_whole(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        extractions_path = tmp_path / "extraction.jsonl"
        ext_out = tmp_path / "ext_out"
        work = _make_work("W1", abstract=None)

        big_section = (
            f'<div xmlns="{TEI_NS}"><head>H{i}</head><p>{"x" * 20_000}</p></div>'
            for i in range(5)
        )
        _write_tei(ext_out / "tei/W1.tei.xml", "".join(big_section))
        extracted = ExtractedDocument(
            work_id="W1",
            tei_path="tei/W1.tei.xml",
            content_sha256="0" * 64,
            pdf_sha256="0" * 64,
            extracted_at="2026-04-17T00:00:00.000000+00:00",
        )
        _write_extractions_jsonl(extractions_path, [extracted])

        resp = _mock_openai_response(
            _valid_payload_json(ctx.payload_field_names)
        )
        client = MagicMock()
        client.chat.completions.create.return_value = resp

        records = _call_extract_works(
            ctx,
            [work],
            {extracted.work_id: extracted},
            ext_out,
            client=client,
        )
        assert len(records) == 1
        assert records[0].reason is None  # type: ignore[attr-defined]
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        user_message = messages[-1]["content"]
        for i in range(5):
            assert f"H{i}" in user_message
        assert user_message.count("x" * 20_000) == 5


# --- run() end-to-end ---


_CRITERIA_SPEC: dict[str, Any] = {
    "id": "test-criteria",
    "decision": {
        "instructions": "Is this ocean particle tracking primary research?",
        "criteria": {"true": "Tracks particles in the ocean.", "false": "Anything else."},
    },
}


def _write_eligibility(
    verdicts_path: Path,
    verdicts: list[EligibilityVerdict],
    corpus_dir: Path,
    *,
    corpus_id: str | None = None,
    prompt_sha256: str | None = None,
) -> None:
    """Write stage 7 verdicts and the eligibility-meta.json stage 8 checks."""
    from laglitsynth.corpus.load import load_corpus_meta
    from laglitsynth.fulltext_eligibility.models import EligibilityMeta
    from laglitsynth.io import write_meta
    from laglitsynth.models import LlmMeta, RunMeta

    verdicts_path.parent.mkdir(parents=True, exist_ok=True)
    with open(verdicts_path, "w") as f:
        for v in verdicts:
            f.write(v.model_dump_json() + "\n")
    write_meta(
        verdicts_path.parent / "eligibility-meta.json",
        EligibilityMeta(
            run=RunMeta(
                tool="laglitsynth.fulltext_eligibility.assess",
                run_at="2026-01-01T00:00:00.000000+00:00",
                validation_skipped=0,
            ),
            llm=LlmMeta(
                model="m",
                base_url=_BASE,
                decisions_url=_BASE + "/decisions",
                reasoning_effort=None,
                prompt_sha256=prompt_sha256
                if prompt_sha256 is not None
                else criteria_sha256(_CRITERIA_SPEC),
            ),
            corpus_id=corpus_id
            if corpus_id is not None
            else load_corpus_meta(corpus_dir).corpus_id,
            input_extractions="extraction.jsonl",
            input_count=len(verdicts),
            eligible_count=sum(1 for v in verdicts if v.eligible is True),
            excluded_count=sum(1 for v in verdicts if v.eligible is False),
            tei_parse_failure_count=0,
            llm_parse_failure_count=0,
        ),
    )


def _inputs(
    tmp_path: Path,
    eligible: list[str],
    *,
    full_text: list[str] | None = None,
) -> tuple[Path, Path, Path]:
    """Write a corpus, stage 6 output and eligible=True verdicts for ``eligible``.

    Returns the corpus dir, the verdicts path and the extractions path.
    """
    corpus = tmp_path / "corpus"
    _write_corpus(corpus, [_make_work(w, abstract="abs") for w in eligible])
    extractions = tmp_path / "fulltext-extraction" / "extraction.jsonl"
    extractions.parent.mkdir(parents=True, exist_ok=True)
    _write_full_text_for(extractions, full_text if full_text is not None else eligible, corpus)
    verdicts = tmp_path / "fulltext-eligibility" / "elig-run" / "verdicts.jsonl"
    _write_eligibility(
        verdicts, [EligibilityVerdict(work_id=w, eligible=True) for w in eligible], corpus
    )
    return corpus, verdicts, extractions


def _make_run_args(
    tmp_path: Path,
    *,
    corpus_dir: Path,
    eligibility_verdicts: Path,
    extractions: Path,
    dry_run: bool = False,
    skip_existing: bool = False,
    max_records: int | None = None,
    run_id: str | None = "test-run-id",
    extraction_output_dir: Path | None = None,
    concurrency: int = 1,
    reasoning_effort: str | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        corpus_dir=corpus_dir,
        eligibility_verdicts=eligibility_verdicts,
        eligibility_criteria=_CRITERIA_SPEC,
        extractions=extractions,
        extraction_output_dir=extraction_output_dir
        if extraction_output_dir is not None
        else extractions.parent,
        data_dir=tmp_path,
        run_id=run_id,
        codebook=DEFAULT_CODEBOOK_PATH,
        model="m",
        base_url=_BASE,
        api_key="k",
        reasoning_effort=reasoning_effort,
        max_records=max_records,
        skip_existing=skip_existing,
        dry_run=dry_run,
        config=None,
        concurrency=concurrency,
    )


def _one_work_args(tmp_path: Path, **kwargs: Any) -> argparse.Namespace:
    corpus, verdicts, extractions = _inputs(tmp_path, ["W1"])
    return _make_run_args(
        tmp_path,
        corpus_dir=corpus,
        eligibility_verdicts=verdicts,
        extractions=extractions,
        **kwargs,
    )


def _resolved_out_dir(args: argparse.Namespace) -> Path:
    return Path(args.data_dir) / "extraction-codebook" / args.run_id


def _make_mock_client(payload_content: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = _mock_openai_response(
        payload_content
    )
    return client


def _run(args: argparse.Namespace, ctx: CodebookContext) -> MagicMock:
    """Run stage 8 against a mock chat client; return the client."""
    mock_client = _make_mock_client(_valid_payload_json(ctx.payload_field_names))
    with patch(
        "laglitsynth.extraction_codebook.extract.chat_client",
        return_value=mock_client,
    ):
        from laglitsynth.extraction_codebook.extract import run

        run(args)
    return mock_client


def _meta(args: argparse.Namespace) -> dict[str, Any]:
    meta: dict[str, Any] = json.loads(
        (_resolved_out_dir(args) / "extraction-codebook-meta.json").read_text()
    )
    return meta


def _corpus_id(corpus_dir: Path) -> str:
    corpus_id: str = json.loads((corpus_dir / "corpus-meta.json").read_text())["corpus_id"]
    return corpus_id


class TestRun:
    def test_dry_run_writes_nothing(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        args = _one_work_args(tmp_path, dry_run=True)
        _run(args, ctx)

        out_dir = _resolved_out_dir(args)
        assert not out_dir.exists() or not any(out_dir.iterdir())

    def test_writes_expected_files(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        # W1, W2 have full text; W3 has no extraction → not extracted, not counted.
        corpus, verdicts, extractions = _inputs(
            tmp_path, ["W1", "W2", "W3"], full_text=["W1", "W2"]
        )
        args = _make_run_args(
            tmp_path, corpus_dir=corpus, eligibility_verdicts=verdicts, extractions=extractions
        )

        _run(args, ctx)

        out_dir = _resolved_out_dir(args)
        assert (out_dir / "records.jsonl").exists()
        assert (out_dir / "extraction-codebook-meta.json").exists()
        assert (out_dir / "config.yaml").exists()

        record_lines = [
            line
            for line in (out_dir / "records.jsonl").read_text().splitlines()
            if line.strip()
        ]
        assert len(record_lines) == 2

        meta = _meta(args)
        assert meta["input_count"] == 2
        assert meta["full_text_count"] == 2
        assert meta["skipped_count"] == 0
        assert meta["llm_parse_failure_count"] == 0
        assert "truncated_count" not in meta
        assert "input_catalogue" not in meta
        assert meta["corpus_id"] == _corpus_id(corpus)
        assert meta["eligibility_prompt_sha256"] == criteria_sha256(_CRITERIA_SPEC)
        assert meta["run"]["tool"] == "laglitsynth.extraction_codebook.extract"
        assert meta["llm"]["base_url"] == _BASE
        assert meta["llm"]["decisions_url"] is None
        assert meta["llm"]["reasoning_effort"] is None
        assert "temperature" not in meta["llm"]
        assert meta["llm_rejected_count"] == 0
        assert meta["llm"]["prompt_sha256"] == hashlib.sha256(
            (ctx.system_prompt + "\n" + USER_TEMPLATE).encode("utf-8")
        ).hexdigest()

    def test_reasoning_effort_sent_and_recorded(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        args = _one_work_args(tmp_path, reasoning_effort="high")

        mock_client = _run(args, ctx)

        kwargs = mock_client.chat.completions.create.call_args.kwargs
        assert kwargs["reasoning_effort"] == "high"
        assert _meta(args)["llm"]["reasoning_effort"] == "high"

    def test_config_yaml_inlines_codebook_and_eligibility_criteria(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        args = _one_work_args(tmp_path)
        args.eligibility_criteria = "examples/eligibility-criteria/lagrangian-oceanography.yaml"
        _write_eligibility(
            args.eligibility_verdicts,
            [EligibilityVerdict(work_id="W1", eligible=True)],
            args.corpus_dir,
            prompt_sha256=criteria_sha256(args.eligibility_criteria),
        )

        _run(args, ctx)

        import yaml

        config = yaml.safe_load(
            (_resolved_out_dir(args) / "config.yaml").read_text()
        )
        # Codebook and criteria embedded as mappings, not path strings.
        assert isinstance(config["codebook"], dict)
        assert config["codebook"]["id"] == "lagrangian-oceanography"
        assert config["codebook"]["fields"]
        assert isinstance(config["eligibility_criteria"], dict)
        assert config["eligibility_criteria"]["decision"]
        # run_id excluded; replay generates a fresh one.
        assert "run_id" not in config
        # config flag itself excluded.
        assert "config" not in config
        assert "api_key" not in config

    def test_eligibility_meta_corpus_mismatch_exits(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        args = _one_work_args(tmp_path)
        _write_eligibility(
            args.eligibility_verdicts,
            [EligibilityVerdict(work_id="W1", eligible=True)],
            args.corpus_dir,
            corpus_id="another-corpus",
        )

        with pytest.raises(SystemExit) as exc_info:
            _run(args, ctx)
        msg = str(exc_info.value)
        assert "eligibility-meta.json" in msg
        assert "re-run fulltext-eligibility" in msg
        assert not _resolved_out_dir(args).exists()

    def test_eligibility_criteria_hash_mismatch_exits(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        args = _one_work_args(tmp_path)
        _write_eligibility(
            args.eligibility_verdicts,
            [EligibilityVerdict(work_id="W1", eligible=True)],
            args.corpus_dir,
            prompt_sha256="0" * 64,
        )

        with pytest.raises(SystemExit, match="--eligibility-criteria"):
            _run(args, ctx)
        assert not _resolved_out_dir(args).exists()

    def test_missing_eligibility_meta_exits(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        args = _one_work_args(tmp_path)
        (args.eligibility_verdicts.parent / "eligibility-meta.json").unlink()

        with pytest.raises(SystemExit, match="run fulltext-eligibility first"):
            _run(args, ctx)
        assert not _resolved_out_dir(args).exists()

    def test_extraction_meta_corpus_mismatch_exits(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        args = _one_work_args(tmp_path)
        _write_full_text_for(args.extractions, ["W1"], args.corpus_dir, corpus_id="other")

        with pytest.raises(SystemExit, match="re-run fulltext-extraction"):
            _run(args, ctx)

    def test_stale_pdf_sha256_extraction_ignored(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        args = _one_work_args(tmp_path)
        _write_full_text_for(
            args.extractions, ["W1"], args.corpus_dir, pdf_sha256={"W1": "e" * 64}
        )

        mock_client = _run(args, ctx)

        mock_client.chat.completions.create.assert_not_called()
        assert _meta(args)["input_count"] == 0

    def test_verdict_outside_corpus_exits(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        args = _one_work_args(tmp_path)
        _write_eligibility(
            args.eligibility_verdicts,
            [
                EligibilityVerdict(work_id="W1", eligible=True),
                EligibilityVerdict(work_id="W2", eligible=True),
            ],
            args.corpus_dir,
        )

        with pytest.raises(SystemExit, match="W2"):
            _run(args, ctx)

    def test_skip_existing_refuses_when_prompt_sha256_differs(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        args = _one_work_args(tmp_path, skip_existing=True, run_id="stale-run")
        out_dir = _resolved_out_dir(args)
        out_dir.mkdir(parents=True)
        (out_dir / "extraction-codebook-meta.json").write_text(
            json.dumps(_stale_meta(_corpus_id(args.corpus_dir), "0" * 64))
        )

        with pytest.raises(SystemExit, match="prompt_sha256"):
            _run(args, ctx)

    def test_skip_existing_refuses_when_corpus_id_differs(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        args = _one_work_args(tmp_path, skip_existing=True, run_id="stale-run")
        out_dir = _resolved_out_dir(args)
        out_dir.mkdir(parents=True)
        prompt_sha256 = hashlib.sha256(
            (ctx.system_prompt + "\n" + USER_TEMPLATE).encode("utf-8")
        ).hexdigest()
        (out_dir / "extraction-codebook-meta.json").write_text(
            json.dumps(_stale_meta("another-corpus", prompt_sha256))
        )

        with pytest.raises(SystemExit, match="another-corpus"):
            _run(args, ctx)

    def test_validation_skipped_counts_invalid_verdict_lines(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        """meta.run.validation_skipped reflects malformed verdict and extraction lines."""
        args = _one_work_args(tmp_path, run_id="skipped-test-run")
        with open(args.eligibility_verdicts, "a") as f:
            f.write('{"not_a_real_field": "y"}\n')
        with open(args.extractions, "a") as f:
            f.write('{"not_a_real_field": "x"}\n')

        _run(args, ctx)

        assert _meta(args)["run"]["validation_skipped"] == 2

    def test_skip_existing_processes_only_delta(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        corpus, verdicts, extractions = _inputs(tmp_path, ["W1", "W2"])
        args = _make_run_args(
            tmp_path,
            corpus_dir=corpus,
            eligibility_verdicts=verdicts,
            extractions=extractions,
            skip_existing=True,
            run_id="resume-run",
        )
        out_dir = _resolved_out_dir(args)
        out_dir.mkdir(parents=True)
        # Prior record for W1 only — built via the same dynamic record_model
        # the runtime would use, so the read-back validates cleanly.
        payload_fields = {name: None for name in ctx.payload_field_names}
        prior = ctx.record_model(work_id="W1", reason=None, **payload_fields)
        (out_dir / "records.jsonl").write_text(prior.model_dump_json() + "\n")

        mock_client = _run(args, ctx)

        # Only W2 was extracted (W1 was skipped).
        assert mock_client.chat.completions.create.call_count == 1
        user_msg = mock_client.chat.completions.create.call_args[1]["messages"][1][
            "content"
        ]
        assert "full_text:" in user_msg

        record_lines = [
            line
            for line in (out_dir / "records.jsonl").read_text().splitlines()
            if line.strip()
        ]
        assert len(record_lines) == 2

        meta = _meta(args)
        assert meta["full_text_count"] == 2
        assert meta["skipped_count"] == 0


def _stale_meta(corpus_id: str, prompt_sha256: str) -> dict[str, Any]:
    """A valid ``ExtractionCodebookMeta`` dict for the resume guards."""
    from laglitsynth.extraction_codebook.models import ExtractionCodebookMeta

    meta = {
        "run": {
            "tool": "laglitsynth.extraction_codebook.extract",
            "run_at": "2026-01-01T00:00:00.000000+00:00",
            "validation_skipped": 0,
        },
        "llm": {
            "model": "m",
            "base_url": _BASE,
            "decisions_url": None,
            "reasoning_effort": None,
            "prompt_sha256": prompt_sha256,
        },
        "corpus_id": corpus_id,
        "eligibility_prompt_sha256": criteria_sha256(_CRITERIA_SPEC),
        "input_eligibility_verdicts": "verdicts.jsonl",
        "input_extractions": "extraction.jsonl",
        "input_count": 1,
        "full_text_count": 1,
        "skipped_count": 0,
        "llm_parse_failure_count": 0,
        "llm_timeout_count": 0,
    }
    # Fails loudly if the model changes under this fixture.
    ExtractionCodebookMeta.model_validate(meta)
    return meta


# --- CLI wiring ---


_CLI_ARGS = [
    "extraction-codebook",
    "--corpus-dir",
    "/tmp/corpus",
    "--eligibility-verdicts",
    "/tmp/ev.jsonl",
    "--extractions",
    "/tmp/x.jsonl",
]


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    from laglitsynth.extraction_codebook.extract import build_subparser

    build_subparser(subparsers)
    return parser.parse_args(argv)


class TestCliWiring:
    def test_subparser_registered(self) -> None:
        parsed = _parse(_CLI_ARGS)
        assert parsed.command == "extraction-codebook"
        assert parsed.corpus_dir == Path("/tmp/corpus")
        assert parsed.eligibility_verdicts == Path("/tmp/ev.jsonl")
        assert parsed.extractions == Path("/tmp/x.jsonl")
        assert parsed.eligibility_criteria == (
            "examples/eligibility-criteria/lagrangian-oceanography.yaml"
        )
        assert parsed.reasoning_effort is None

    def test_catalogue_flag_is_gone(self) -> None:
        with pytest.raises(SystemExit):
            _parse([*_CLI_ARGS, "--catalogue", "/tmp/cat.jsonl"])

    def test_reasoning_effort_choices(self) -> None:
        assert _parse([*_CLI_ARGS, "--reasoning-effort", "high"]).reasoning_effort == "high"
        with pytest.raises(SystemExit):
            _parse([*_CLI_ARGS, "--reasoning-effort", "extreme"])

    def test_main_cli_includes_subparser(self) -> None:
        from laglitsynth.cli import main

        with patch("laglitsynth.extraction_codebook.extract.run") as mock_run:
            main(_CLI_ARGS)
        assert mock_run.called

    def test_model_and_base_url_have_no_default(self) -> None:
        parsed = _parse(_CLI_ARGS)
        assert parsed.model is None
        assert parsed.base_url is None
        assert not hasattr(parsed, "decisions_url")


# --- _active_eligible_works join ---


def _write_verdicts(path: Path, verdicts: list[EligibilityVerdict]) -> None:
    with open(path, "w") as f:
        for v in verdicts:
            f.write(v.model_dump_json() + "\n")


class TestActiveEligibleWorks:
    """Unit tests for the stage-8 eligibility join against the corpus works."""

    def test_eligibility_gate_filters_to_eligible_true(
        self, tmp_path: Path
    ) -> None:
        """Only works with eligible=True flow through; False and None are excluded."""
        works = [
            _make_work("W1", abstract="First"),
            _make_work("W2", abstract="Second"),
            _make_work("W3", abstract="Third"),
        ]
        verdicts_path = tmp_path / "verdicts.jsonl"
        _write_verdicts(
            verdicts_path,
            [
                EligibilityVerdict(work_id="W1", eligible=True),
                EligibilityVerdict(work_id="W2", eligible=False),
                EligibilityVerdict(work_id="W3", eligible=None, reason="llm-timeout"),
            ],
        )

        result = list(_active_eligible_works(works, verdicts_path))
        assert [w.id for w in result] == ["W1"]

    def test_verdict_outside_corpus_exits(self, tmp_path: Path) -> None:
        """A verdict for a work_id absent from the corpus exits naming it."""
        verdicts_path = tmp_path / "verdicts.jsonl"
        _write_verdicts(
            verdicts_path,
            [
                EligibilityVerdict(work_id="W1", eligible=True),
                EligibilityVerdict(work_id="W2", eligible=True),
            ],
        )

        with pytest.raises(SystemExit, match="W2"):
            list(_active_eligible_works([_make_work("W1")], verdicts_path))


# --- run() stderr output ---


def test_run_dir_printed_to_stderr_at_end(
    tmp_path: Path, ctx: CodebookContext, capsys: pytest.CaptureFixture[str]
) -> None:
    """run() prints 'Run dir: <output_dir>' to stderr at the end of a normal (non-dry) run."""
    args = _one_work_args(tmp_path, run_id="test-stderr-run")
    expected_dir = tmp_path / "extraction-codebook" / "test-stderr-run"

    _run(args, ctx)

    err = capsys.readouterr().err
    assert f"Output dir: {expected_dir}" in err
    # The end-of-run line must appear at the end (last non-empty line).
    last_line = [line for line in err.splitlines() if line.strip()][-1]
    assert last_line == f"Run dir: {expected_dir}"


# --- --concurrency ---


def test_concurrency_default_is_one() -> None:
    """Stage 8 --concurrency defaults to 1 (prefill-bound stage)."""
    assert _parse(_CLI_ARGS).concurrency == 1


def test_concurrency_flag_threaded_to_extract_works(
    tmp_path: Path, ctx: CodebookContext
) -> None:
    """--concurrency N is forwarded to extract_works as concurrency=N."""
    args = _one_work_args(tmp_path, concurrency=3)

    captured: dict[str, Any] = {}
    original_extract = extract_works

    def spy_extract(*a: Any, **kw: Any) -> Any:
        captured["concurrency"] = kw.get("concurrency")
        return original_extract(*a, **kw)

    with patch(
        "laglitsynth.extraction_codebook.extract.extract_works",
        side_effect=spy_extract,
    ):
        _run(args, ctx)

    assert captured["concurrency"] == 3


def test_concurrency_one_uses_sequential_path(
    tmp_path: Path, ctx: CodebookContext
) -> None:
    """concurrency=1 does not create a ThreadPoolExecutor."""
    from concurrent.futures import ThreadPoolExecutor as TPE

    args = _one_work_args(tmp_path, concurrency=1)

    with patch("laglitsynth.concurrency.ThreadPoolExecutor", wraps=TPE) as mock_tpe:
        _run(args, ctx)

    mock_tpe.assert_not_called()


# --- run() conventional input paths ---


class TestConventionalPaths:
    def _layout(self, data_dir: Path, analysis_run_id: str = TEST_ANALYSIS_RUN_ID) -> None:
        corpus = layout.corpus_dir(data_dir)
        _write_corpus(
            corpus, [_make_work("W1", abstract="about oceans")], analysis_run_id=analysis_run_id
        )
        _write_eligibility(
            layout.eligibility_verdicts_path(data_dir, analysis_run_id),
            [EligibilityVerdict(work_id="W1", eligible=True)],
            corpus,
        )
        extractions = layout.extractions_path(data_dir)
        extractions.parent.mkdir(parents=True, exist_ok=True)
        _write_full_text_for(extractions, ["W1"], corpus)

    def _bare_args(self, data_dir: Path) -> argparse.Namespace:
        args = _make_run_args(
            data_dir,
            corpus_dir=Path("unused"),
            eligibility_verdicts=Path("unused"),
            extractions=Path("unused"),
        )
        args.corpus_dir = None
        args.eligibility_verdicts = None
        args.extractions = None
        args.extraction_output_dir = None
        args.run_id = None
        return args

    def _meta(self, data_dir: Path, run_id: str) -> dict[str, Any]:
        out_dir = data_dir / layout.EXTRACTION_CODEBOOK_SUBDIR / run_id
        meta: dict[str, Any] = json.loads(
            (out_dir / "extraction-codebook-meta.json").read_text()
        )
        return meta

    def test_omitted_flags_resolve_to_conventional_paths(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        self._layout(tmp_path)

        _run(self._bare_args(tmp_path), ctx)

        meta = self._meta(tmp_path, TEST_ANALYSIS_RUN_ID)
        assert meta["input_eligibility_verdicts"] == str(
            layout.eligibility_verdicts_path(tmp_path, TEST_ANALYSIS_RUN_ID)
        )
        assert meta["input_extractions"] == str(layout.extractions_path(tmp_path))
        assert meta["input_count"] == 1

    def test_run_id_defaults_to_corpus_analysis_run_id(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        self._layout(tmp_path, analysis_run_id="analysis-xyz")

        _run(self._bare_args(tmp_path), ctx)

        assert self._meta(tmp_path, "analysis-xyz")["input_count"] == 1

    def test_explicit_flags_win(self, tmp_path: Path, ctx: CodebookContext) -> None:
        self._layout(tmp_path)
        explicit_verdicts = tmp_path / "explicit" / "verdicts.jsonl"
        _write_eligibility(
            explicit_verdicts,
            [EligibilityVerdict(work_id="W1", eligible=True)],
            layout.corpus_dir(tmp_path),
        )
        args = self._bare_args(tmp_path)
        args.eligibility_verdicts = explicit_verdicts
        args.run_id = "explicit-run-id"

        _run(args, ctx)

        assert not (
            tmp_path / layout.EXTRACTION_CODEBOOK_SUBDIR / TEST_ANALYSIS_RUN_ID
        ).exists()
        meta = self._meta(tmp_path, "explicit-run-id")
        assert meta["input_eligibility_verdicts"] == str(explicit_verdicts)

    def test_missing_eligibility_verdicts_names_upstream_and_flag(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        self._layout(tmp_path)
        layout.eligibility_verdicts_path(tmp_path, TEST_ANALYSIS_RUN_ID).unlink()

        with pytest.raises(SystemExit) as exc_info:
            _run(self._bare_args(tmp_path), ctx)
        msg = str(exc_info.value)
        assert "run fulltext-eligibility first" in msg
        assert "--eligibility-verdicts" in msg

    def test_missing_corpus_names_upstream_and_flag(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            _run(self._bare_args(tmp_path), ctx)
        msg = str(exc_info.value)
        assert "run corpus-assemble first" in msg
        assert "--corpus-dir" in msg
