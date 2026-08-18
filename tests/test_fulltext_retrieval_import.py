"""Tests for the fulltext-retrieval-import subcommand."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from pypdf import PdfWriter

from laglitsynth.fulltext_retrieval.export import PDF_MANIFEST_FILENAME
from laglitsynth.fulltext_retrieval.import_ import import_pdfs, read_manifest, run
from laglitsynth.fulltext_retrieval.models import PdfSource
from laglitsynth.fulltext_retrieval.store import load_provenance, store_pdf_path
from laglitsynth.io import write_meta
from laglitsynth.manifest import RunManifest, StageEntry, load_manifest
from laglitsynth.models import RunMeta


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


class TestReadManifest:
    def test_round_trips_title_and_year_columns(self, tmp_path: Path) -> None:
        manifest = tmp_path / "pdf-manifest.csv"
        manifest.parent.mkdir(parents=True, exist_ok=True)
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

        entries = read_manifest(manifest)
        assert len(entries) == 1
        assert entries[0].title == "A Great Paper"
        assert entries[0].year == 2019

    def test_empty_title_and_year_cells_become_none(self, tmp_path: Path) -> None:
        manifest = tmp_path / "pdf-manifest.csv"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        with open(manifest, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["work_id", "stem", "doi", "title", "year", "expected_filename"]
            )
            writer.writerow(["https://openalex.org/W1", "W1", "", "", "", "W1.pdf"])

        entries = read_manifest(manifest)
        assert entries[0].title is None
        assert entries[0].year is None


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

    def test_first_page_text_doi_with_trailing_period(self, tmp_path: Path) -> None:
        # The DOI is only resolvable via first-page text, where it is rendered
        # in running prose with a trailing period (``10.1234/abc.``). The
        # trailing punctuation must be stripped so it matches the bare manifest
        # DOI ``10.1234/abc``. Metadata carries no DOI, so only the first-page
        # text path can resolve this work.
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        manifest = tmp_path / "pdf-manifest.csv"
        _write_manifest(
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


class TestManifestWiring:
    def test_omitted_manifest_resolves_from_bundle_dir_and_appends_entry(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "data"
        import_dir = tmp_path / "incoming"
        bundle_dir = data_dir / "pdfs" / "export"
        manifest_path = bundle_dir / PDF_MANIFEST_FILENAME
        _write_manifest(manifest_path, [("https://openalex.org/W1", "W1", "")])
        _make_pdf(import_dir / "W1.pdf")

        _seed_manifest(
            data_dir,
            [_stage_entry("fulltext-retrieval-export", str(bundle_dir))],
        )

        args = MagicMock()
        args.import_dir = import_dir
        args.manifest = None
        args.data_dir = data_dir
        args.source = "manual"
        args.overwrite = False

        run(args)

        assert store_pdf_path(data_dir, "W1").exists()
        assert (
            load_provenance(data_dir)["https://openalex.org/W1"].source
            == PdfSource.manual
        )

        manifest = load_manifest(data_dir)
        assert manifest is not None
        entry = manifest.stages[-1]
        assert entry.stage == "fulltext-retrieval-import"
        assert entry.inputs == {
            "import_dir": str(import_dir),
            "manifest": str(manifest_path),
        }
        assert entry.output == str(data_dir / "pdfs")
        assert entry.meta_path is None

    def test_explicit_manifest_flag_wins_over_the_recorded_bundle(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "data"
        import_dir = tmp_path / "incoming"

        recorded_bundle_dir = data_dir / "pdfs" / "export"
        _write_manifest(
            recorded_bundle_dir / PDF_MANIFEST_FILENAME,
            [("https://openalex.org/W-wrong", "W-wrong", "")],
        )
        _seed_manifest(
            data_dir,
            [_stage_entry("fulltext-retrieval-export", str(recorded_bundle_dir))],
        )

        explicit_manifest = tmp_path / "explicit-manifest.csv"
        _write_manifest(explicit_manifest, [("https://openalex.org/W1", "W1", "")])
        _make_pdf(import_dir / "W1.pdf")

        args = MagicMock()
        args.import_dir = import_dir
        args.manifest = explicit_manifest
        args.data_dir = data_dir
        args.source = "manual"
        args.overwrite = False

        run(args)

        assert store_pdf_path(data_dir, "W1").exists()
        manifest = load_manifest(data_dir)
        assert manifest is not None
        assert manifest.stages[-1].inputs["manifest"] == str(explicit_manifest)

    def test_no_manifest_and_omitted_flag_names_the_flag(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        args = MagicMock()
        args.import_dir = tmp_path / "incoming"
        args.manifest = None
        args.data_dir = data_dir
        args.source = "manual"
        args.overwrite = False

        try:
            run(args)
        except SystemExit as exc:
            assert "--manifest" in str(exc)
        else:
            raise AssertionError("expected SystemExit without a manifest")

    def test_manifest_naming_a_missing_bundle_file_raises_operator_visible_error(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "data"
        # The bundle directory is recorded, but pdf-manifest.csv never made it
        # to this disk (e.g. rsync brought data/manifest.json but not the
        # export bundle it points at).
        bundle_dir = data_dir / "pdfs" / "export"
        _seed_manifest(
            data_dir,
            [_stage_entry("fulltext-retrieval-export", str(bundle_dir))],
        )

        args = MagicMock()
        args.import_dir = tmp_path / "incoming"
        args.manifest = None
        args.data_dir = data_dir
        args.source = "manual"
        args.overwrite = False

        try:
            run(args)
        except SystemExit as exc:
            assert "does not exist here" in str(exc)
        else:
            raise AssertionError("expected SystemExit for a path absent on this disk")
