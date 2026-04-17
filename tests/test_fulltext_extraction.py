"""Tests for the fulltext_extraction stage."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from lxml import etree

from laglitsynth.fulltext_extraction.extract import parse_tei, run
from laglitsynth.fulltext_extraction.models import TextSection

TEI_NS = "http://www.tei-c.org/ns/1.0"


def _wrap_tei_body(body_content: str) -> bytes:
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{TEI_NS}">
  <text>
    <body>
      {body_content}
    </body>
  </text>
</TEI>""".encode()


class TestParseTeiNormalSections:
    def test_multiple_sections(self) -> None:
        xml = _wrap_tei_body("""
            <div xmlns="{ns}">
              <head>Introduction</head>
              <p>First paragraph.</p>
              <p>Second paragraph.</p>
            </div>
            <div xmlns="{ns}">
              <head>Methods</head>
              <p>We used Parcels.</p>
            </div>
        """.format(ns=TEI_NS))
        sections = parse_tei(xml)
        assert len(sections) == 2
        assert sections[0].title == "Introduction"
        assert "First paragraph." in sections[0].text
        assert "Second paragraph." in sections[0].text
        assert sections[1].title == "Methods"
        assert "We used Parcels." in sections[1].text


class TestParseTeiMissingHead:
    def test_no_head_element(self) -> None:
        xml = _wrap_tei_body("""
            <div xmlns="{ns}">
              <p>Some text without heading.</p>
            </div>
        """.format(ns=TEI_NS))
        sections = parse_tei(xml)
        assert len(sections) == 1
        assert sections[0].title == "Untitled section"
        assert "Some text without heading." in sections[0].text


class TestParseTeiNoDivsFallback:
    def test_body_with_only_paragraphs(self) -> None:
        xml = _wrap_tei_body("""
            <p xmlns="{ns}">Just a paragraph in body.</p>
        """.format(ns=TEI_NS))
        sections = parse_tei(xml)
        assert len(sections) == 1
        assert sections[0].title == "Body"
        assert "Just a paragraph" in sections[0].text


class TestParseTeiEmptyBody:
    def test_empty_body(self) -> None:
        xml = _wrap_tei_body("")
        sections = parse_tei(xml)
        assert sections == []


class TestParseTeiNoBody:
    def test_no_body_element(self) -> None:
        xml = f"""\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{TEI_NS}">
  <text></text>
</TEI>""".encode()
        sections = parse_tei(xml)
        assert sections == []


class TestParseTeFigureStripped:
    def test_figures_removed(self) -> None:
        xml = _wrap_tei_body("""
            <div xmlns="{ns}">
              <head>Results</head>
              <p>Real text here.</p>
              <figure xmlns="{ns}"><head>Figure 1</head><p>Caption noise.</p></figure>
            </div>
        """.format(ns=TEI_NS))
        sections = parse_tei(xml)
        assert len(sections) == 1
        assert "Real text" in sections[0].text
        assert "Caption noise" not in sections[0].text
        assert "Figure 1" not in sections[0].text


class TestParseTeiNestedInline:
    def test_ref_and_hi_elements(self) -> None:
        xml = _wrap_tei_body("""
            <div xmlns="{ns}">
              <head>Discussion</head>
              <p>See <ref>Smith et al. (2020)</ref> for <hi rend="italic">details</hi>.</p>
            </div>
        """.format(ns=TEI_NS))
        sections = parse_tei(xml)
        assert len(sections) == 1
        assert "Smith et al. (2020)" in sections[0].text
        assert "details" in sections[0].text


class TestParseTeiEmptyDivSkipped:
    def test_div_with_only_head(self) -> None:
        xml = _wrap_tei_body("""
            <div xmlns="{ns}">
              <head>Empty Section</head>
            </div>
            <div xmlns="{ns}">
              <head>Real Section</head>
              <p>Content.</p>
            </div>
        """.format(ns=TEI_NS))
        sections = parse_tei(xml)
        assert len(sections) == 1
        assert sections[0].title == "Real Section"


class TestParseTeiMalformedXml:
    def test_rejects_malformed_xml(self) -> None:
        with pytest.raises(etree.XMLSyntaxError):
            parse_tei(b"not xml at all")

    def test_rejects_truncated_xml(self) -> None:
        with pytest.raises(etree.XMLSyntaxError):
            parse_tei(b"<TEI><text><body>")


class TestParseTeiExternalEntity:
    def test_external_entity_not_resolved(self) -> None:
        # Feed TEI with an external SYSTEM entity reference.
        # With resolve_entities=False, lxml should either raise XMLSyntaxError
        # or leave the entity unexpanded — it must NOT attempt filesystem/network access.
        xml_bytes = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<!DOCTYPE TEI [<!ENTITY ext SYSTEM "/nonexistent/path">]>'
            b'<TEI xmlns="http://www.tei-c.org/ns/1.0">'
            b"<text><body>&ext;</body></text>"
            b"</TEI>"
        )
        # lxml with resolve_entities=False raises XMLSyntaxError for unresolvable
        # external entities — either that or it parses without resolving them.
        # Pin whichever behaviour lxml actually exhibits (external entity not fetched).
        try:
            sections = parse_tei(xml_bytes)
            # If it parsed, the entity must not have been resolved to real content.
            # The body will be empty or whitespace-only.
            assert sections == [] or all(s.text.strip() == "" for s in sections)
        except etree.XMLSyntaxError:
            pass  # Also acceptable — entity reference rejected.


class TestInvalidStemSkipped:
    def test_invalid_stem_skipped(self, tmp_path: Path) -> None:
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        output_dir = tmp_path / "out"

        # Place a stray file with an invalid stem and a valid one.
        (pdf_dir / "sample.pdf").write_bytes(b"%PDF-1.4 fake")
        (pdf_dir / "W1234.pdf").write_bytes(b"%PDF-1.4 fake")

        args = MagicMock()
        args.pdf_dir = pdf_dir
        args.output_dir = output_dir
        args.grobid_url = "http://localhost:8070"
        args.timeout = 120.0
        args.skip_existing = False

        # Minimal valid TEI XML that GROBID would return.
        fake_tei = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<TEI xmlns="http://www.tei-c.org/ns/1.0">'
            b"<text><body>"
            b'<div><head>Intro</head><p>Content.</p></div>'
            b"</body></text></TEI>"
        )

        preflight_ok = httpx.Response(
            200,
            text="alive",
            request=httpx.Request("GET", "http://localhost:8070/api/isalive"),
        )
        version_ok = httpx.Response(
            200,
            text="0.8.0",
            request=httpx.Request("GET", "http://localhost:8070/api/version"),
        )

        preflight_client_mock = MagicMock(spec=httpx.Client)
        preflight_client_mock.get.side_effect = [preflight_ok, version_ok]

        paper_client_mock = MagicMock(spec=httpx.Client)
        paper_resp = httpx.Response(
            200,
            content=fake_tei,
            request=httpx.Request(
                "POST", "http://localhost:8070/api/processFulltextDocument"
            ),
        )
        paper_client_mock.post.return_value = paper_resp

        def _make_client(timeout: float) -> MagicMock:
            if timeout == 5.0:
                return preflight_client_mock
            return paper_client_mock

        with patch(
            "laglitsynth.fulltext_extraction.extract.httpx.Client",
            side_effect=_make_client,
        ):
            run(args)

        extraction_path = output_dir / "extraction.jsonl"
        lines = [l for l in extraction_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 1

        import json
        record = json.loads(lines[0])
        assert record["work_id"] == "https://openalex.org/W1234"

        meta_path = output_dir / "extraction-meta.json"
        meta = json.loads(meta_path.read_text())
        assert meta["invalid_stem_count"] == 1
        assert meta["extracted_count"] == 1
