"""Tests for ``laglitsynth.fulltext_eligibility.prompts``."""

from __future__ import annotations

from pathlib import Path

import pytest

from laglitsynth.fulltext_eligibility.prompts import (
    build_user_message,
    load_system_prompt,
    render_fulltext,
)
from laglitsynth.fulltext_extraction.tei import TeiDocument

# Verbatim transcription of the original module-level SYSTEM_PROMPT
# (from the pre-YAML version of prompts.py). The shipped default
# eligibility-criteria YAML must reproduce this byte-for-byte.
LEGACY_SYSTEM_PROMPT = """\
You are assessing whether a scientific paper meets the inclusion criteria
for a systematic review of numerical methods in Lagrangian oceanography.

Criteria:
1. The paper describes a computation that tracks particles, tracers, or
   objects in an ocean flow field.
2. The paper is primary research (not a review, editorial, or commentary).
3. The paper contains at least some description of the numerical methods
   used.

Respond with JSON: {"eligible": true|false, "reason": "<one sentence>"}.
Return ONLY the JSON object, nothing else."""

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


class TestBuildUserMessage:
    def test_full_text_tag(self) -> None:
        msg = build_user_message("full_text", "body text")
        assert msg == "full_text:\nbody text"

    def test_abstract_only_tag(self) -> None:
        msg = build_user_message("abstract_only", "just the abstract")
        assert msg == "abstract_only:\njust the abstract"


class TestLoadSystemPrompt:
    def test_default_yaml_reproduces_legacy_prompt_byte_for_byte(self) -> None:
        loaded = load_system_prompt(DEFAULT_CRITERIA_PATH)
        assert loaded == LEGACY_SYSTEM_PROMPT

    def test_loads_from_inlined_dict(self) -> None:
        spec = {"id": "x", "system_prompt": "hello world"}
        assert load_system_prompt(spec) == "hello world"

    def test_missing_system_prompt_key_raises(self) -> None:
        with pytest.raises(ValueError, match="system_prompt"):
            load_system_prompt({"id": "x"})

    def test_alternate_yaml_changes_prompt(self, tmp_path: Path) -> None:
        alt = tmp_path / "alt.yaml"
        alt.write_text(
            "id: alt\nsystem_prompt: |-\n  Alternate criterion text.\n",
            encoding="utf-8",
        )
        assert load_system_prompt(alt) == "Alternate criterion text."
