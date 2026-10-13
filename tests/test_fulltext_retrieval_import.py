"""Tests for the fulltext-retrieval-import subcommand."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pypdf import PdfWriter

from laglitsynth.fulltext_retrieval.import_ import import_pdfs, read_manifest, run
from laglitsynth.fulltext_retrieval.models import PdfSource
from laglitsynth.fulltext_retrieval.store import load_provenance, store_pdf_path
from laglitsynth.layout import pdf_manifest_path


def _write_pdf_manifest(
    path: Path, rows: list[tuple[str, str, str]]
) -> None:
    """rows: (work_id, stem, doi)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["work_id", "stem", "doi", "expected_filename"])
        for work_id, stem, doi in rows:
            writer.writerow([work_id, stem, doi, f"{stem}.pdf"])


def _make_pdf(path: Path, *, doi_in_metadata: str | None = None) -> None:
    """Write a minimal real PDF, optionally with a DOI in /Subject metadata."""
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    if doi_in_metadata is not None:
        writer.add_metadata({"/Subject": f"doi:{doi_in_metadata}"})
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        writer.write(f)


class TestReadManifest:
    def test_extra_export_columns_round_trip(self, tmp_path: Path) -> None:
        # The export writes title/year/expected_filename for the collaborator;
        # read_manifest ignores them and keeps work_id/stem/doi.
        manifest = tmp_path / "pdf-manifest.csv"
        with open(manifest, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["work_id", "stem", "doi", "title", "year", "expected_filename"]
            )
            writer.writerow(
                [
                    "https://openalex.org/W1",
                    "W1",
                    "10.1/a",
                    "A Great Paper",
                    "2019",
                    "W1.pdf",
                ]
            )
            writer.writerow(["https://openalex.org/W2", "W2", "", "", "", "W2.pdf"])

        entries = read_manifest(manifest)
        assert [(e.work_id, e.stem, e.doi) for e in entries] == [
            ("https://openalex.org/W1", "W1", "10.1/a"),
            ("https://openalex.org/W2", "W2", None),
        ]


class TestImportByDoi:
    def test_embedded_doi_match_zotero(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        manifest = tmp_path / "pdf-manifest.csv"
        _write_pdf_manifest(
            manifest, [("https://openalex.org/W1", "W1", "10.1234/abc")]
        )
        # Zotero-named file (item title), DOI only resolvable via embedded metadata.
        _make_pdf(import_dir / "Some Paper Title.pdf", doi_in_metadata="10.1234/abc")

        summary, still_missing = import_pdfs(
            import_dir, manifest, data_dir, PdfSource.zotero_import, overwrite=False
        )

        assert summary.copied == 1
        assert still_missing == 0
        assert store_pdf_path(data_dir, "W1").exists()
        rec = load_provenance(data_dir)["https://openalex.org/W1"]
        assert rec.source == PdfSource.zotero_import
        assert rec.pdf_path == "pdfs/W1.pdf"
        assert rec.content_sha256 is not None

    def test_first_page_text_doi_with_trailing_period(self, tmp_path: Path) -> None:
        # The DOI is only resolvable via first-page text, where it is rendered
        # in running prose with a trailing period (``10.1234/abc.``). The
        # trailing punctuation must be stripped so it matches the bare manifest
        # DOI ``10.1234/abc``. Metadata carries no DOI, so only the first-page
        # text path can resolve this work.
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        manifest = tmp_path / "pdf-manifest.csv"
        _write_pdf_manifest(
            manifest, [("https://openalex.org/W1", "W1", "10.1234/abc")]
        )
        _make_pdf(import_dir / "Some Paper Title.pdf")  # no embedded DOI metadata

        # pypdf cannot render extractable text from a blank page, so stand in a
        # reader whose metadata has no DOI and whose first page text carries the
        # DOI followed by a sentence-ending period.
        fake_page = MagicMock()
        fake_page.extract_text.return_value = (
            "Cite as: doi 10.1234/abc. Published 2024."
        )
        fake_reader = MagicMock()
        fake_reader.metadata = None
        fake_reader.pages = [fake_page]

        with patch(
            "laglitsynth.fulltext_retrieval.import_.PdfReader",
            return_value=fake_reader,
        ):
            summary, still_missing = import_pdfs(
                import_dir, manifest, data_dir, PdfSource.zotero_import, overwrite=False
            )

        assert summary.copied == 1
        assert still_missing == 0
        assert load_provenance(data_dir)["https://openalex.org/W1"].stem == "W1"


class TestImportByFilenameStem:
    def test_filename_stem_match_manual(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        manifest = tmp_path / "pdf-manifest.csv"
        _write_pdf_manifest(manifest, [("https://openalex.org/W2", "W2", "")])
        _make_pdf(import_dir / "W2.pdf")  # named by stem, no DOI

        summary, still_missing = import_pdfs(
            import_dir, manifest, data_dir, PdfSource.manual, overwrite=False
        )

        assert summary.copied == 1
        rec = load_provenance(data_dir)["https://openalex.org/W2"]
        assert rec.source == PdfSource.manual


class TestImportSidecar:
    def test_sidecar_json_maps_filename_to_doi(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        manifest = tmp_path / "pdf-manifest.csv"
        _write_pdf_manifest(
            manifest, [("https://openalex.org/W3", "W3", "10.9/zzz")]
        )
        # File named neither by stem nor with an embedded DOI; sidecar maps it.
        _make_pdf(import_dir / "weird-name.pdf")
        (import_dir / "map.json").write_text(
            json.dumps({"weird-name.pdf": "10.9/zzz"}), encoding="utf-8"
        )

        summary, _ = import_pdfs(
            import_dir, manifest, data_dir, PdfSource.manual, overwrite=False
        )
        assert summary.copied == 1
        assert load_provenance(data_dir)["https://openalex.org/W3"].stem == "W3"


class TestImportSkips:
    def test_non_pdf_and_unmatched_skipped(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        manifest = tmp_path / "pdf-manifest.csv"
        _write_pdf_manifest(manifest, [("https://openalex.org/W1", "W1", "10.1/a")])
        import_dir.mkdir()
        # Not a PDF (wrong magic bytes) but .pdf extension.
        (import_dir / "fake.pdf").write_bytes(b"<html>nope</html>")
        # A real PDF that matches nothing in the manifest.
        _make_pdf(import_dir / "Unknown.pdf")

        summary, still_missing = import_pdfs(
            import_dir, manifest, data_dir, PdfSource.manual, overwrite=False
        )

        assert summary.copied == 0
        assert summary.skipped_not_pdf == ["fake.pdf"]
        assert summary.skipped_unmatched == ["Unknown.pdf"]
        assert still_missing == 1


class TestImportDedup:
    def test_identical_reimport_is_noop(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        manifest = tmp_path / "pdf-manifest.csv"
        _write_pdf_manifest(manifest, [("https://openalex.org/W2", "W2", "")])
        _make_pdf(import_dir / "W2.pdf")

        import_pdfs(import_dir, manifest, data_dir, PdfSource.manual, overwrite=False)
        summary, _ = import_pdfs(
            import_dir, manifest, data_dir, PdfSource.manual, overwrite=False
        )
        assert summary.copied == 0
        assert summary.skipped_duplicate == ["W2.pdf"]

    def test_differing_pdf_skipped_without_overwrite(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        manifest = tmp_path / "pdf-manifest.csv"
        _write_pdf_manifest(manifest, [("https://openalex.org/W2", "W2", "")])
        _make_pdf(import_dir / "W2.pdf")
        import_pdfs(import_dir, manifest, data_dir, PdfSource.manual, overwrite=False)

        # A different PDF for the same stem.
        _make_pdf(import_dir / "W2.pdf", doi_in_metadata="10.5/changed")
        first_sha = load_provenance(data_dir)["https://openalex.org/W2"].content_sha256

        summary, _ = import_pdfs(
            import_dir, manifest, data_dir, PdfSource.manual, overwrite=False
        )
        assert summary.copied == 0
        assert summary.skipped_existing_differs == ["W2.pdf"]
        # Existing wins: provenance unchanged.
        assert (
            load_provenance(data_dir)["https://openalex.org/W2"].content_sha256
            == first_sha
        )

    def test_differing_pdf_replaced_with_overwrite(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        manifest = tmp_path / "pdf-manifest.csv"
        _write_pdf_manifest(manifest, [("https://openalex.org/W2", "W2", "")])
        _make_pdf(import_dir / "W2.pdf")
        import_pdfs(import_dir, manifest, data_dir, PdfSource.manual, overwrite=False)
        first_sha = load_provenance(data_dir)["https://openalex.org/W2"].content_sha256

        _make_pdf(import_dir / "W2.pdf", doi_in_metadata="10.5/changed")
        summary, _ = import_pdfs(
            import_dir, manifest, data_dir, PdfSource.manual, overwrite=True
        )
        assert summary.copied == 1
        assert (
            load_provenance(data_dir)["https://openalex.org/W2"].content_sha256
            != first_sha
        )


class TestImportRun:
    def test_run_via_args(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        manifest = tmp_path / "pdf-manifest.csv"
        _write_pdf_manifest(manifest, [("https://openalex.org/W1", "W1", "")])
        _make_pdf(import_dir / "W1.pdf")

        args = MagicMock()
        args.import_dir = import_dir
        args.pdf_manifest = manifest
        args.data_dir = data_dir
        args.source = "manual"
        args.overwrite = False

        run(args)
        assert store_pdf_path(data_dir, "W1").exists()
        assert (
            load_provenance(data_dir)["https://openalex.org/W1"].source
            == PdfSource.manual
        )


class TestConventionalPaths:
    def _args(self, tmp_path: Path, data_dir: Path) -> MagicMock:
        args = MagicMock()
        args.import_dir = tmp_path / "incoming"
        args.pdf_manifest = None
        args.data_dir = data_dir
        args.source = "manual"
        args.overwrite = False
        return args

    def test_omitted_flag_resolves_to_conventional_path(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        _write_pdf_manifest(
            pdf_manifest_path(data_dir), [("https://openalex.org/W1", "W1", "")]
        )
        _make_pdf(tmp_path / "incoming" / "W1.pdf")

        run(self._args(tmp_path, data_dir))

        assert store_pdf_path(data_dir, "W1").exists()
        assert (
            load_provenance(data_dir)["https://openalex.org/W1"].source
            == PdfSource.manual
        )

    def test_explicit_flag_wins(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        _write_pdf_manifest(
            pdf_manifest_path(data_dir),
            [("https://openalex.org/W-wrong", "W-wrong", "")],
        )
        explicit = tmp_path / "explicit-manifest.csv"
        _write_pdf_manifest(explicit, [("https://openalex.org/W1", "W1", "")])
        _make_pdf(tmp_path / "incoming" / "W1.pdf")

        args = self._args(tmp_path, data_dir)
        args.pdf_manifest = explicit
        run(args)

        assert set(load_provenance(data_dir)) == {"https://openalex.org/W1"}

    def test_missing_pdf_manifest_names_upstream_and_flag(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            run(self._args(tmp_path, tmp_path / "data"))
        msg = str(exc_info.value)
        assert "run fulltext-retrieval-export first" in msg
        assert "--pdf-manifest" in msg
