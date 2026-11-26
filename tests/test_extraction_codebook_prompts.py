"""Tests for ``laglitsynth.extraction_codebook.prompts``.

The system-prompt / field-list assembly tests live in
``test_extraction_codebook_loader.py`` now that those concerns moved
into the codebook module. This file only covers the user-message
render path (TEI flattening) and the user-message wrapper.
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


class TestBuildUserMessage:
    def test_full_text_tag(self) -> None:
        msg = build_user_message("body text")
        assert msg == "full_text:\nbody text"
