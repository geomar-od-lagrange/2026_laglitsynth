"""Tests for the ``extraction_codebook`` stage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from laglitsynth.extraction_codebook.extract import (
    extract_codebook,
    extract_works,
)
from laglitsynth.extraction_codebook.models import (
    ExtractionRecord,
    _ExtractionPayload,
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


# --- fixtures and helpers ---


def _write_malformed_tei(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not xml at all")


def _valid_payload_json() -> str:
    """Return a JSON payload with every ``_ExtractionPayload`` field set.

    Uses placeholder strings to keep the test explicit about which
    fields come from the LLM.
    """
    data: dict[str, str | None] = {}
    for name in _ExtractionPayload.model_fields:
        # Any valid str or None is fine; alternate for variety.
        data[name] = f"v:{name}"
    return json.dumps(data)


def _all_none_payload_json() -> str:
    data: dict[str, str | None] = {
        name: None for name in _ExtractionPayload.model_fields
    }
    return json.dumps(data)


# --- ExtractionRecord model ---


class TestSchemaRoundtrip:
    def test_populated_record_roundtrip(self) -> None:
        payload = json.loads(_valid_payload_json())
        record = ExtractionRecord(
            work_id="W1",
            source_basis="full_text",
            reason=None,
            seed=42,
            truncated=False,
            **payload,
        )
        reparsed = ExtractionRecord.model_validate_json(record.model_dump_json())
        assert reparsed == record

    def test_all_none_sentinel_variant(self) -> None:
        payload = {name: None for name in _ExtractionPayload.model_fields}
        record = ExtractionRecord(
            work_id="W1",
            source_basis="none",
            reason="no-source",
            seed=None,
            truncated=False,
            **payload,
        )
        reparsed = ExtractionRecord.model_validate_json(record.model_dump_json())
        assert reparsed == record

    def test_extra_fields_dropped_by_inherited_coercer(self) -> None:
        # ExtractionRecord inherits _ExtractionPayload's mode="before" validator
        # which drops unknown keys before pydantic sees them (LLM-tolerance design).
        # Extras passed at construction time are silently dropped; the record is
        # valid. This is the expected behaviour: extra="forbid" guards
        # deserialization of stored records against schema drift, but construction
        # from caller code goes through the coercer.
        payload = {name: None for name in _ExtractionPayload.model_fields}
        record = ExtractionRecord(
            work_id="W1",
            source_basis="none",
            reason=None,
            seed=None,
            truncated=False,
            bonus="no",  # type: ignore[call-arg]
            **payload,
        )
        assert record.work_id == "W1"
        assert not hasattr(record, "bonus")


# --- extract_codebook ---


class TestExtractCodebook:
    def test_valid_payload_produces_record(self) -> None:
        resp = _mock_openai_response(_valid_payload_json())
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        record = extract_codebook(
            "W1",
            "full_text",
            "body text",
            client=mock_client,
            model="m",
            truncated=False,
        )
        assert record.work_id == "W1"
        assert record.source_basis == "full_text"
        assert record.reason is None
        assert isinstance(record.seed, int)
        assert record.truncated is False
        assert record.integration_scheme == "v:integration_scheme"

    def test_truncated_flag_forwarded(self) -> None:
        resp = _mock_openai_response(_valid_payload_json())
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        record = extract_codebook(
            "W1",
            "full_text",
            "body",
            client=mock_client,
            model="m",
            truncated=True,
        )
        assert record.truncated is True
        assert record.reason is None

    def test_seed_forwarded_to_client(self) -> None:
        resp = _mock_openai_response(_valid_payload_json())
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        with patch(
            "laglitsynth.extraction_codebook.extract.random.randint",
            return_value=42,
        ):
            record = extract_codebook(
                "W1",
                "full_text",
                "body",
                client=mock_client,
                model="m",
                truncated=False,
            )
        assert record.seed == 42

    def test_bad_json_yields_llm_parse_failure(self) -> None:
        resp = _mock_openai_response("not json at all")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        record = extract_codebook(
            "W1",
            "abstract_only",
            "abstract",
            client=mock_client,
            model="m",
            truncated=False,
        )
        assert record.reason == "llm-parse-failure"
        assert record.source_basis == "abstract_only"
        assert record.seed is None
        assert record.truncated is False
        # Every content field is None on a sentinel.
        for name in _ExtractionPayload.model_fields:
            assert getattr(record, name) is None

    def test_partial_json_missing_keys_parses_with_none_defaults(self) -> None:
        # Partial LLM response: only one field populated, rest default to None.
        # This is the intended tolerant-parse behaviour — a single laggy field
        # cannot discard the other 29.
        resp = _mock_openai_response('{"integration_scheme": "RK4"}')
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        record = extract_codebook(
            "W1",
            "full_text",
            "body",
            client=mock_client,
            model="m",
            truncated=False,
        )
        assert record.reason is None
        assert record.integration_scheme == "RK4"
        assert record.time_step_strategy is None
        assert record.sub_discipline is None

    def test_bool_field_coerced_to_yes_no_string(self) -> None:
        # LLMs often answer `config_available` with a JSON bool. We coerce
        # to keep the str-only downstream contract.
        resp = _mock_openai_response(
            '{"config_available": true, "code_tracking_software": false}'
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        record = extract_codebook(
            "W1",
            "full_text",
            "body",
            client=mock_client,
            model="m",
            truncated=False,
        )
        assert record.reason is None
        assert record.config_available == "yes"
        assert record.code_tracking_software == "no"

    def test_list_field_coerced_to_joined_string(self) -> None:
        # LLMs treat plural-named fields as lists; coerce to a " / "-joined string.
        resp = _mock_openai_response(
            '{"in_text_locations": ["Section 2.1", "Table 3"]}'
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        record = extract_codebook(
            "W1",
            "full_text",
            "body",
            client=mock_client,
            model="m",
            truncated=False,
        )
        assert record.reason is None
        assert record.in_text_locations == "Section 2.1 / Table 3"

    def test_empty_list_coerced_to_none(self) -> None:
        resp = _mock_openai_response('{"in_text_locations": []}')
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        record = extract_codebook(
            "W1",
            "full_text",
            "body",
            client=mock_client,
            model="m",
            truncated=False,
        )
        assert record.reason is None
        assert record.in_text_locations is None

    def test_extra_fields_dropped_by_coercer(self) -> None:
        # LLMs at t>0 sprinkle extras like "confidence", "notes". The
        # coercer drops unknown keys so a stray field does not nuke the
        # whole 30-field record via extra="forbid".
        resp = _mock_openai_response(
            '{"integration_scheme": "RK4", "confidence": 95, "notes": "extra"}'
        )
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        record = extract_codebook(
            "W1",
            "full_text",
            "body",
            client=mock_client,
            model="m",
            truncated=False,
        )
        assert record.reason is None
        assert record.integration_scheme == "RK4"

    def test_raw_response_captured_on_success(self) -> None:
        content = '{"integration_scheme": "RK4"}'
        resp = _mock_openai_response(content)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        record = extract_codebook(
            "W1",
            "full_text",
            "body",
            client=mock_client,
            model="m",
            truncated=False,
        )
        assert record.raw_response == content

    def test_raw_response_captured_on_llm_parse_failure(self) -> None:
        content = "this is not json at all"
        resp = _mock_openai_response(content)
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        record = extract_codebook(
            "W1",
            "full_text",
            "body",
            client=mock_client,
            model="m",
            truncated=False,
        )
        assert record.reason == "llm-parse-failure"
        assert record.raw_response == content


# --- extract_works cascade ---


class TestExtractWorksCascade:
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

        resp = _mock_openai_response(_valid_payload_json())
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp

        records = list(
            extract_works(
                catalogue,
                extractions,
                output_dir,
                client=mock_client,
                model="m",
                max_records=None,
            )
        )

        assert len(records) == 1
        assert records[0].source_basis == "full_text"
        assert records[0].reason is None
        # Prompt should see the section text, not the abstract.
        call_kwargs = mock_client.chat.completions.create.call_args[1]
        user_msg = call_kwargs["messages"][1]["content"]
        assert "Methods" in user_msg
        assert "Real content." in user_msg
        assert "full_text:" in user_msg
        assert "The abstract." not in user_msg

    def test_abstract_only_branch_when_extraction_missing(
        self, tmp_path: Path
    ) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract text.")
        _write_works_jsonl(catalogue, [work])

        resp = _mock_openai_response(_valid_payload_json())
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp

        records = list(
            extract_works(
                catalogue,
                {},
                output_dir,
                client=mock_client,
                model="m",
                max_records=None,
            )
        )

        assert len(records) == 1
        assert records[0].source_basis == "abstract_only"
        assert records[0].reason is None

        call_kwargs = mock_client.chat.completions.create.call_args[1]
        user_msg = call_kwargs["messages"][1]["content"]
        assert "abstract_only:" in user_msg
        assert "Paper abstract text." in user_msg

    def test_no_source_sentinel_no_llm_call(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract=None)
        _write_works_jsonl(catalogue, [work])

        mock_client = MagicMock()
        records = list(
            extract_works(
                catalogue,
                {},
                output_dir,
                client=mock_client,
                model="m",
                max_records=None,
            )
        )

        assert len(records) == 1
        assert records[0].source_basis == "none"
        assert records[0].reason == "no-source"
        assert records[0].seed is None
        assert records[0].truncated is False
        mock_client.chat.completions.create.assert_not_called()

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

        mock_client = MagicMock()
        records = list(
            extract_works(
                catalogue,
                extractions,
                output_dir,
                client=mock_client,
                model="m",
                max_records=None,
            )
        )

        assert len(records) == 1
        assert records[0].reason == "tei-parse-failure"
        assert records[0].source_basis == "full_text"
        assert records[0].seed is None
        mock_client.chat.completions.create.assert_not_called()

    def test_llm_parse_failure_recorded(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Paper abstract.")
        _write_works_jsonl(catalogue, [work])

        resp = _mock_openai_response("not json at all")
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp

        records = list(
            extract_works(
                catalogue,
                {},
                output_dir,
                client=mock_client,
                model="m",
                max_records=None,
            )
        )

        assert len(records) == 1
        assert records[0].reason == "llm-parse-failure"
        assert records[0].source_basis == "abstract_only"
        assert records[0].seed is None

    def test_seed_recorded_on_successful_record(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        output_dir = tmp_path / "ext_out"
        work = _make_work("W1", abstract="Abstract text.")
        _write_works_jsonl(catalogue, [work])

        resp = _mock_openai_response(_valid_payload_json())
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp

        with patch(
            "laglitsynth.extraction_codebook.extract.random.randint",
            return_value=99,
        ):
            records = list(
                extract_works(
                    catalogue,
                    {},
                    output_dir,
                    client=mock_client,
                    model="m",
                    max_records=None,
                )
            )
        assert records[0].seed == 99

    def test_truncation_flag_on_over_budget_body(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        output_dir = tmp_path / "ext_out"

        work = _make_work("W1", abstract=None)
        _write_works_jsonl(catalogue, [work])

        big_section = (
            f'<div xmlns="{TEI_NS}"><head>H{i}</head>'
            f'<p>{"x" * 20_000}</p></div>'
            for i in range(5)
        )
        _write_tei(output_dir / "tei/W1.tei.xml", "".join(big_section))

        extracted = ExtractedDocument(
            work_id="W1",
            tei_path="tei/W1.tei.xml",
            content_sha256="0" * 64,
            extracted_at="2026-04-17T00:00:00.000000+00:00",
        )
        _write_extractions_jsonl(extractions_path, [extracted])
        extractions = {extracted.work_id: extracted}

        resp = _mock_openai_response(_valid_payload_json())
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp

        records = list(
            extract_works(
                catalogue,
                extractions,
                output_dir,
                client=mock_client,
                model="m",
                max_records=None,
            )
        )
        assert len(records) == 1
        assert records[0].reason is None
        assert records[0].truncated is True

    def test_truncation_flag_false_when_body_fits(self, tmp_path: Path) -> None:
        catalogue = tmp_path / "catalogue.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        output_dir = tmp_path / "ext_out"

        work = _make_work("W1", abstract=None)
        _write_works_jsonl(catalogue, [work])

        _write_tei(
            output_dir / "tei/W1.tei.xml",
            f'<div xmlns="{TEI_NS}"><head>H</head><p>Short.</p></div>',
        )
        extracted = ExtractedDocument(
            work_id="W1",
            tei_path="tei/W1.tei.xml",
            content_sha256="0" * 64,
            extracted_at="2026-04-17T00:00:00.000000+00:00",
        )
        _write_extractions_jsonl(extractions_path, [extracted])
        extractions = {extracted.work_id: extracted}

        resp = _mock_openai_response(_valid_payload_json())
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp

        records = list(
            extract_works(
                catalogue,
                extractions,
                output_dir,
                client=mock_client,
                model="m",
                max_records=None,
            )
        )
        assert records[0].truncated is False


# --- _preflight ---


class TestPreflight:
    def test_preflight_raises_on_connection_failure(self) -> None:
        from laglitsynth.extraction_codebook.extract import _preflight

        args = MagicMock()
        args.base_url = "http://localhost:99999"
        args.model = "nonexistent"

        with patch(
            "laglitsynth.extraction_codebook.extract.OpenAI"
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
    eligible: Path,
    extractions: Path,
    dry_run: bool = False,
    skip_existing: bool = False,
    max_records: int | None = None,
) -> MagicMock:
    args = MagicMock()
    args.eligible = eligible
    args.extractions = extractions
    args.extraction_output_dir = extractions.parent
    args.output_dir = tmp_path / "out"
    args.model = "m"
    args.base_url = "http://x"
    args.max_records = max_records
    args.skip_existing = skip_existing
    args.dry_run = dry_run
    return args


def _make_mock_client(payload_content: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.return_value = _mock_openai_response(
        payload_content
    )
    return client


class TestRun:
    def test_dry_run_writes_nothing(self, tmp_path: Path) -> None:
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

        mock_client = _make_mock_client(_valid_payload_json())
        with (
            patch("laglitsynth.extraction_codebook.extract._preflight"),
            patch(
                "laglitsynth.extraction_codebook.extract.OpenAI",
                return_value=mock_client,
            ),
        ):
            from laglitsynth.extraction_codebook.extract import run

            run(args)

        assert not (args.output_dir).exists() or not any(
            (args.output_dir).iterdir()
        )

    def test_writes_expected_files(self, tmp_path: Path) -> None:
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

        mock_client = _make_mock_client(_valid_payload_json())
        with (
            patch("laglitsynth.extraction_codebook.extract._preflight"),
            patch(
                "laglitsynth.extraction_codebook.extract.OpenAI",
                return_value=mock_client,
            ),
        ):
            from laglitsynth.extraction_codebook.extract import run

            run(args)

        out_dir = args.output_dir
        assert (out_dir / "records.jsonl").exists()
        assert (out_dir / "extraction-codebook-meta.json").exists()

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

    def test_skip_existing_refuses_when_prompt_sha256_differs(
        self, tmp_path: Path
    ) -> None:
        """--skip-existing must raise SystemExit when recorded prompt_sha256 differs."""
        import json

        eligible = tmp_path / "eligible.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        _write_works_jsonl(eligible, [_make_work("W1", abstract="first abstract")])
        extractions_path.write_text("")

        out_dir = tmp_path / "out"
        out_dir.mkdir()

        # Write a meta file with a stale/wrong prompt_sha256.
        stale_meta = {
            "run": {
                "tool": "laglitsynth.extraction_codebook.extract",
                "run_at": "2026-01-01T00:00:00.000000+00:00",
                "validation_skipped": 0,
            },
            "llm": {
                "model": "gemma3:4b",
                "temperature": 0.8,
                "prompt_sha256": "0" * 64,  # deliberately wrong hash
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
        )
        args.output_dir = out_dir

        with (
            patch("laglitsynth.extraction_codebook.extract._preflight"),
            patch("laglitsynth.extraction_codebook.extract.OpenAI"),
        ):
            from laglitsynth.extraction_codebook.extract import run

            with pytest.raises(SystemExit, match="prompt_sha256"):
                run(args)

    def test_skip_existing_processes_only_delta(self, tmp_path: Path) -> None:
        eligible = tmp_path / "eligible.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        works = [
            _make_work("W1", abstract="first abstract"),
            _make_work("W2", abstract="second abstract"),
        ]
        _write_works_jsonl(eligible, works)
        extractions_path.write_text("")

        out_dir = tmp_path / "out"
        out_dir.mkdir()
        # Prior record for W1 only.
        payload: dict[str, str | None] = {
            name: None for name in _ExtractionPayload.model_fields
        }
        prior = ExtractionRecord(
            work_id="W1",
            source_basis="abstract_only",
            reason=None,
            seed=1,
            truncated=False,
            **payload,
        )
        (out_dir / "records.jsonl").write_text(prior.model_dump_json() + "\n")

        args = _make_run_args(
            tmp_path,
            eligible=eligible,
            extractions=extractions_path,
            skip_existing=True,
        )
        args.output_dir = out_dir

        mock_client = _make_mock_client(_valid_payload_json())
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
        # User message should mention W2's abstract.
        user_msg = mock_client.chat.completions.create.call_args[1][
            "messages"
        ][1]["content"]
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

    def test_meta_counts_per_branch(self, tmp_path: Path) -> None:
        eligible = tmp_path / "eligible.jsonl"
        extractions_path = tmp_path / "extraction.jsonl"
        output_dir_ext = tmp_path / "ext_out"

        # W1: full_text, W2: abstract_only, W3: no-source, W4: tei-parse-failure.
        w1 = _make_work("W1", abstract="abs1")
        w2 = _make_work("W2", abstract="abs2")
        w3 = _make_work("W3", abstract=None)
        w4 = _make_work("W4", abstract="abs4")
        _write_works_jsonl(eligible, [w1, w2, w3, w4])

        _write_tei(
            output_dir_ext / "tei/W1.tei.xml",
            f'<div xmlns="{TEI_NS}"><head>M</head><p>Real methods.</p></div>',
        )
        _write_malformed_tei(output_dir_ext / "tei/W4.tei.xml")
        records = [
            ExtractedDocument(
                work_id="W1",
                tei_path="tei/W1.tei.xml",
                content_sha256="0" * 64,
                extracted_at="2026-04-17T00:00:00.000000+00:00",
            ),
            ExtractedDocument(
                work_id="W4",
                tei_path="tei/W4.tei.xml",
                content_sha256="0" * 64,
                extracted_at="2026-04-17T00:00:00.000000+00:00",
            ),
        ]
        _write_extractions_jsonl(extractions_path, records)

        args = _make_run_args(
            tmp_path,
            eligible=eligible,
            extractions=extractions_path,
        )
        args.extraction_output_dir = output_dir_ext

        mock_client = _make_mock_client(_valid_payload_json())
        with (
            patch("laglitsynth.extraction_codebook.extract._preflight"),
            patch(
                "laglitsynth.extraction_codebook.extract.OpenAI",
                return_value=mock_client,
            ),
        ):
            from laglitsynth.extraction_codebook.extract import run

            run(args)

        meta = json.loads(
            (args.output_dir / "extraction-codebook-meta.json").read_text()
        )
        assert meta["full_text_count"] == 1
        assert meta["abstract_only_count"] == 1
        assert meta["skipped_count"] == 2  # no-source + tei-parse-failure
        assert meta["llm_parse_failure_count"] == 0
        # The full_text branch gets source_basis="full_text"; the
        # tei-parse-failure sentinel also records source_basis="full_text".
        assert meta["by_source_basis"].get("full_text") == 2
        assert meta["by_source_basis"].get("abstract_only") == 1
        assert meta["by_source_basis"].get("none") == 1


# --- CLI wiring ---


class TestCliWiring:
    def test_subparser_registered(self) -> None:
        from laglitsynth import cli

        parser_args: list[str] = []

        def fake_run(args: Any) -> None:
            parser_args.append(args.command)

        # The subcommand must parse without error.
        import argparse

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

        # Running with the subcommand should reach our run() (which we
        # patch) without argparse rejecting the command.
        with patch(
            "laglitsynth.extraction_codebook.extract.run"
        ) as mock_run:
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
