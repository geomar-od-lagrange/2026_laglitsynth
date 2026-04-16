"""Tests for the fulltext_extraction stage."""

from __future__ import annotations

from pathlib import Path

from laglitsynth.fulltext_extraction.extract import parse_tei
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
