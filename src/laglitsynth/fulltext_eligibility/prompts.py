"""Full-text rendering for eligibility assessment.

``render_fulltext`` flattens a ``TeiDocument`` into the single string
sent as the decision ``input``. The question itself comes from the
eligibility-criteria YAML's ``decision`` block.
"""

from __future__ import annotations

from laglitsynth.fulltext_extraction.tei import TeiDocument, flatten_sections

__all__ = [
    "USER_TEMPLATE",
    "render_fulltext",
]

# Marker for the input render, folded into prompt_sha256 so the hash pins
# both the question and the shape of the input.
USER_TEMPLATE = "full_text"


def render_fulltext(tei: TeiDocument) -> str:
    """Flatten TEI sections into a single blank-line-separated string.

    Depth-first walk of ``tei.sections()``; each section title + its
    paragraphs form one block, blocks separated by a blank line.
    Returns the empty string when ``sections()`` is empty; the caller
    treats an empty render as a ``tei-parse-failure`` since stage 7 has
    no abstract fallback.
    """
    return "\n\n".join(flatten_sections(tei))
