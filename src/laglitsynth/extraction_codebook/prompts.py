"""User-message construction and full-text rendering for stage 8.

The system prompt and field list are codebook-driven and live in
[codebook.py](codebook.py); this module only handles the per-work
user-message body (TEI flattening). The whole rendered text is sent: a
text longer than the model's context is the provider's to reject.
"""

from __future__ import annotations

from laglitsynth.fulltext_extraction.tei import TeiDocument, flatten_sections

USER_TEMPLATE = "full_text:\n{text}"


def render_fulltext(tei: TeiDocument) -> str:
    """Flatten TEI sections into a blank-line-separated string.

    Depth-first walk of ``tei.sections()``; each section title + its
    paragraphs form one block, blocks separated by a blank line. Empty
    ``sections()`` returns ``""``.
    """
    return "\n\n".join(flatten_sections(tei))


def build_user_message(text: str) -> str:
    """Wrap rendered full-text body for the user message."""
    return USER_TEMPLATE.format(text=text)
