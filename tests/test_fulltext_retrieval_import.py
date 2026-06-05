"""Tests for the fulltext-retrieval-import subcommand."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import MagicMock

from pypdf import PdfWriter

from laglitsynth.fulltext_retrieval.import_ import import_pdfs, run
from laglitsynth.fulltext_retrieval.models import PdfSource
from laglitsynth.fulltext_retrieval.store import load_provenance, store_pdf_path


def _write_manifest(
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


class TestImportByDoi:
    def test_embedded_doi_match_zotero(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        manifest = tmp_path / "pdf-manifest.csv"
        _write_manifest(
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


class TestImportByFilenameStem:
    def test_filename_stem_match_manual(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        manifest = tmp_path / "pdf-manifest.csv"
        _write_manifest(manifest, [("https://openalex.org/W2", "W2", "")])
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
        _write_manifest(
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
        _write_manifest(manifest, [("https://openalex.org/W1", "W1", "10.1/a")])
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
        _write_manifest(manifest, [("https://openalex.org/W2", "W2", "")])
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
        _write_manifest(manifest, [("https://openalex.org/W2", "W2", "")])
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
        _write_manifest(manifest, [("https://openalex.org/W2", "W2", "")])
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
        _write_manifest(manifest, [("https://openalex.org/W1", "W1", "")])
        _make_pdf(import_dir / "W1.pdf")

        args = MagicMock()
        args.import_dir = import_dir
        args.manifest = manifest
        args.data_dir = data_dir
        args.source = "manual"
        args.overwrite = False

        run(args)
        assert store_pdf_path(data_dir, "W1").exists()
        assert (
            load_provenance(data_dir)["https://openalex.org/W1"].source
            == PdfSource.manual
        )
