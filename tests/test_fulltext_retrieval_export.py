"""Tests for the fulltext-retrieval-export subcommand."""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from laglitsynth.catalogue_fetch.models import Location, Source, Work
from laglitsynth.fulltext_retrieval.export import PDF_MANIFEST_FILENAME, run, write_export
from laglitsynth.fulltext_retrieval.models import PdfProvenanceRecord, PdfSource
from laglitsynth.fulltext_retrieval.store import write_provenance
from laglitsynth.io import write_meta
from laglitsynth.manifest import RunManifest, StageEntry, load_manifest
from laglitsynth.models import RunMeta
from laglitsynth.screening_abstracts.models import ScreeningVerdict

from conftest import _make_authorship, _make_work, _write_works_jsonl


def _seed_manifest(data_dir: Path, stages: list[StageEntry]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    write_meta(
        data_dir / "manifest.json",
        RunManifest(
            run=RunMeta(
                tool="laglitsynth.manifest",
                run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
                validation_skipped=0,
            ),
            run_id="2026-01-01T00-00-00_abcdef123456",
            queries=["test query"],
            data_dir=str(data_dir),
            stages=stages,
        ),
    )


def _stage_entry(stage: str, output: str) -> StageEntry:
    return StageEntry(
        stage=stage,
        run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        inputs={},
        output=output,
        meta_path=None,
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
        # Names the two return shapes the importer accepts.
        assert "<stem>.pdf" in readme
        assert "DOI" in readme


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
        args.screening_threshold = 50.0
        args.data_dir = data_dir
        args.export_dir = None

        run(args)

        export_dir = data_dir / "pdfs" / "export"
        manifest = _read_manifest(export_dir / "pdf-manifest.csv")
        ids = {r["work_id"] for r in manifest}
        # Only W1: W2 has a PDF, W3 is below threshold.
        assert ids == {"https://openalex.org/W1"}


class TestManifestWiring:
    def _seed_inputs_and_manifest(self, data_dir: Path) -> list[Work]:
        works = [
            _work_with_journal("https://openalex.org/W1", doi="10.1/a"),
            _work_with_journal("https://openalex.org/W2", doi=None),
        ]
        catalogue_path = data_dir / "catalogue-dedup" / "deduplicated.jsonl"
        catalogue_path.parent.mkdir(parents=True, exist_ok=True)
        _write_works_jsonl(catalogue_path, works)

        verdicts_path = data_dir / "screening-abstracts" / "run1" / "verdicts.jsonl"
        verdicts_path.parent.mkdir(parents=True, exist_ok=True)
        with open(verdicts_path, "w") as f:
            for w in works:
                f.write(
                    ScreeningVerdict(work_id=w.id, relevance_score=80).model_dump_json()
                    + "\n"
                )

        _seed_manifest(
            data_dir,
            [
                _stage_entry("catalogue-dedup", str(catalogue_path)),
                _stage_entry("screening-abstracts", str(verdicts_path)),
            ],
        )
        return works

    def test_data_dir_only_matches_fully_flagged_invocation(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "data"
        self._seed_inputs_and_manifest(data_dir)
        catalogue_path = data_dir / "catalogue-dedup" / "deduplicated.jsonl"
        verdicts_path = data_dir / "screening-abstracts" / "run1" / "verdicts.jsonl"

        # Fully-flagged invocation, writing to its own bundle dir.
        flagged_args = MagicMock()
        flagged_args.catalogue = catalogue_path
        flagged_args.screening_verdicts = verdicts_path
        flagged_args.screening_threshold = 50.0
        flagged_args.data_dir = data_dir
        flagged_args.export_dir = tmp_path / "flagged-export"
        run(flagged_args)

        # --data-dir-only invocation: catalogue/verdicts resolved from the
        # manifest, export dir at the default location.
        bare_args = MagicMock()
        bare_args.catalogue = None
        bare_args.screening_verdicts = None
        bare_args.screening_threshold = 50.0
        bare_args.data_dir = data_dir
        bare_args.export_dir = None
        run(bare_args)

        default_export_dir = data_dir / "pdfs" / "export"
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

    def test_appends_entry_with_bundle_dir_as_output(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        self._seed_inputs_and_manifest(data_dir)

        args = MagicMock()
        args.catalogue = None
        args.screening_verdicts = None
        args.screening_threshold = 50.0
        args.data_dir = data_dir
        args.export_dir = None
        run(args)

        manifest = load_manifest(data_dir)
        assert manifest is not None
        entry = manifest.stages[-1]
        assert entry.stage == "fulltext-retrieval-export"
        assert entry.output == str(data_dir / "pdfs" / "export")
        assert entry.meta_path is None

    def test_no_manifest_and_omitted_catalogue_names_the_flag(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "data"
        args = MagicMock()
        args.catalogue = None
        args.screening_verdicts = None
        args.screening_threshold = 50.0
        args.data_dir = data_dir
        args.export_dir = None

        try:
            run(args)
        except SystemExit as exc:
            assert "--catalogue" in str(exc)
        else:
            raise AssertionError("expected SystemExit without a manifest")
