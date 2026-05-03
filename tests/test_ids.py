"""Tests for the shared laglitsynth.ids module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from laglitsynth.ids import (
    RUN_ID_RE,
    filename_to_work_id,
    generate_run_id,
    work_id_to_filename,
)


class TestWorkIdToFilename:
    def test_full_url(self) -> None:
        assert work_id_to_filename("https://openalex.org/W1234") == "W1234"

    def test_bare_id_passthrough(self) -> None:
        assert work_id_to_filename("W1234") == "W1234"


class TestFilenameToWorkId:
    def test_valid_stem(self) -> None:
        assert filename_to_work_id("W1234") == "https://openalex.org/W1234"

    def test_sample_stem_rejected(self) -> None:
        assert filename_to_work_id("sample") is None

    def test_alphanumeric_garbage_rejected(self) -> None:
        assert filename_to_work_id("W1234abc") is None

    def test_empty_string_rejected(self) -> None:
        assert filename_to_work_id("") is None

    def test_bare_w_rejected(self) -> None:
        assert filename_to_work_id("W") is None

    def test_large_id(self) -> None:
        assert filename_to_work_id("W9876543210") == "https://openalex.org/W9876543210"


class TestGenerateRunId:
    def test_format_matches_regex(self) -> None:
        assert RUN_ID_RE.match(generate_run_id())

    def test_two_calls_differ(self) -> None:
        assert generate_run_id() != generate_run_id()

    def test_injected_now_in_timestamp(self) -> None:
        moment = datetime(2026, 5, 3, 14, 30, 0, tzinfo=UTC)
        run_id = generate_run_id(moment)
        assert run_id.startswith("2026-05-03T14-30-00_")
        assert RUN_ID_RE.match(run_id)

    def test_microseconds_stripped(self) -> None:
        moment = datetime(2026, 5, 3, 14, 30, 0, 999999, tzinfo=UTC)
        run_id = generate_run_id(moment)
        assert run_id.startswith("2026-05-03T14-30-00_")

    def test_non_utc_coerced_to_utc(self) -> None:
        cest = timezone(timedelta(hours=2))
        # 2026-05-03T14:30:00+02:00 → 2026-05-03T12:30:00Z
        moment = datetime(2026, 5, 3, 14, 30, 0, tzinfo=cest)
        run_id = generate_run_id(moment)
        assert run_id.startswith("2026-05-03T12-30-00_")
        assert RUN_ID_RE.match(run_id)


class TestGenerateRunIdCli:
    def test_subcommand_prints_valid_id(self, capsys: object) -> None:
        from laglitsynth.cli import main

        main(["generate-run-id"])
        captured = capsys.readouterr()  # type: ignore[attr-defined]
        assert RUN_ID_RE.match(captured.out.strip())
