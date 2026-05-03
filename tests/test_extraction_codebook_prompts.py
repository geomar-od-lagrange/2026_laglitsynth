"""Tests for ``laglitsynth.extraction_codebook.prompts``.

The system-prompt / field-list assembly tests live in
``test_extraction_codebook_loader.py`` now that those concerns moved
into the codebook module. This file only covers the user-message
render path (TEI flattening + char-budget truncation) and the
``source_basis`` framing tag.
"""

from __future__ import annotations

from laglitsynth.extraction_codebook.prompts import (
    build_user_message,
    render_fulltext,
)
from laglitsynth.fulltext_extraction.tei import TeiDocument

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


class TestRenderFulltext:
    def test_depth_first_with_blank_line_separation(self) -> None:
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}">
              <head>Introduction</head>
              <p>Intro one.</p>
              <p>Intro two.</p>
            </div>
            <div xmlns="{TEI_NS}">
              <head>Methods</head>
              <p>Top methods.</p>
              <div xmlns="{TEI_NS}">
                <head>Sub</head>
                <p>Sub text.</p>
              </div>
            </div>
        """)
        text, truncated = render_fulltext(
            TeiDocument.from_bytes(xml), char_budget=1_000_000
        )
        expected = (
            "Introduction\n"
            "Intro one.\n"
            "Intro two.\n"
            "\n"
            "Methods\n"
            "Top methods.\n"
            "\n"
            "Sub\n"
            "Sub text."
        )
        assert text == expected
        assert truncated is False

    def test_under_budget_not_truncated(self) -> None:
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}">
              <head>Only</head>
              <p>Short paragraph.</p>
            </div>
        """)
        text, truncated = render_fulltext(
            TeiDocument.from_bytes(xml), char_budget=1_000
        )
        assert truncated is False
        assert "Short paragraph." in text

    def test_over_budget_truncates_on_paragraph_boundary(self) -> None:
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}">
              <head>Alpha</head>
              <p>AAAAAAAA</p>
            </div>
            <div xmlns="{TEI_NS}">
              <head>Beta</head>
              <p>BBBBBBBB</p>
            </div>
            <div xmlns="{TEI_NS}">
              <head>Gamma</head>
              <p>CCCCCCCC</p>
            </div>
        """)
        # Budget that fits the first block ("Alpha\nAAAAAAAA" = 14 chars)
        # but not the second on top (14 + 2 + 13 = 29 > 20).
        text, truncated = render_fulltext(
            TeiDocument.from_bytes(xml), char_budget=20
        )
        assert truncated is True
        assert "Alpha" in text
        assert "AAAAAAAA" in text
        # Dropped whole — no partial block, no "Beta" or "Gamma".
        assert "Beta" not in text
        assert "Gamma" not in text
        # Truncation drops trailing blocks: text ends at a paragraph boundary.
        assert text.endswith("AAAAAAAA")

    def test_empty_sections_returns_empty_string(self) -> None:
        xml = _wrap_tei_body("")
        text, truncated = render_fulltext(
            TeiDocument.from_bytes(xml), char_budget=1_000
        )
        assert text == ""
        assert truncated is False


class TestBuildUserMessage:
    def test_full_text_tag(self) -> None:
        msg = build_user_message("full_text", "body text")
        assert msg == "full_text:\nbody text"

    def test_abstract_only_tag(self) -> None:
        msg = build_user_message("abstract_only", "just the abstract")
        assert msg == "abstract_only:\njust the abstract"
