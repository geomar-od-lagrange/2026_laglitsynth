"""Tests for the shared laglitsynth.ids module."""

from __future__ import annotations

from laglitsynth.ids import filename_to_work_id, work_id_to_filename


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
