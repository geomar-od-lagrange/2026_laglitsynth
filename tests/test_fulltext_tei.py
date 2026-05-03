"""Tests for the TEI wrapper accessors (``TeiDocument``)."""

from __future__ import annotations

from pathlib import Path

import pytest
from lxml import etree

from laglitsynth.fulltext_extraction.models import ExtractedDocument
from laglitsynth.fulltext_extraction.tei import (
    BibReference,
    Citation,
    Figure,
    Section,
    TeiDocument,
    flatten_sections,
)

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


def _wrap_full_tei(body_content: str, back_content: str = "") -> bytes:
    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<TEI xmlns="{TEI_NS}">
  <text>
    <body>
      {body_content}
    </body>
    <back>
      {back_content}
    </back>
  </text>
</TEI>""".encode()


class TestTeiDocumentSections:
    def test_multiple_sections(self) -> None:
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}">
              <head>Introduction</head>
              <p>First paragraph.</p>
              <p>Second paragraph.</p>
            </div>
            <div xmlns="{TEI_NS}">
              <head>Methods</head>
              <p>We used Parcels.</p>
            </div>
        """)
        secs = TeiDocument.from_bytes(xml).sections()
        assert len(secs) == 2
        assert secs[0].title == "Introduction"
        assert secs[0].paragraphs == ["First paragraph.", "Second paragraph."]
        assert secs[1].title == "Methods"
        assert secs[1].paragraphs == ["We used Parcels."]

    def test_missing_head(self) -> None:
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}">
              <p>Some text without heading.</p>
            </div>
        """)
        secs = TeiDocument.from_bytes(xml).sections()
        assert len(secs) == 1
        assert secs[0].title is None
        assert secs[0].paragraphs == ["Some text without heading."]

    def test_no_divs(self) -> None:
        xml = _wrap_tei_body(f"""
            <p xmlns="{TEI_NS}">Just a paragraph in body.</p>
        """)
        # No top-level <div> — sections() returns empty; consumers read TEI
        # text via figures/citations/bibliography or ignore it.
        secs = TeiDocument.from_bytes(xml).sections()
        assert secs == []

    def test_empty_body(self) -> None:
        xml = _wrap_tei_body("")
        assert TeiDocument.from_bytes(xml).sections() == []

    def test_figure_stripped(self) -> None:
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}">
              <head>Results</head>
              <p>Real text here.</p>
              <figure xmlns="{TEI_NS}"><head>Figure 1</head><p>Caption noise.</p></figure>
            </div>
        """)
        secs = TeiDocument.from_bytes(xml).sections()
        assert len(secs) == 1
        assert secs[0].paragraphs == ["Real text here."]

    def test_recursive_nested_divs(self) -> None:
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}" xml:id="s1">
              <head>Methods</head>
              <p>Top-level methods text.</p>
              <div xmlns="{TEI_NS}" xml:id="s1-1">
                <head>Sub</head>
                <p>Sub methods text.</p>
              </div>
            </div>
        """)
        secs = TeiDocument.from_bytes(xml).sections()
        assert len(secs) == 1
        top = secs[0]
        assert top.id == "s1"
        assert top.title == "Methods"
        assert top.paragraphs == ["Top-level methods text."]
        assert len(top.children) == 1
        child = top.children[0]
        assert child.id == "s1-1"
        assert child.title == "Sub"
        assert child.paragraphs == ["Sub methods text."]
        assert child.children == []

    def test_sections_preserves_figures_for_later_access(self) -> None:
        # Asserts the figure-stripping in sections() does not mutate the
        # cached tree; figures() still sees the figure on the same instance.
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}">
              <head>Results</head>
              <p>Text.</p>
              <figure xmlns="{TEI_NS}" xml:id="fig_0"><label>1</label><figDesc>A caption.</figDesc></figure>
            </div>
        """)
        doc = TeiDocument.from_bytes(xml)
        _ = doc.sections()
        figs = doc.figures()
        assert len(figs) == 1
        assert figs[0].id == "fig_0"


class TestTeiDocumentFigures:
    def test_figure_with_label_and_caption(self) -> None:
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}">
              <head>Results</head>
              <p>Text.</p>
              <figure xmlns="{TEI_NS}" xml:id="fig_0"><label>1</label><figDesc>Particle paths.</figDesc></figure>
            </div>
        """)
        figs = TeiDocument.from_bytes(xml).figures()
        assert figs == [
            Figure(id="fig_0", label="1", caption="Particle paths.")
        ]

    def test_figure_without_label(self) -> None:
        xml = _wrap_tei_body(f"""
            <figure xmlns="{TEI_NS}"><figDesc>Just a caption.</figDesc></figure>
        """)
        figs = TeiDocument.from_bytes(xml).figures()
        assert len(figs) == 1
        assert figs[0].label is None
        assert figs[0].caption == "Just a caption."
        assert figs[0].id is None


class TestTeiDocumentCitations:
    def test_inline_bibr_reference(self) -> None:
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}">
              <head>Discussion</head>
              <p>See <ref type="bibr" target="#b3">Smith 2020</ref> for details.</p>
            </div>
        """)
        cites = TeiDocument.from_bytes(xml).citations()
        assert cites == [Citation(target_id="b3", text="Smith 2020")]

    def test_citation_without_target(self) -> None:
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}">
              <p>See <ref type="bibr">Smith 2020</ref>.</p>
            </div>
        """)
        cites = TeiDocument.from_bytes(xml).citations()
        assert cites == [Citation(target_id=None, text="Smith 2020")]

    def test_non_bibr_refs_ignored(self) -> None:
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}">
              <p>See <ref type="figure" target="#fig_0">Fig. 1</ref>.</p>
            </div>
        """)
        assert TeiDocument.from_bytes(xml).citations() == []


class TestTeiDocumentBibliography:
    def test_full_bib_struct(self) -> None:
        back = f"""
            <div xmlns="{TEI_NS}">
              <listBibl>
                <biblStruct xml:id="b3">
                  <analytic>
                    <title level="a" type="main">A paper about particles</title>
                    <author>
                      <persName><forename type="first">John</forename><surname>Smith</surname></persName>
                    </author>
                    <author>
                      <persName><forename type="first">Alice</forename><forename type="middle">B</forename><surname>Jones</surname></persName>
                    </author>
                    <idno type="DOI">10.1234/example</idno>
                  </analytic>
                  <monogr>
                    <title level="j">J. Example</title>
                    <imprint>
                      <date type="published" when="2020">2020</date>
                    </imprint>
                  </monogr>
                </biblStruct>
              </listBibl>
            </div>
        """
        xml = _wrap_full_tei("", back)
        bibs = TeiDocument.from_bytes(xml).bibliography()
        assert len(bibs) == 1
        bib = bibs[0]
        assert bib.id == "b3"
        assert bib.authors == ["Smith, J.", "Jones, A. B."]
        assert bib.title == "A paper about particles"
        assert bib.year == "2020"
        assert bib.doi == "10.1234/example"
        assert "A paper about particles" in bib.raw

    def test_author_without_surname_dropped(self) -> None:
        back = f"""
            <div xmlns="{TEI_NS}">
              <listBibl>
                <biblStruct xml:id="b1">
                  <analytic>
                    <title level="a" type="main">T</title>
                    <author>
                      <persName><forename type="first">Anonymous</forename></persName>
                    </author>
                    <author>
                      <persName><surname>Smith</surname></persName>
                    </author>
                  </analytic>
                </biblStruct>
              </listBibl>
            </div>
        """
        xml = _wrap_full_tei("", back)
        bibs = TeiDocument.from_bytes(xml).bibliography()
        assert bibs[0].authors == ["Smith"]

    def test_year_string_kept_verbatim(self) -> None:
        # No @when attribute; year comes from the date element's text.
        back = f"""
            <div xmlns="{TEI_NS}">
              <listBibl>
                <biblStruct>
                  <monogr>
                    <title level="j">J. Example</title>
                    <imprint>
                      <date type="published">2021a</date>
                    </imprint>
                  </monogr>
                </biblStruct>
              </listBibl>
            </div>
        """
        xml = _wrap_full_tei("", back)
        bibs = TeiDocument.from_bytes(xml).bibliography()
        assert bibs[0].year == "2021a"


class TestTeiDocumentFailure:
    def test_missing_file_raises_at_construction(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist.tei.xml"
        with pytest.raises(FileNotFoundError):
            TeiDocument(missing)

    def test_malformed_xml_raises_lazily(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.tei.xml"
        path.write_bytes(b"not xml at all")
        # Construction succeeds — file exists.
        doc = TeiDocument(path)
        with pytest.raises(etree.XMLSyntaxError):
            doc.sections()

    def test_from_bytes_malformed_raises_on_first_accessor(self) -> None:
        doc = TeiDocument.from_bytes(b"not xml")
        with pytest.raises(etree.XMLSyntaxError):
            doc.sections()

    def test_external_entity_not_resolved(self) -> None:
        # Hardened parser must not fetch external entities.
        xml_bytes = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<!DOCTYPE TEI [<!ENTITY ext SYSTEM "/nonexistent/path">]>'
            b'<TEI xmlns="http://www.tei-c.org/ns/1.0">'
            b"<text><body>&ext;</body></text>"
            b"</TEI>"
        )
        doc = TeiDocument.from_bytes(xml_bytes)
        try:
            secs = doc.sections()
            # Either parsed without resolving the entity (empty sections),
            # or raised XMLSyntaxError — both acceptable.
            assert secs == []
        except etree.XMLSyntaxError:
            pass


class TestFlattenSections:
    def test_nested_sections_depth_first(self) -> None:
        # Two top-level sections; the first has one nested child.
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}" xml:id="s1">
              <head>Introduction</head>
              <p>Intro text.</p>
              <div xmlns="{TEI_NS}" xml:id="s1-1">
                <head>Background</head>
                <p>Background text.</p>
              </div>
            </div>
            <div xmlns="{TEI_NS}" xml:id="s2">
              <head>Methods</head>
              <p>Methods text.</p>
            </div>
        """)
        doc = TeiDocument.from_bytes(xml)
        blocks = flatten_sections(doc)
        # Depth-first order: Introduction block, Background block, Methods block.
        assert len(blocks) == 3
        assert blocks[0] == "Introduction\nIntro text."
        assert blocks[1] == "Background\nBackground text."
        assert blocks[2] == "Methods\nMethods text."

    def test_empty_document_returns_empty_list(self) -> None:
        xml = _wrap_tei_body("")
        assert flatten_sections(TeiDocument.from_bytes(xml)) == []

    def test_section_without_title(self) -> None:
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}">
              <p>Only a paragraph, no heading.</p>
            </div>
        """)
        blocks = flatten_sections(TeiDocument.from_bytes(xml))
        assert blocks == ["Only a paragraph, no heading."]


class TestContentSha256:
    def test_hash_matches_bytes(self) -> None:
        import hashlib

        data = _wrap_tei_body("")
        doc = TeiDocument.from_bytes(data)
        assert doc.content_sha256 == hashlib.sha256(data).hexdigest()


class TestOpenTeiBridge:
    def test_open_tei_resolves_relative_path(self, tmp_path: Path) -> None:
        output_dir = tmp_path
        tei_dir = output_dir / "tei"
        tei_dir.mkdir()
        tei_bytes = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}"><head>H</head><p>P.</p></div>
        """)
        (tei_dir / "W1.tei.xml").write_bytes(tei_bytes)

        rec = ExtractedDocument(
            work_id="https://openalex.org/W1",
            tei_path="tei/W1.tei.xml",
            content_sha256="0" * 64,
            extracted_at="2026-04-17T00:00:00.000000+00:00",
        )
        tei = rec.open_tei(output_dir)
        secs = tei.sections()
        assert secs == [Section(id=None, title="H", paragraphs=["P."], children=[])]
