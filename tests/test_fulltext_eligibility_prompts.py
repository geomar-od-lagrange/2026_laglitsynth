"""Tests for ``laglitsynth.fulltext_eligibility.prompts``."""

from __future__ import annotations

from laglitsynth.fulltext_eligibility.prompts import (
    build_user_message,
    render_abstract,
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
        text = render_fulltext(TeiDocument.from_bytes(xml))
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

    def test_empty_sections_returns_empty_string(self) -> None:
        xml = _wrap_tei_body("")
        text = render_fulltext(TeiDocument.from_bytes(xml))
        assert text == ""

    def test_section_without_head_skips_title_line(self) -> None:
        xml = _wrap_tei_body(f"""
            <div xmlns="{TEI_NS}">
              <p>Only a paragraph.</p>
            </div>
        """)
        text = render_fulltext(TeiDocument.from_bytes(xml))
        assert text == "Only a paragraph."


class TestRenderAbstract:
    def test_passthrough(self) -> None:
        assert render_abstract("An abstract.") == "An abstract."


class TestBuildUserMessage:
    def test_full_text_tag(self) -> None:
        msg = build_user_message("full_text", "body text")
        assert msg == "full_text:\nbody text"

    def test_abstract_only_tag(self) -> None:
        msg = build_user_message("abstract_only", "just the abstract")
        assert msg == "abstract_only:\njust the abstract"
