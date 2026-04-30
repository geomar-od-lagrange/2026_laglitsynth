"""Tests for ``laglitsynth.extraction_codebook.prompts``."""

from __future__ import annotations

from laglitsynth.extraction_codebook.models import ExtractionRecord, _ExtractionPayload
from laglitsynth.extraction_codebook.prompts import (
    SYSTEM_PROMPT,
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


class TestSystemPromptCoversEveryField:
    """Guard against prompt/model drift.

    Every LLM-fillable field in ``_ExtractionPayload`` must appear in
    the system prompt's field list. If a field is added to the payload
    without regenerating the prompt, this test fails — which is the
    whole point.
    """

    def test_every_payload_field_named_in_prompt(self) -> None:
        missing = [
            name for name in _ExtractionPayload.model_fields
            if name not in SYSTEM_PROMPT
        ]
        assert missing == [], (
            f"Fields absent from SYSTEM_PROMPT: {missing}. "
            "Regenerate the field list from _ExtractionPayload."
        )

    def test_identification_fields_not_in_prompt(self) -> None:
        # The identification block is filled by the caller, not the LLM.
        # These keys should NOT be in the payload schema; this is a
        # separate assertion in case someone leaks them in.
        payload_fields = set(_ExtractionPayload.model_fields)
        for ident in ("work_id", "source_basis", "reason", "seed", "truncated"):
            assert ident not in payload_fields, (
                f"Identification field {ident!r} leaked into _ExtractionPayload."
            )

    def test_content_fields_match_record_minus_identification(self) -> None:
        # The payload covers exactly the non-identification fields of
        # the full record — guarantees we can compose the record from
        # (payload dump + identification kwargs) without missing a key.
        record_fields = set(ExtractionRecord.model_fields)
        payload_fields = set(_ExtractionPayload.model_fields)
        identification = {
            "work_id",
            "source_basis",
            "reason",
            "seed",
            "truncated",
            "raw_response",
        }
        assert payload_fields == record_fields - identification
