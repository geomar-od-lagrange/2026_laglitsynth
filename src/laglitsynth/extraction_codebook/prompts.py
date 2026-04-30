"""Prompt construction for the extraction codebook stage.

The system prompt expands its field list from
``_ExtractionPayload.model_json_schema()`` so the prompt and the
validator stay in lockstep when fields are added or renamed.
``render_fulltext`` flattens a ``TeiDocument`` into a single string,
truncating at ``CHAR_BUDGET`` on paragraph boundaries to keep the
prompt within the LLM's context window. ``build_user_message`` wraps
the rendered body with the ``source_basis`` tag the system prompt
references.

Stage 7 has its own ``render_fulltext`` without truncation. The budget
behaviour is stage-8-specific; per the plan this duplicates the stage 7
helper with light factoring rather than introducing a shared module.
"""

from __future__ import annotations

from typing import Any

from laglitsynth.extraction_codebook.models import SourceBasis, _ExtractionPayload
from laglitsynth.fulltext_extraction.tei import TeiDocument, flatten_sections

# Tuning placeholder — tune on first smoke run against real papers.
# Roughly ~15k tokens on typical English prose; the principled fix is
# two-pass retrieval, not a larger number here.
CHAR_BUDGET = 60_000


def _field_line(name: str, info: dict[str, Any]) -> str:
    description = info.get("description")
    if description:
        return f'- "{name}": {description}'
    return f'- "{name}"'


def _render_field_list() -> str:
    schema = _ExtractionPayload.model_json_schema()
    properties: dict[str, dict[str, Any]] = schema.get("properties", {})
    # model_json_schema preserves field definition order and emits the
    # per-field ``description`` set via ``Field(..., description=...)``
    # on ``_ExtractionPayload`` — so the LLM sees the codebook
    # definition of each field, not just its name.
    return "\n".join(_field_line(name, info) for name, info in properties.items())


_FIELD_LIST = _render_field_list()


SYSTEM_PROMPT = f"""\
You extract structured metadata from scientific papers on computational
Lagrangian methods in oceanography. You will be shown the text of one
paper and must fill a JSON object with the fields below. Every value
field has a companion "*_context" field containing a short verbatim
excerpt from the paper supporting that value. If the paper does not
state the information, write null for both the value and its context —
do not guess or infer.

Fields:
{_FIELD_LIST}

Respond with a single JSON object containing exactly these keys."""


USER_TEMPLATE = "{source_basis}:\n{text}"


def _truncate_blocks(blocks: list[str], char_budget: int) -> tuple[list[str], bool]:
    """Drop trailing blocks that would push the join over ``char_budget``.

    Blocks are kept whole: the join separator is a blank line (``"\\n\\n"``).
    Returns ``(kept_blocks, truncated_flag)``. If even the first block is
    over budget it is still included on its own — truncation only drops
    trailing blocks; we do not cut mid-paragraph.
    """
    if not blocks:
        return [], False
    kept: list[str] = [blocks[0]]
    running = len(blocks[0])
    truncated = False
    for block in blocks[1:]:
        tentative = running + 2 + len(block)  # "\n\n" separator
        if tentative > char_budget:
            truncated = True
            break
        kept.append(block)
        running = tentative
    return kept, truncated


def render_fulltext(tei: TeiDocument, *, char_budget: int) -> tuple[str, bool]:
    """Flatten TEI sections into a blank-line-separated string.

    Depth-first walk of ``tei.sections()``; each section title + its
    paragraphs form one block, blocks separated by a blank line. The
    joined text is truncated at ``char_budget`` preferring paragraph
    (i.e. block) boundaries — the first block that would push the body
    over budget is dropped whole rather than cut mid-sentence.

    Returns ``(text, truncated)``. Empty ``sections()`` returns
    ``("", False)``.
    """
    kept, truncated = _truncate_blocks(flatten_sections(tei), char_budget)
    return "\n\n".join(kept), truncated


def build_user_message(source_basis: SourceBasis, text: str) -> str:
    """Wrap rendered body text with the ``source_basis`` tag."""
    return USER_TEMPLATE.format(source_basis=source_basis, text=text)
