"""Tests for the fulltext-retrieval-export subcommand."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from laglitsynth.catalogue_fetch.models import Location, Source, Work
from laglitsynth.fulltext_retrieval.export import run, write_export
from laglitsynth.fulltext_retrieval.models import PdfProvenanceRecord, PdfSource
from laglitsynth.fulltext_retrieval.store import write_provenance
from laglitsynth.layout import (
    PDF_MANIFEST_FILENAME,
    deduplicated_path,
    export_dir,
    screening_verdicts_path,
)
from laglitsynth.screening_abstracts.models import ScreeningVerdict

from conftest import (
    TEST_RUN_ID,
    _make_authorship,
    _make_work,
    _write_manifest,
    _write_works_jsonl,
)


def _write_verdicts_jsonl(path: Path, verdicts: list[ScreeningVerdict]) -> None:
    with open(path, "w") as f:
        for v in verdicts:
            f.write(v.model_dump_json() + "\n")


def _work_with_journal(
    work_id: str,
    *,
    doi: str | None,
    title: str = "A Title",
    year: int = 2020,
    journal: str = "J. Ocean",
    author: str = "Smith, J.",
) -> Work:
    w = _make_work(
        work_id,
        title=title,
        doi=doi,
        publication_year=year,
        authorships=[_make_authorship(author)],
    )
    w.primary_location = Location(source=Source(id="S1", display_name=journal))
    return w


def _read_manifest(path: Path) -> list[dict[str, str]]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


class TestWriteExport:
    def test_doiless_in_csv_and_ris_not_in_dois(self, tmp_path: Path) -> None:
        works = [
            _work_with_journal("https://openalex.org/W1", doi="10.1/a"),
            _work_with_journal("https://openalex.org/W2", doi=None),
        ]
        write_export(works, tmp_path)

        dois = (tmp_path / "dois.txt").read_text().splitlines()
        assert dois == ["https://doi.org/10.1/a"]

        manifest = _read_manifest(tmp_path / "pdf-manifest.csv")
        by_id = {r["work_id"]: r for r in manifest}
        assert by_id["https://openalex.org/W1"]["doi"] == "10.1/a"
        assert by_id["https://openalex.org/W1"]["stem"] == "W1"
        assert by_id["https://openalex.org/W1"]["expected_filename"] == "W1.pdf"
        # DOI-less work is in CSV with empty DOI.
        assert by_id["https://openalex.org/W2"]["doi"] == ""
        assert by_id["https://openalex.org/W2"]["expected_filename"] == "W2.pdf"

        ris = (tmp_path / "missing.ris").read_text()
        assert ris.count("TY  - JOUR") == 2
        assert "DO  - 10.1/a" in ris
        assert "TI  - A Title" in ris
        assert "AU  - Smith, J." in ris
        assert "ER  - " in ris

    def test_doi_url_prefix_stripped_then_rebuilt(self, tmp_path: Path) -> None:
        works = [
            _work_with_journal(
                "https://openalex.org/W1", doi="https://doi.org/10.1/B"
            ),
        ]
        write_export(works, tmp_path)
        assert (tmp_path / "dois.txt").read_text().splitlines() == [
            "https://doi.org/10.1/B"
        ]
        manifest = _read_manifest(tmp_path / "pdf-manifest.csv")
        assert manifest[0]["doi"] == "10.1/B"

    def test_manifest_title_and_year_columns(self, tmp_path: Path) -> None:
        works = [
            _work_with_journal(
                "https://openalex.org/W1",
                doi="10.1/a",
                title="A Great Paper",
                year=2019,
            ),
        ]
        write_export(works, tmp_path)
        manifest = _read_manifest(tmp_path / "pdf-manifest.csv")
        assert manifest[0]["title"] == "A Great Paper"
        assert manifest[0]["year"] == "2019"
        # Column order: doi, then title, then year, then expected_filename.
        assert list(manifest[0].keys()) == [
            "work_id",
            "stem",
            "doi",
            "title",
            "year",
            "expected_filename",
        ]

    def test_manifest_null_title_and_year_write_empty_cells(
        self, tmp_path: Path
    ) -> None:
        work = _make_work(
            "https://openalex.org/W1",
            title=None,
            doi="10.1/a",
            publication_year=None,
        )
        write_export([work], tmp_path)
        manifest = _read_manifest(tmp_path / "pdf-manifest.csv")
        assert manifest[0]["title"] == ""
        assert manifest[0]["year"] == ""
        # Never the literal string "None".
        assert "None" not in manifest[0]["title"]
        assert "None" not in manifest[0]["year"]


class TestNoDoiCsv:
    def test_empty_when_all_works_have_a_doi(self, tmp_path: Path) -> None:
        works = [
            _work_with_journal("https://openalex.org/W1", doi="10.1/a"),
            _work_with_journal("https://openalex.org/W2", doi="10.1/b"),
        ]
        write_export(works, tmp_path)

        no_doi_path = tmp_path / "no-doi.csv"
        assert no_doi_path.exists()
        rows = _read_manifest(no_doi_path)
        assert rows == []
        # Header row is always present, even for zero data rows.
        with open(no_doi_path, newline="") as f:
            header = next(csv.reader(f))
        assert header == ["work_id", "stem", "title", "year", "expected_filename"]

    def test_doi_less_work_listed_and_absent_from_dois_txt(
        self, tmp_path: Path
    ) -> None:
        works = [
            _work_with_journal(
                "https://openalex.org/W1",
                doi=None,
                title="No DOI Paper",
                year=2021,
            ),
            _work_with_journal("https://openalex.org/W2", doi="10.1/b"),
        ]
        write_export(works, tmp_path)

        no_doi_rows = _read_manifest(tmp_path / "no-doi.csv")
        assert len(no_doi_rows) == 1
        row = no_doi_rows[0]
        assert row["work_id"] == "https://openalex.org/W1"
        assert row["stem"] == "W1"
        assert row["title"] == "No DOI Paper"
        assert row["year"] == "2021"
        assert row["expected_filename"] == "W1.pdf"

        dois = (tmp_path / "dois.txt").read_text().splitlines()
        assert "https://doi.org/W1" not in dois
        assert dois == ["https://doi.org/10.1/b"]


class TestReadme:
    def test_names_the_work_count(self, tmp_path: Path) -> None:
        works = [
            _work_with_journal("https://openalex.org/W1", doi="10.1/a"),
            _work_with_journal("https://openalex.org/W2", doi=None),
            _work_with_journal("https://openalex.org/W3", doi=None),
        ]
        write_export(works, tmp_path)

        readme = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert "3 works" in readme
        assert "(1 with a DOI, 2 without)" in readme
        # The no-DOI works are pointed at their own list.
        assert "no-doi.csv" in readme
        assert "2 works with no DOI" in readme
        # The return instructions: no renaming, optional filename,doi sidecar.
        assert "send it back as it is" in readme
        assert "`filename`" in readme
        assert "`doi`" in readme

    def test_omits_no_doi_mentions_when_every_work_has_a_doi(
        self, tmp_path: Path
    ) -> None:
        works = [
            _work_with_journal("https://openalex.org/W1", doi="10.1/a"),
            _work_with_journal("https://openalex.org/W2", doi="10.1/b"),
        ]
        write_export(works, tmp_path)

        readme = (tmp_path / "README.md").read_text(encoding="utf-8")
        assert "2 works still need a PDF." in readme
        assert "without)" not in readme
        assert "no-doi.csv" not in readme


class TestRun:
    def test_only_missing_in_selection_exported(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        works = [
            _work_with_journal("https://openalex.org/W1", doi="10.1/a"),  # missing
            _work_with_journal("https://openalex.org/W2", doi="10.1/b"),  # has pdf
            _work_with_journal("https://openalex.org/W3", doi="10.1/c"),  # below thresh
        ]
        catalogue_path = tmp_path / "catalogue.jsonl"
        verdicts_path = tmp_path / "verdicts.jsonl"
        _write_works_jsonl(catalogue_path, works)
        _write_verdicts_jsonl(
            verdicts_path,
            [
                ScreeningVerdict(work_id="https://openalex.org/W1", relevance_score=80),
                ScreeningVerdict(work_id="https://openalex.org/W2", relevance_score=80),
                ScreeningVerdict(work_id="https://openalex.org/W3", relevance_score=10),
            ],
        )

        # W2 already has a PDF (oa); W1 has a missing record.
        write_provenance(
            data_dir,
            {
                "https://openalex.org/W2": PdfProvenanceRecord(
                    work_id="https://openalex.org/W2",
                    stem="W2",
                    doi="10.1/b",
                    source=PdfSource.oa,
                    source_url="https://example.com/p.pdf",
                    pdf_path="pdfs/W2.pdf",
                    content_sha256="x",
                    obtained_at="2026-01-01T00:00:00+00:00",
                ),
                "https://openalex.org/W1": PdfProvenanceRecord(
                    work_id="https://openalex.org/W1",
                    stem="W1",
                    doi="10.1/a",
                    source=PdfSource.missing,
                    source_url=None,
                    pdf_path=None,
                    content_sha256=None,
                    obtained_at="2026-01-01T00:00:00+00:00",
                ),
            },
        )

        args = MagicMock()
        args.catalogue = catalogue_path
        args.screening_verdicts = verdicts_path
        args.run_id = None
        args.screening_threshold = 50.0
        args.data_dir = data_dir
        args.export_dir = None

        run(args)

        manifest = _read_manifest(export_dir(data_dir) / PDF_MANIFEST_FILENAME)
        ids = {r["work_id"] for r in manifest}
        # Only W1: W2 has a PDF, W3 is below threshold.
        assert ids == {"https://openalex.org/W1"}


class TestConventionalPaths:
    def _layout(self, data_dir: Path, run_id: str = TEST_RUN_ID) -> list[Work]:
        works = [
            _work_with_journal("https://openalex.org/W1", doi="10.1/a"),
            _work_with_journal("https://openalex.org/W2", doi=None),
        ]
        catalogue_path = deduplicated_path(data_dir)
        catalogue_path.parent.mkdir(parents=True, exist_ok=True)
        _write_works_jsonl(catalogue_path, works)
        verdicts_path = screening_verdicts_path(data_dir, run_id)
        verdicts_path.parent.mkdir(parents=True, exist_ok=True)
        _write_verdicts_jsonl(
            verdicts_path,
            [ScreeningVerdict(work_id=w.id, relevance_score=80) for w in works],
        )
        return works

    def _args(self, data_dir: Path) -> MagicMock:
        args = MagicMock()
        args.catalogue = None
        args.screening_verdicts = None
        args.run_id = None
        args.screening_threshold = 50.0
        args.data_dir = data_dir
        args.export_dir = None
        return args

    def test_data_dir_only_matches_fully_flagged_invocation(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "data"
        self._layout(data_dir)
        _write_manifest(data_dir)

        flagged_args = self._args(data_dir)
        flagged_args.catalogue = deduplicated_path(data_dir)
        flagged_args.screening_verdicts = screening_verdicts_path(data_dir, TEST_RUN_ID)
        flagged_args.export_dir = tmp_path / "flagged-export"
        run(flagged_args)

        run(self._args(data_dir))

        default_export_dir = export_dir(data_dir)
        for filename in (
            "dois.txt",
            "missing.ris",
            PDF_MANIFEST_FILENAME,
            "no-doi.csv",
            "README.md",
        ):
            assert (flagged_args.export_dir / filename).read_text() == (
                default_export_dir / filename
            ).read_text()

    def test_explicit_flag_wins(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        self._layout(data_dir)
        _write_manifest(data_dir)

        other = _work_with_journal("https://openalex.org/W9", doi="10.1/z")
        explicit_catalogue = tmp_path / "explicit-catalogue.jsonl"
        _write_works_jsonl(explicit_catalogue, [other])
        explicit_verdicts = tmp_path / "explicit-verdicts.jsonl"
        _write_verdicts_jsonl(
            explicit_verdicts,
            [ScreeningVerdict(work_id=other.id, relevance_score=80)],
        )

        args = self._args(data_dir)
        args.catalogue = explicit_catalogue
        args.screening_verdicts = explicit_verdicts
        run(args)

        rows = _read_manifest(export_dir(data_dir) / PDF_MANIFEST_FILENAME)
        assert {r["work_id"] for r in rows} == {other.id}

    def test_missing_catalogue_names_upstream_and_flag(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        _write_manifest(data_dir)

        with pytest.raises(SystemExit) as exc_info:
            run(self._args(data_dir))
        msg = str(exc_info.value)
        assert "run catalogue-dedup first" in msg
        assert "--catalogue" in msg

    def test_no_manifest_and_no_run_id_exits(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        self._layout(data_dir)

        with pytest.raises(SystemExit) as exc_info:
            run(self._args(data_dir))
        assert "--run-id" in str(exc_info.value)
