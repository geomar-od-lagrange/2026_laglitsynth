"""Tests for the fulltext-retrieval-import subcommand."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pypdf import PdfWriter

from laglitsynth.cli import main
from laglitsynth.fulltext_retrieval.import_ import _ImportSummary, import_pdfs, run
from laglitsynth.fulltext_retrieval.models import PdfSource
from laglitsynth.fulltext_retrieval.store import load_provenance, store_pdf_path
from laglitsynth.layout import PDF_MANIFEST_FILENAME, deduplicated_path, export_dir

from conftest import _make_work, _write_works_jsonl


def _write_catalogue(
    path: Path,
    rows: list[tuple[str, str, str]],
    titles: dict[str, str] | None = None,
) -> None:
    """rows: (work_id, stem, doi); an empty DOI means a DOI-less work.

    ``titles`` maps a stem to its title; other works keep ``_make_work``'s
    default, which is too short to take part in title matching.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    titles = titles or {}
    works = []
    for work_id, stem, doi in rows:
        assert work_id.endswith("/" + stem)
        if stem in titles:
            works.append(_make_work(work_id, title=titles[stem], doi=doi or None))
        else:
            works.append(_make_work(work_id, doi=doi or None))
    _write_works_jsonl(path, works)


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
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(
            catalogue, [("https://openalex.org/W1", "W1", "10.1234/abc")]
        )
        # Zotero-named file (item title), DOI only resolvable via embedded metadata.
        _make_pdf(import_dir / "Some Paper Title.pdf", doi_in_metadata="10.1234/abc")

        summary, still_missing = import_pdfs(
            import_dir, catalogue, data_dir, PdfSource.zotero_desktop, overwrite=False
        )

        assert summary.copied == 1
        assert still_missing == 0
        assert store_pdf_path(data_dir, "W1").exists()
        rec = load_provenance(data_dir)["https://openalex.org/W1"]
        assert rec.source == PdfSource.zotero_desktop
        assert rec.pdf_path == "pdfs/W1.pdf"
        assert rec.content_sha256 is not None

    def test_first_page_text_doi_with_trailing_period(self, tmp_path: Path) -> None:
        # The DOI is only resolvable via first-page text, where it is rendered
        # in running prose with a trailing period (``10.1234/abc.``). The
        # trailing punctuation must be stripped so it matches the bare catalogue
        # DOI ``10.1234/abc``. Metadata carries no DOI, so only the first-page
        # text path can resolve this work.
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(
            catalogue, [("https://openalex.org/W1", "W1", "10.1234/abc")]
        )
        _make_pdf(import_dir / "Some Paper Title.pdf")  # no embedded DOI metadata

        # pypdf cannot render extractable text from a blank page, so stand in a
        # reader whose metadata has no DOI and whose first page text carries the
        # DOI followed by a sentence-ending period.
        fake_page = MagicMock()
        fake_page.extract_text.return_value = (
            "Cite as: doi 10.1234/abc. Published 2024."
        )
        fake_page.get.return_value = None  # no link annotations
        fake_reader = MagicMock()
        fake_reader.metadata = None
        fake_reader.xmp_metadata = None
        fake_reader.pages = [fake_page]

        with patch(
            "laglitsynth.fulltext_retrieval.import_.PdfReader",
            return_value=fake_reader,
        ):
            summary, still_missing = import_pdfs(
                import_dir, catalogue, data_dir, PdfSource.zotero_desktop, overwrite=False
            )

        assert summary.copied == 1
        assert still_missing == 0
        assert load_provenance(data_dir)["https://openalex.org/W1"].stem == "W1"


class TestImportByFilenameStem:
    def test_filename_stem_match_manual(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(catalogue, [("https://openalex.org/W2", "W2", "")])
        _make_pdf(import_dir / "W2.pdf")  # named by stem, no DOI

        summary, still_missing = import_pdfs(
            import_dir, catalogue, data_dir, PdfSource.manual, overwrite=False
        )

        assert summary.copied == 1
        rec = load_provenance(data_dir)["https://openalex.org/W2"]
        assert rec.source == PdfSource.manual


class TestImportSidecar:
    def test_sidecar_json_maps_filename_to_doi(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(
            catalogue, [("https://openalex.org/W3", "W3", "10.9/zzz")]
        )
        # File named neither by stem nor with an embedded DOI; sidecar maps it.
        _make_pdf(import_dir / "weird-name.pdf")
        (import_dir / "map.json").write_text(
            json.dumps({"weird-name.pdf": "10.9/zzz"}), encoding="utf-8"
        )

        summary, _ = import_pdfs(
            import_dir, catalogue, data_dir, PdfSource.manual, overwrite=False
        )
        assert summary.copied == 1
        assert load_provenance(data_dir)["https://openalex.org/W3"].stem == "W3"


class TestImportSkips:
    def test_non_pdf_and_unmatched_skipped(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(catalogue, [("https://openalex.org/W1", "W1", "10.1/a")])
        import_dir.mkdir()
        # Not a PDF (wrong magic bytes) but .pdf extension.
        (import_dir / "fake.pdf").write_bytes(b"<html>nope</html>")
        # A real PDF that matches nothing in the catalogue.
        _make_pdf(import_dir / "Unknown.pdf")

        summary, still_missing = import_pdfs(
            import_dir, catalogue, data_dir, PdfSource.manual, overwrite=False
        )

        assert summary.copied == 0
        assert summary.skipped_not_pdf == ["fake.pdf"]
        assert summary.skipped_unmatched == ["Unknown.pdf"]
        assert still_missing == 1


class TestImportDedup:
    def test_identical_reimport_is_noop(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(catalogue, [("https://openalex.org/W2", "W2", "")])
        _make_pdf(import_dir / "W2.pdf")

        import_pdfs(import_dir, catalogue, data_dir, PdfSource.manual, overwrite=False)
        summary, _ = import_pdfs(
            import_dir, catalogue, data_dir, PdfSource.manual, overwrite=False
        )
        assert summary.copied == 0
        assert summary.skipped_duplicate == ["W2.pdf"]

    def test_differing_pdf_skipped_without_overwrite(self, tmp_path: Path) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(catalogue, [("https://openalex.org/W2", "W2", "")])
        _make_pdf(import_dir / "W2.pdf")
        import_pdfs(import_dir, catalogue, data_dir, PdfSource.manual, overwrite=False)

        # A different PDF for the same stem.
        _make_pdf(import_dir / "W2.pdf", doi_in_metadata="10.5/changed")
        first_sha = load_provenance(data_dir)["https://openalex.org/W2"].content_sha256

        summary, _ = import_pdfs(
            import_dir, catalogue, data_dir, PdfSource.manual, overwrite=False
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
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(catalogue, [("https://openalex.org/W2", "W2", "")])
        _make_pdf(import_dir / "W2.pdf")
        import_pdfs(import_dir, catalogue, data_dir, PdfSource.manual, overwrite=False)
        first_sha = load_provenance(data_dir)["https://openalex.org/W2"].content_sha256

        _make_pdf(import_dir / "W2.pdf", doi_in_metadata="10.5/changed")
        summary, _ = import_pdfs(
            import_dir, catalogue, data_dir, PdfSource.manual, overwrite=True
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
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(catalogue, [("https://openalex.org/W1", "W1", "")])
        _make_pdf(import_dir / "W1.pdf")

        args = MagicMock()
        args.import_dir = import_dir
        args.catalogue = catalogue
        args.data_dir = data_dir
        args.source = "manual"
        args.overwrite = False

        run(args)
        assert store_pdf_path(data_dir, "W1").exists()
        assert (
            load_provenance(data_dir)["https://openalex.org/W1"].source
            == PdfSource.manual
        )


class TestWholeCatalogue:
    """Import needs no export: any PDF of a catalogue work lands."""

    def test_work_in_no_export_matched_by_embedded_doi(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        _write_catalogue(
            deduplicated_path(data_dir),
            [
                ("https://openalex.org/W1", "W1", "10.1234/listed"),
                ("https://openalex.org/W2", "W2", "10.1234/never-exported"),
            ],
        )
        # The only export listed W1; the returned PDF is W2's.
        export_dir(data_dir).mkdir(parents=True)
        (export_dir(data_dir) / PDF_MANIFEST_FILENAME).write_text(
            "work_id,stem,doi,title,year,expected_filename\n"
            "https://openalex.org/W1,W1,10.1234/listed,,,W1.pdf\n",
            encoding="utf-8",
        )
        _make_pdf(tmp_path / "incoming" / "paper.pdf", doi_in_metadata="10.1234/never-exported")

        main(["fulltext-retrieval-import", "--import-dir", str(tmp_path / "incoming"),
              "--data-dir", str(data_dir), "--source", "browser"])

        rec = load_provenance(data_dir)["https://openalex.org/W2"]
        assert rec.stem == "W2"
        assert rec.doi == "10.1234/never-exported"
        assert store_pdf_path(data_dir, "W2").exists()

    def test_url_form_catalogue_doi_matches_and_is_stored_bare(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "data"
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(
            catalogue, [("https://openalex.org/W1", "W1", "https://doi.org/10.1234/ABC")]
        )
        _make_pdf(tmp_path / "incoming" / "paper.pdf", doi_in_metadata="10.1234/abc")

        summary, _ = import_pdfs(
            tmp_path / "incoming", catalogue, data_dir, PdfSource.browser, overwrite=False
        )

        assert summary.copied == 1
        assert load_provenance(data_dir)["https://openalex.org/W1"].doi == "10.1234/ABC"

    def test_bare_stem_pdf_lands_without_an_export(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        _write_catalogue(
            deduplicated_path(data_dir), [("https://openalex.org/W7", "W7", "")]
        )
        _make_pdf(tmp_path / "incoming" / "W7.pdf")

        main(["fulltext-retrieval-import", "--import-dir", str(tmp_path / "incoming"),
              "--data-dir", str(data_dir), "--source", "manual"])

        assert not export_dir(data_dir).exists()
        assert store_pdf_path(data_dir, "W7").exists()
        assert load_provenance(data_dir)["https://openalex.org/W7"].doi is None


class TestSourceFlag:
    @pytest.mark.parametrize(
        ("flag", "expected"),
        [
            ("zotero-desktop", PdfSource.zotero_desktop),
            ("browser", PdfSource.browser),
            ("institutional", PdfSource.institutional),
            ("manual", PdfSource.manual),
        ],
    )
    def test_source_recorded(
        self, tmp_path: Path, flag: str, expected: PdfSource
    ) -> None:
        data_dir = tmp_path / "data"
        _write_catalogue(
            deduplicated_path(data_dir), [("https://openalex.org/W1", "W1", "")]
        )
        _make_pdf(tmp_path / "incoming" / "W1.pdf")

        main(["fulltext-retrieval-import", "--import-dir", str(tmp_path / "incoming"),
              "--data-dir", str(data_dir), "--source", flag])

        assert load_provenance(data_dir)["https://openalex.org/W1"].source == expected

    def test_zotero_import_rejected_by_argparse(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["fulltext-retrieval-import", "--import-dir", str(tmp_path),
                  "--source", "zotero-import"])
        assert exc_info.value.code == 2
        assert "invalid choice: 'zotero-import'" in capsys.readouterr().err

    def test_pdf_manifest_flag_is_gone(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["fulltext-retrieval-import", "--import-dir", str(tmp_path),
                  "--source", "manual", "--pdf-manifest", "x.csv"])
        assert exc_info.value.code == 2
        assert "--pdf-manifest" in capsys.readouterr().err


class TestConventionalPaths:
    def _args(self, tmp_path: Path, data_dir: Path) -> MagicMock:
        args = MagicMock()
        args.import_dir = tmp_path / "incoming"
        args.catalogue = None
        args.data_dir = data_dir
        args.source = "manual"
        args.overwrite = False
        return args

    def test_omitted_flag_resolves_to_conventional_path(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        _write_catalogue(
            deduplicated_path(data_dir), [("https://openalex.org/W1", "W1", "")]
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
        _write_catalogue(
            deduplicated_path(data_dir),
            [("https://openalex.org/W-wrong", "W-wrong", "")],
        )
        explicit = tmp_path / "explicit-catalogue.jsonl"
        _write_catalogue(explicit, [("https://openalex.org/W1", "W1", "")])
        _make_pdf(tmp_path / "incoming" / "W1.pdf")

        args = self._args(tmp_path, data_dir)
        args.catalogue = explicit
        run(args)

        assert set(load_provenance(data_dir)) == {"https://openalex.org/W1"}

    def test_missing_catalogue_names_upstream_and_flag(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            run(self._args(tmp_path, tmp_path / "data"))
        msg = str(exc_info.value)
        assert "run catalogue-dedup first" in msg
        assert "--catalogue" in msg


class TestDoiCandidateSet:
    """Membership in the catalogue decides which extracted DOI wins, not order."""

    def test_journal_doi_in_metadata_does_not_beat_the_article_doi_in_text(
        self, tmp_path: Path
    ) -> None:
        # The AGU/Wiley shape measured against real PDFs: the Info dictionary
        # carries the journal's ISSN-DOI, which is not in the catalogue, while
        # the article's own DOI is printed on page 1.
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(catalogue, [("https://openalex.org/W1", "W1", "10.1029/2024MS004848")])
        _make_pdf(import_dir / "download (3).pdf", doi_in_metadata="10.1002/(ISSN)1942-2466")

        fake_page = MagicMock()
        fake_page.extract_text.return_value = "Journal of Advances\n10.1029/2024MS004848\n"
        fake_page.get.return_value = None
        fake_reader = MagicMock()
        fake_reader.metadata = {"/Subject": "doi:10.1002/(ISSN)1942-2466"}
        fake_reader.xmp_metadata = None
        fake_reader.pages = [fake_page]

        with patch(
            "laglitsynth.fulltext_retrieval.import_.PdfReader", return_value=fake_reader
        ):
            summary, still_missing = import_pdfs(
                import_dir, catalogue, data_dir, PdfSource.manual, overwrite=False
            )

        assert summary.copied == 1
        assert still_missing == 0
        assert load_provenance(data_dir)["https://openalex.org/W1"].stem == "W1"

    def test_a_metadata_doi_wins_when_both_candidates_are_in_the_catalogue(
        self, tmp_path: Path
    ) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(
            catalogue,
            [
                ("https://openalex.org/W1", "W1", "10.1234/from-metadata"),
                ("https://openalex.org/W2", "W2", "10.1234/from-text"),
            ],
        )
        _make_pdf(import_dir / "anything.pdf")

        fake_page = MagicMock()
        fake_page.extract_text.return_value = "see also 10.1234/from-text"
        fake_page.get.return_value = None
        fake_reader = MagicMock()
        fake_reader.metadata = {"/Subject": "doi:10.1234/from-metadata"}
        fake_reader.xmp_metadata = None
        fake_reader.pages = [fake_page]

        with patch(
            "laglitsynth.fulltext_retrieval.import_.PdfReader", return_value=fake_reader
        ):
            import_pdfs(import_dir, catalogue, data_dir, PdfSource.manual, overwrite=False)

        assert load_provenance(data_dir)["https://openalex.org/W1"].stem == "W1"

    def test_no_candidate_in_the_catalogue_is_still_skipped_not_guessed(
        self, tmp_path: Path
    ) -> None:
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(catalogue, [("https://openalex.org/W1", "W1", "10.1234/wanted")])
        _make_pdf(import_dir / "unrelated.pdf", doi_in_metadata="10.9999/something-else")

        summary, still_missing = import_pdfs(
            import_dir, catalogue, data_dir, PdfSource.manual, overwrite=False
        )

        assert summary.copied == 0
        assert summary.skipped_unmatched == ["unrelated.pdf"]
        assert still_missing == 1


class TestAmsStyleDois:
    def test_a_doi_with_angle_brackets_matches_the_catalogue(self, tmp_path: Path) -> None:
        # 10.1175/1520-0485(1997)027<1038:KOTPEU>2.0.CO;2 -- the Crossref class
        # excludes angle brackets, which truncated the DOI before it could match.
        ams_doi = "10.1175/1520-0485(1997)027<1038:KOTPEU>2.0.CO;2"
        import_dir = tmp_path / "incoming"
        data_dir = tmp_path / "data"
        catalogue = tmp_path / "catalogue.jsonl"
        _write_catalogue(catalogue, [("https://openalex.org/W1", "W1", ams_doi)])
        _make_pdf(import_dir / "scan.pdf", doi_in_metadata=ams_doi)

        summary, still_missing = import_pdfs(
            import_dir, catalogue, data_dir, PdfSource.manual, overwrite=False
        )

        assert summary.copied == 1
        assert still_missing == 0
        assert load_provenance(data_dir)["https://openalex.org/W1"].stem == "W1"


def _fake_reader(
    *,
    metadata: dict[str, str] | None = None,
    xmp_text: str | None = None,
    page_texts: list[str] | None = None,
    annotation_uris: list[list[str]] | None = None,
) -> MagicMock:
    """Stand in a pypdf reader with the given sources populated.

    ``annotation_uris`` is one list of link URIs per page; ``page_texts`` is
    the extractable text per page.
    """
    page_texts = page_texts or [""]
    annotation_uris = annotation_uris or [[] for _ in page_texts]
    pages = []
    for text, uris in zip(page_texts, annotation_uris, strict=True):
        page = MagicMock()
        page.extract_text.return_value = text
        annots = []
        for uri in uris:
            annotation = MagicMock()
            annotation.get_object.return_value = {"/A": {"/URI": uri}}
            annots.append(annotation)
        page.get.return_value = annots or None
        pages.append(page)
    reader = MagicMock()
    reader.metadata = metadata
    if xmp_text is None:
        reader.xmp_metadata = None
    else:
        reader.xmp_metadata.stream.get_data.return_value = xmp_text.encode()
    reader.pages = pages
    return reader


def _import_with(
    reader: MagicMock,
    tmp_path: Path,
    rows: list[tuple[str, str, str]],
    *,
    titles: dict[str, str] | None = None,
    filename: str = "anything.pdf",
) -> tuple[_ImportSummary, int, Path]:
    import_dir = tmp_path / "incoming"
    data_dir = tmp_path / "data"
    catalogue = tmp_path / "catalogue.jsonl"
    _write_catalogue(catalogue, rows, titles)
    _make_pdf(import_dir / filename)
    with patch("laglitsynth.fulltext_retrieval.import_.PdfReader", return_value=reader):
        summary, still_missing = import_pdfs(
            import_dir, catalogue, data_dir, PdfSource.manual, overwrite=False
        )
    return summary, still_missing, data_dir


class TestMetadataSources:
    ROWS = [("https://openalex.org/W1", "W1", "10.1234/only-here")]

    def test_a_pdf_carrying_only_an_xmp_doi_resolves(self, tmp_path: Path) -> None:
        reader = _fake_reader(
            xmp_text='<rdf:Description prism:doi="10.1234/only-here"/>'
        )
        summary, still_missing, data_dir = _import_with(reader, tmp_path, self.ROWS)
        assert summary.copied == 1
        assert still_missing == 0
        assert load_provenance(data_dir)["https://openalex.org/W1"].stem == "W1"

    def test_a_pdf_carrying_only_a_link_annotation_doi_resolves(
        self, tmp_path: Path
    ) -> None:
        reader = _fake_reader(annotation_uris=[["https://doi.org/10.1234/only-here"]])
        summary, _, data_dir = _import_with(reader, tmp_path, self.ROWS)
        assert summary.copied == 1
        assert load_provenance(data_dir)["https://openalex.org/W1"].stem == "W1"

    def test_a_link_annotation_on_the_second_page_is_read(self, tmp_path: Path) -> None:
        # A cover sheet ahead of the title page: the first page has nothing.
        reader = _fake_reader(
            page_texts=["Cover sheet", "Title page"],
            annotation_uris=[[], ["https://doi.org/10.1234/only-here"]],
        )
        summary, _, _ = _import_with(reader, tmp_path, self.ROWS)
        assert summary.copied == 1


class TestTextTierAmbiguity:
    ROWS = [
        ("https://openalex.org/W1", "W1", "10.1234/original"),
        ("https://openalex.org/W2", "W2", "10.1234/correction"),
    ]

    def test_two_catalogue_dois_on_page_one_are_not_guessed_between(
        self, tmp_path: Path
    ) -> None:
        # A correction notice names itself and the paper it corrects.
        reader = _fake_reader(
            page_texts=["Correction to: 10.1234/original\nThis notice: 10.1234/correction"]
        )
        summary, still_missing, _ = _import_with(reader, tmp_path, self.ROWS)
        assert summary.copied == 0
        assert summary.skipped_unmatched == ["anything.pdf"]
        assert still_missing == 2

    def test_a_metadata_doi_settles_what_page_text_leaves_ambiguous(
        self, tmp_path: Path
    ) -> None:
        reader = _fake_reader(
            metadata={"/Subject": "doi:10.1234/correction"},
            page_texts=["Correction to: 10.1234/original\nThis notice: 10.1234/correction"],
        )
        summary, _, data_dir = _import_with(reader, tmp_path, self.ROWS)
        assert summary.copied == 1
        assert load_provenance(data_dir)["https://openalex.org/W2"].stem == "W2"


class TestTitleTier:
    """Title matching runs only after every DOI, filename and sidecar route."""

    TITLE = "Deep Lagrangian connectivity in the global ocean inferred from Argo floats"
    OTHER = "Seasonal variability of surface drifter dispersion in the Gulf of Mexico"
    ROWS = [
        ("https://openalex.org/W1", "W1", ""),
        ("https://openalex.org/W2", "W2", "10.1234/other"),
    ]
    TITLES = {"W1": TITLE, "W2": OTHER}

    def test_a_title_broken_over_lines_on_page_one_files_a_doi_less_pdf(
        self, tmp_path: Path
    ) -> None:
        page = (
            "Journal of Physical Oceanography 52 (2022)\n"
            "Deep Lagrangian Connectivity in the Global\n"
            "Ocean Inferred from Argo Floats\n"
            "Ryan Abernathey, Christopher Bladwell\n"
            "Lamont-Doherty Earth Observatory, Palisades, New York\n"
        )
        reader = _fake_reader(page_texts=[page])
        summary, _, data_dir = _import_with(
            reader, tmp_path, self.ROWS, titles=self.TITLES
        )
        assert summary.copied == 1
        assert load_provenance(data_dir)["https://openalex.org/W1"].stem == "W1"

    def test_a_metadata_title_alone_files_the_pdf(self, tmp_path: Path) -> None:
        reader = _fake_reader(metadata={"/Title": self.TITLE.upper()})
        summary, _, data_dir = _import_with(
            reader, tmp_path, self.ROWS, titles=self.TITLES
        )
        assert summary.copied == 1
        assert "https://openalex.org/W1" in load_provenance(data_dir)

    def test_a_doi_beats_the_title_of_another_work(self, tmp_path: Path) -> None:
        reader = _fake_reader(
            metadata={"/Subject": "doi:10.1234/other"}, page_texts=[self.TITLE]
        )
        summary, _, data_dir = _import_with(
            reader, tmp_path, self.ROWS, titles=self.TITLES
        )
        assert summary.copied == 1
        assert list(load_provenance(data_dir)) == ["https://openalex.org/W2"]

    def test_a_stem_filename_beats_the_title_of_another_work(
        self, tmp_path: Path
    ) -> None:
        reader = _fake_reader(page_texts=[self.TITLE])
        summary, _, data_dir = _import_with(
            reader, tmp_path, self.ROWS, titles=self.TITLES, filename="W2.pdf"
        )
        assert summary.copied == 1
        assert list(load_provenance(data_dir)) == ["https://openalex.org/W2"]

    def test_two_works_with_the_same_title_are_not_guessed_between(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        reader = _fake_reader(page_texts=[self.TITLE])
        summary, still_missing, _ = _import_with(
            reader, tmp_path, self.ROWS, titles={"W1": self.TITLE, "W2": self.TITLE}
        )
        assert summary.copied == 0
        assert summary.skipped_unmatched == ["anything.pdf"]
        assert still_missing == 2
        assert "W1, W2" in capsys.readouterr().err

    def test_a_near_same_title_is_refused(self, tmp_path: Path) -> None:
        # A preprint and its article, retitled in a few words.
        titles = {
            "W1": "Lagrangian transport in a microtidal coastal area: "
            "the Bay of Palma, Mallorca Island, Spain",
            "W2": "Lagrangian transport in a microtidal coastal area: "
            "the Bay of Palma, island of Mallorca, Spain",
        }
        reader = _fake_reader(page_texts=[titles["W1"]])
        summary, _, _ = _import_with(reader, tmp_path, self.ROWS, titles=titles)
        assert summary.copied == 0

    def test_a_short_title_met_in_an_affiliation_is_not_accepted(
        self, tmp_path: Path
    ) -> None:
        titles = {"W1": "Geophysical fluid dynamics", "W2": self.OTHER}
        page = "Reviewed by: John Doe,\nGeophysical Fluid Dynamics\nLaboratory"
        reader = _fake_reader(page_texts=[page])
        summary, _, _ = _import_with(reader, tmp_path, self.ROWS, titles=titles)
        assert summary.copied == 0
        assert summary.skipped_unmatched == ["anything.pdf"]
