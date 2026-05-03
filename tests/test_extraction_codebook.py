"""Tests for the ``extraction_codebook`` stage.

The codebook YAML loader and dynamic-payload-model behaviours are
covered in ``test_extraction_codebook_loader.py``. This file focuses on
the ``extract_codebook`` LLM-call path, the cascade in ``extract_works``,
and the end-to-end ``run()`` wiring (run dirs, config.yaml, meta).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from openai import APIConnectionError, APITimeoutError
from pydantic import BaseModel

from laglitsynth.extraction_codebook.codebook import CodebookContext
from laglitsynth.extraction_codebook.extract import (
    extract_codebook,
    extract_works,
)
from laglitsynth.fulltext_extraction.models import ExtractedDocument

from conftest import (
    TEI_NS,
    _make_work,
    _mock_openai_response,
    _write_extractions_jsonl,
    _write_tei,
    _write_works_jsonl,
)

DEFAULT_CODEBOOK_PATH = Path("examples/codebooks/lagrangian-oceanography.yaml")


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
    source_basis: str = "full_text",
    user_text: str = "body",
    client: Any,
    truncated: bool = False,
) -> BaseModel:
    return extract_codebook(  # type: ignore[return-value]
        work_id,
        source_basis,  # type: ignore[arg-type]
        user_text,
        client=client,
        model="m",
        truncated=truncated,
        ctx=ctx,
    )


def _call_extract_works(
    ctx: CodebookContext,
    catalogue: Path,
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    *,
    client: Any,
    max_records: int | None = None,
) -> list[BaseModel]:
    return list(
        extract_works(  # type: ignore[arg-type]
            catalogue,
            extractions,
            extraction_output_dir,
            client=client,
            model="m",
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
        assert record.source_basis == "full_text"  # type: ignore[attr-defined]
        assert record.reason is None  # type: ignore[attr-defined]
        assert isinstance(record.seed, int)  # type: ignore[attr-defined]
        assert record.truncated is False  # type: ignore[attr-defined]
        assert record.integration_scheme == "v:integration_scheme"  # type: ignore[attr-defined]

    def test_truncated_flag_forwarded(self, ctx: CodebookContext) -> None:
        resp = _mock_openai_response(
            _valid_payload_json(ctx.payload_field_names)
        )
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        record = _call_extract(ctx, client=client, truncated=True)
        assert record.truncated is True  # type: ignore[attr-defined]
        assert record.reason is None  # type: ignore[attr-defined]

    def test_seed_forwarded_to_client(self, ctx: CodebookContext) -> None:
        resp = _mock_openai_response(
            _valid_payload_json(ctx.payload_field_names)
        )
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        with patch(
            "laglitsynth.extraction_codebook.extract.random.randint",
            return_value=42,
        ):
            record = _call_extract(ctx, client=client)
        assert record.seed == 42  # type: ignore[attr-defined]

    def test_timeout_yields_llm_timeout_sentinel(self, ctx: CodebookContext) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = APITimeoutError(request=MagicMock())
        record = _call_extract(ctx, client=client)
        assert record.reason == "llm-timeout"  # type: ignore[attr-defined]
        assert record.source_basis == "full_text"  # type: ignore[attr-defined]
        assert record.seed is None  # type: ignore[attr-defined]
        assert record.truncated is False  # type: ignore[attr-defined]
        assert record.raw_response is None  # type: ignore[attr-defined]
        for name in ctx.payload_field_names:
            assert getattr(record, name) is None

    def test_connection_error_yields_llm_timeout_sentinel(
        self, ctx: CodebookContext
    ) -> None:
        client = MagicMock()
        client.chat.completions.create.side_effect = APIConnectionError(request=MagicMock())
        record = _call_extract(ctx, client=client, source_basis="abstract_only")
        assert record.reason == "llm-timeout"  # type: ignore[attr-defined]
        assert record.source_basis == "abstract_only"  # type: ignore[attr-defined]

    def test_bad_json_yields_llm_parse_failure(
        self, ctx: CodebookContext
    ) -> None:
        resp = _mock_openai_response("not json at all")
        client = MagicMock()
        client.chat.completions.create.return_value = resp
        record = _call_extract(ctx, client=client, source_basis="abstract_only")
        assert record.reason == "llm-parse-failure"  # type: ignore[attr-defined]
        assert record.source_basis == "abstract_only"  # type: ignore[attr-defined]
        assert record.seed is None  # type: ignore[attr-defined]
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


# --- extract_works cascade ---


class TestExtractWorksCascade:
    def test_full_text_branch(self, tmp_path: Path, ctx: CodebookContext) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        ext_out = tmp_path / "ext_out"

        _write_works_jsonl(catalogue, [_make_work("W1", abstract="The abstract.")])

        tei_path = "tei/W1.tei.xml"
        _write_tei(
            ext_out / tei_path,
            f'<div xmlns="{TEI_NS}"><head>Methods</head><p>Real content.</p></div>',
        )
        extracted = ExtractedDocument(
            work_id="W1",
            tei_path=tei_path,
            content_sha256="0" * 64,
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
            catalogue,
            {extracted.work_id: extracted},
            ext_out,
            client=client,
        )

        assert len(records) == 1
        assert records[0].source_basis == "full_text"  # type: ignore[attr-defined]
        assert records[0].reason is None  # type: ignore[attr-defined]
        # The user message should contain the full-text section, not the abstract.
        user_msg = client.chat.completions.create.call_args[1]["messages"][1]["content"]
        assert "Methods" in user_msg
        assert "Real content." in user_msg
        assert "full_text:" in user_msg
        assert "The abstract." not in user_msg

    def test_abstract_only_branch_when_extraction_missing(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        ext_out = tmp_path / "ext_out"
        _write_works_jsonl(catalogue, [_make_work("W1", abstract="Paper abstract text.")])

        resp = _mock_openai_response(
            _valid_payload_json(ctx.payload_field_names)
        )
        client = MagicMock()
        client.chat.completions.create.return_value = resp

        records = _call_extract_works(
            ctx, catalogue, {}, ext_out, client=client
        )
        assert len(records) == 1
        assert records[0].source_basis == "abstract_only"  # type: ignore[attr-defined]
        assert records[0].reason is None  # type: ignore[attr-defined]

    def test_no_source_sentinel_no_llm_call(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        ext_out = tmp_path / "ext_out"
        _write_works_jsonl(catalogue, [_make_work("W1", abstract=None)])

        client = MagicMock()
        records = _call_extract_works(
            ctx, catalogue, {}, ext_out, client=client
        )
        assert len(records) == 1
        assert records[0].source_basis == "none"  # type: ignore[attr-defined]
        assert records[0].reason == "no-source"  # type: ignore[attr-defined]
        client.chat.completions.create.assert_not_called()

    def test_malformed_tei_no_abstract_fallback(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        ext_out = tmp_path / "ext_out"
        _write_works_jsonl(catalogue, [_make_work("W1", abstract="Paper abstract.")])

        tei_path = "tei/W1.tei.xml"
        _write_malformed_tei(ext_out / tei_path)
        extracted = ExtractedDocument(
            work_id="W1",
            tei_path=tei_path,
            content_sha256="0" * 64,
            extracted_at="2026-04-17T00:00:00.000000+00:00",
        )
        client = MagicMock()
        records = _call_extract_works(
            ctx,
            catalogue,
            {extracted.work_id: extracted},
            ext_out,
            client=client,
        )
        assert len(records) == 1
        assert records[0].reason == "tei-parse-failure"  # type: ignore[attr-defined]
        assert records[0].source_basis == "full_text"  # type: ignore[attr-defined]
        client.chat.completions.create.assert_not_called()

    def test_truncation_flag_on_over_budget_body(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        ext_out = tmp_path / "ext_out"
        _write_works_jsonl(catalogue, [_make_work("W1", abstract=None)])

        big_section = (
            f'<div xmlns="{TEI_NS}"><head>H{i}</head><p>{"x" * 20_000}</p></div>'
            for i in range(5)
        )
        _write_tei(ext_out / "tei/W1.tei.xml", "".join(big_section))
        extracted = ExtractedDocument(
            work_id="W1",
            tei_path="tei/W1.tei.xml",
            content_sha256="0" * 64,
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
            catalogue,
            {extracted.work_id: extracted},
            ext_out,
            client=client,
        )
        assert len(records) == 1
        assert records[0].reason is None  # type: ignore[attr-defined]
        assert records[0].truncated is True  # type: ignore[attr-defined]


# --- _preflight ---


class TestPreflight:
    def test_preflight_raises_on_connection_failure(self) -> None:
        from laglitsynth.extraction_codebook.extract import _preflight

        args = MagicMock()
        args.base_url = "http://localhost:99999"
        args.model = "nonexistent"

        with patch("laglitsynth.extraction_codebook.extract.OpenAI") as mock_cls:
            mock_cls.return_value.models.retrieve.side_effect = Exception(
                "connection refused"
            )
            with pytest.raises(SystemExit):
                _preflight(args)


# --- run() end-to-end ---


def _make_run_args(
    tmp_path: Path,
    *,
    eligible: Path,
    extractions: Path,
    dry_run: bool = False,
    skip_existing: bool = False,
    max_records: int | None = None,
    run_id: str = "test-run-id",
    extraction_output_dir: Path | None = None,
) -> argparse.Namespace:
    return argparse.Namespace(
        eligible=eligible,
        extractions=extractions,
        extraction_output_dir=extraction_output_dir
        if extraction_output_dir is not None
        else extractions.parent,
        data_dir=tmp_path,
        run_id=run_id,
        codebook=DEFAULT_CODEBOOK_PATH,
        model="m",
        base_url="http://x",
        max_records=max_records,
        skip_existing=skip_existing,
        dry_run=dry_run,
        config=None,
    )


def _resolved_out_dir(args: argparse.Namespace) -> Path:
    return Path(args.data_dir) / "extraction-codebook" / args.run_id


def _make_mock_client(payload_content: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = _mock_openai_response(
        payload_content
    )
    return client


class TestRun:
    def test_dry_run_writes_nothing(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        eligible = tmp_path / "eligible.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        _write_works_jsonl(eligible, [_make_work("W1", abstract="abs")])
        extractions_path.write_text("")

        args = _make_run_args(
            tmp_path,
            eligible=eligible,
            extractions=extractions_path,
            dry_run=True,
        )

        mock_client = _make_mock_client(
            _valid_payload_json(ctx.payload_field_names)
        )
        with (
            patch("laglitsynth.extraction_codebook.extract._preflight"),
            patch(
                "laglitsynth.extraction_codebook.extract.OpenAI",
                return_value=mock_client,
            ),
        ):
            from laglitsynth.extraction_codebook.extract import run

            run(args)

        out_dir = _resolved_out_dir(args)
        assert not out_dir.exists() or not any(out_dir.iterdir())

    def test_writes_expected_files(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        eligible = tmp_path / "eligible.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"

        works = [
            _make_work("W1", abstract="about oceans"),
            _make_work("W2", abstract="not relevant"),
            _make_work("W3", abstract=None),
        ]
        _write_works_jsonl(eligible, works)
        extractions_path.write_text("")

        args = _make_run_args(
            tmp_path,
            eligible=eligible,
            extractions=extractions_path,
        )

        mock_client = _make_mock_client(
            _valid_payload_json(ctx.payload_field_names)
        )
        with (
            patch("laglitsynth.extraction_codebook.extract._preflight"),
            patch(
                "laglitsynth.extraction_codebook.extract.OpenAI",
                return_value=mock_client,
            ),
        ):
            from laglitsynth.extraction_codebook.extract import run

            run(args)

        out_dir = _resolved_out_dir(args)
        assert (out_dir / "records.jsonl").exists()
        assert (out_dir / "extraction-codebook-meta.json").exists()
        assert (out_dir / "config.yaml").exists()

        record_lines = [
            line
            for line in (out_dir / "records.jsonl").read_text().splitlines()
            if line.strip()
        ]
        assert len(record_lines) == 3

        meta = json.loads((out_dir / "extraction-codebook-meta.json").read_text())
        assert meta["input_count"] == 3
        # Two successful abstract_only (W1, W2) plus one no-source (W3).
        assert meta["abstract_only_count"] == 2
        assert meta["full_text_count"] == 0
        assert meta["skipped_count"] == 1
        assert meta["llm_parse_failure_count"] == 0
        assert meta["truncated_count"] == 0
        assert meta["by_source_basis"] == {"abstract_only": 2, "none": 1}
        assert meta["run"]["tool"] == "laglitsynth.extraction_codebook.extract"
        assert meta["llm"]["temperature"] == 0.8
        assert len(meta["llm"]["prompt_sha256"]) == 64

    def test_config_yaml_inlines_codebook(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        eligible = tmp_path / "eligible.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        _write_works_jsonl(eligible, [_make_work("W1", abstract="abs")])
        extractions_path.write_text("")

        args = _make_run_args(
            tmp_path,
            eligible=eligible,
            extractions=extractions_path,
        )

        mock_client = _make_mock_client(
            _valid_payload_json(ctx.payload_field_names)
        )
        with (
            patch("laglitsynth.extraction_codebook.extract._preflight"),
            patch(
                "laglitsynth.extraction_codebook.extract.OpenAI",
                return_value=mock_client,
            ),
        ):
            from laglitsynth.extraction_codebook.extract import run

            run(args)

        import yaml

        config = yaml.safe_load(
            (_resolved_out_dir(args) / "config.yaml").read_text()
        )
        # Codebook embedded as a mapping, not a path string.
        assert isinstance(config["codebook"], dict)
        assert config["codebook"]["id"] == "lagrangian-oceanography"
        assert config["codebook"]["fields"]
        # run_id excluded; replay generates a fresh one.
        assert "run_id" not in config
        # config flag itself excluded.
        assert "config" not in config

    def test_skip_existing_refuses_when_prompt_sha256_differs(
        self, tmp_path: Path
    ) -> None:
        eligible = tmp_path / "eligible.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        _write_works_jsonl(eligible, [_make_work("W1", abstract="first abstract")])
        extractions_path.write_text("")

        run_id = "stale-run"
        out_dir = tmp_path / "extraction-codebook" / run_id
        out_dir.mkdir(parents=True)

        # Write a meta file with a deliberately wrong prompt_sha256.
        stale_meta = {
            "run": {
                "tool": "laglitsynth.extraction_codebook.extract",
                "run_at": "2026-01-01T00:00:00.000000+00:00",
                "validation_skipped": 0,
            },
            "llm": {
                "model": "gemma3:4b",
                "temperature": 0.8,
                "prompt_sha256": "0" * 64,
            },
            "input_catalogue": str(eligible),
            "input_extractions": str(extractions_path),
            "input_count": 1,
            "full_text_count": 0,
            "abstract_only_count": 1,
            "skipped_count": 0,
            "llm_parse_failure_count": 0,
            "truncated_count": 0,
            "by_source_basis": {"abstract_only": 1},
        }
        (out_dir / "extraction-codebook-meta.json").write_text(json.dumps(stale_meta))

        args = _make_run_args(
            tmp_path,
            eligible=eligible,
            extractions=extractions_path,
            skip_existing=True,
            run_id=run_id,
        )

        with (
            patch("laglitsynth.extraction_codebook.extract._preflight"),
            patch("laglitsynth.extraction_codebook.extract.OpenAI"),
        ):
            from laglitsynth.extraction_codebook.extract import run

            with pytest.raises(SystemExit, match="prompt_sha256"):
                run(args)

    def test_skip_existing_processes_only_delta(
        self, tmp_path: Path, ctx: CodebookContext
    ) -> None:
        eligible = tmp_path / "eligible.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        works = [
            _make_work("W1", abstract="first abstract"),
            _make_work("W2", abstract="second abstract"),
        ]
        _write_works_jsonl(eligible, works)
        extractions_path.write_text("")

        run_id = "resume-run"
        out_dir = tmp_path / "extraction-codebook" / run_id
        out_dir.mkdir(parents=True)
        # Prior record for W1 only — built via the same dynamic record_model
        # the runtime would use, so the read-back validates cleanly.
        record_model = ctx.record_model
        payload_fields = {name: None for name in ctx.payload_field_names}
        prior = record_model(
            work_id="W1",
            source_basis="abstract_only",
            reason=None,
            seed=1,
            truncated=False,
            **payload_fields,
        )
        (out_dir / "records.jsonl").write_text(prior.model_dump_json() + "\n")

        args = _make_run_args(
            tmp_path,
            eligible=eligible,
            extractions=extractions_path,
            skip_existing=True,
            run_id=run_id,
        )

        mock_client = _make_mock_client(
            _valid_payload_json(ctx.payload_field_names)
        )
        with (
            patch("laglitsynth.extraction_codebook.extract._preflight"),
            patch(
                "laglitsynth.extraction_codebook.extract.OpenAI",
                return_value=mock_client,
            ),
        ):
            from laglitsynth.extraction_codebook.extract import run

            run(args)

        # Only W2 was classified (W1 was skipped).
        assert mock_client.chat.completions.create.call_count == 1
        user_msg = mock_client.chat.completions.create.call_args[1]["messages"][1][
            "content"
        ]
        assert "second abstract" in user_msg

        record_lines = [
            line
            for line in (out_dir / "records.jsonl").read_text().splitlines()
            if line.strip()
        ]
        assert len(record_lines) == 2

        meta = json.loads((out_dir / "extraction-codebook-meta.json").read_text())
        assert meta["abstract_only_count"] == 2
        assert meta["skipped_count"] == 0


# --- CLI wiring ---


class TestCliWiring:
    def test_subparser_registered(self) -> None:
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        from laglitsynth.extraction_codebook.extract import build_subparser

        build_subparser(subparsers)
        parsed = parser.parse_args(
            [
                "extraction-codebook",
                "--eligible",
                "/tmp/e.jsonl",
                "--extractions",
                "/tmp/x.jsonl",
            ]
        )
        assert parsed.command == "extraction-codebook"
        assert parsed.eligible == Path("/tmp/e.jsonl")
        assert parsed.extractions == Path("/tmp/x.jsonl")

    def test_main_cli_includes_subparser(self) -> None:
        from laglitsynth.cli import main

        with patch("laglitsynth.extraction_codebook.extract.run") as mock_run:
            main(
                [
                    "extraction-codebook",
                    "--eligible",
                    "/tmp/e.jsonl",
                    "--extractions",
                    "/tmp/x.jsonl",
                ]
            )
        assert mock_run.called
