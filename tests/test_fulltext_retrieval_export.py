"""Tests for the fulltext-retrieval-export subcommand."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import MagicMock

from laglitsynth.catalogue_fetch.models import Location, Source, Work
from laglitsynth.fulltext_retrieval.export import run, write_export
from laglitsynth.fulltext_retrieval.models import PdfProvenanceRecord, PdfSource
from laglitsynth.fulltext_retrieval.store import write_provenance
from laglitsynth.screening_abstracts.models import ScreeningVerdict

from conftest import _make_authorship, _make_work, _write_works_jsonl


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
