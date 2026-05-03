"""User-message construction and full-text rendering for stage 8.

The system prompt and field list are now codebook-driven and live in
[codebook.py](codebook.py); this module only handles the per-work
user-message body (TEI flattening + char-budget truncation) and the
``source_basis`` framing tag.

Stage 7 has its own ``render_fulltext`` without truncation. The budget
behaviour is stage-8-specific; per the plan this duplicates the stage 7
helper with light factoring rather than introducing a shared module.
"""

from __future__ import annotations

from laglitsynth.extraction_codebook.models import SourceBasis
from laglitsynth.fulltext_extraction.tei import TeiDocument, flatten_sections

# Tuning placeholder — tune on first smoke run against real papers.
# Roughly ~15k tokens on typical English prose; the principled fix is
# two-pass retrieval, not a larger number here.
CHAR_BUDGET = 60_000

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
