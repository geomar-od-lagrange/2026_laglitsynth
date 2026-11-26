"""Tests for ``laglitsynth.fulltext_eligibility.prompts``."""

from __future__ import annotations

from pathlib import Path

import pytest

from laglitsynth.fulltext_eligibility.eligibility import criteria_sha256, question_sha256
from laglitsynth.fulltext_eligibility.prompts import render_fulltext
from laglitsynth.fulltext_extraction.tei import TeiDocument
from laglitsynth.prompts import DecisionQuestion, load_decision_question

DEFAULT_CRITERIA_PATH = Path("examples/eligibility-criteria/lagrangian-oceanography.yaml")

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


class TestExampleCriteria:
    def test_loads_as_decision_question(self) -> None:
        question = load_decision_question(DEFAULT_CRITERIA_PATH)
        assert isinstance(question, DecisionQuestion)
        assert question.instructions
        assert question.criteria.true
        assert question.criteria.false

    def test_covers_criteria_one_and_two_only(self) -> None:
        question = load_decision_question(DEFAULT_CRITERIA_PATH)
        text = question.render().lower()
        assert "ocean flow field" in text
        assert "primary research" in text
        assert "numerical methods used" not in text

    def test_has_no_system_prompt(self) -> None:
        import yaml

        loaded = yaml.safe_load(DEFAULT_CRITERIA_PATH.read_text(encoding="utf-8"))
        assert "system_prompt" not in loaded


class TestCriteriaSha256:
    def test_path_and_inlined_spec_hash_equal(self) -> None:
        import yaml

        inlined = yaml.safe_load(DEFAULT_CRITERIA_PATH.read_text(encoding="utf-8"))
        assert criteria_sha256(DEFAULT_CRITERIA_PATH) == criteria_sha256(inlined)

    def test_equals_question_hash(self) -> None:
        question = load_decision_question(DEFAULT_CRITERIA_PATH)
        assert criteria_sha256(DEFAULT_CRITERIA_PATH) == question_sha256(question)

    def test_changes_with_wording(self) -> None:
        a = {"decision": {"instructions": "Q?", "criteria": {"true": "t", "false": "f"}}}
        b = {"decision": {"instructions": "Q!", "criteria": {"true": "t", "false": "f"}}}
        assert criteria_sha256(a) != criteria_sha256(b)

    def test_spec_without_decision_raises(self) -> None:
        with pytest.raises(ValueError, match="decision"):
            criteria_sha256({"id": "x"})
