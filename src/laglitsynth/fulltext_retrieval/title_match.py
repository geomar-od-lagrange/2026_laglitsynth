"""Match a returned PDF to a catalogue work by title, for PDFs that carry no DOI.

The question is closed-set: which catalogue work's title does this PDF carry,
if any? Candidate strings come from the PDF's metadata title (Info
dictionary ``/Title``, XMP ``dc:title``) and from windows of consecutive
lines at the top of the first page, where the title block sits. Each
candidate is compared with every catalogue title by ``rapidfuzz``'s
normalised edit-distance ratio, and every title keeps its best score.

A match is accepted only when it is clear, and refused otherwise, never
guessed. The thresholds below were chosen against the PDF store, by hiding
the DOI of every PDF whose DOI confirms its work and checking which rule
files none of them under the wrong work
(``docs/explorations/contributor-pdf-matching.md`` has the measurement).
"""

from __future__ import annotations

import html
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from rapidfuzz import fuzz, process

# The best title must score at least this (0-100 ratio) ...
TITLE_MIN_SCORE = 95.0
# ... beat the next-best different title by at least this much ...
TITLE_MIN_MARGIN = 10.0
# ... and have a key at least this long: short titles such as "Geophysical
# fluid dynamics" recur verbatim in affiliations and running heads. A shorter
# title is never accepted but still counts as the runner-up, so it can block
# a correct match; that costs recall only, never a wrong filing.
TITLE_MIN_KEY_LENGTH = 30

# Shorter strings are neither indexed nor used as candidates: they carry too
# little to compare by edit distance.
_MIN_COMPARE_LENGTH = 15
# The title block lies within the first lines of page 1 and spans a few lines.
_MAX_LINES = 40
_MAX_WINDOW = 8
# Per candidate, only the closest catalogue titles above this score matter.
_PER_CANDIDATE_LIMIT = 3
_SCORE_CUTOFF = 70.0

# Inline markup OpenAlex titles carry: HTML emphasis and scripts, JATS
# small caps, and MathML elements. Only these tags are dropped, so text such
# as "u < 0 for x > 1" keeps its letters and digits.
_TAG_RE = re.compile(
    r"</?(?:i|b|em|strong|u|sub|sup|sc|span|scp|"
    r"(?:mml:)?(?:math|mi|mn|mo|ms|mrow|msub|msup|msubsup|mfrac|msqrt|mtext|mover|munder|mstyle|mspace))"
    r"""(?:\s+[\w:-]+=(?:"[^"]*"|'[^']*'))*\s*/?>""",
    re.IGNORECASE,
)


def title_key(text: str) -> str:
    """Return the comparison key of a title or a run of page text.

    Markup is dropped, Unicode is NFKC-normalised (ligatures such as ``ﬁ``
    become ``fi``) and casefolded, and every character that is not a letter
    or digit is removed, spaces included. That makes line breaks,
    end-of-line hyphenation, and the spaces a text extractor drops or
    inserts irrelevant.
    """
    text = _TAG_RE.sub("", html.unescape(text))
    text = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in text if ch.isalnum())


def first_page_candidates(page_text: str) -> list[str]:
    """Return the title-block candidates of a first page, as keys.

    Every run of one to ``_MAX_WINDOW`` consecutive lines among the first
    ``_MAX_LINES`` non-blank lines, so a title broken over several lines is
    compared whole and not diluted by the authors or affiliations around it.
    """
    lines = [line for line in page_text.splitlines() if title_key(line)][:_MAX_LINES]
    seen: set[str] = set()
    out: list[str] = []
    for start in range(len(lines)):
        for end in range(start + 1, min(start + _MAX_WINDOW, len(lines)) + 1):
            key = title_key(" ".join(lines[start:end]))
            if len(key) >= _MIN_COMPARE_LENGTH and key not in seen:
                seen.add(key)
                out.append(key)
    return out


@dataclass(frozen=True)
class TitleMatch:
    """The outcome of matching one PDF by title.

    ``stem`` is the accepted work, or None. ``contenders`` names the works a
    near-tie left undecided, so the caller can report them; it is empty when
    nothing came close enough to be worth reporting.
    """

    stem: str | None
    contenders: tuple[str, ...] = ()


class TitleIndex:
    """Catalogue titles keyed for matching, built once per import."""

    def __init__(self, titles: Mapping[str, str | None]) -> None:
        """``titles`` maps each work's stem to its catalogue title (None if absent)."""
        stems_by_key: dict[str, set[str]] = defaultdict(set)
        for stem, title in titles.items():
            if title is None:
                continue
            key = title_key(title)
            if len(key) >= _MIN_COMPARE_LENGTH:
                stems_by_key[key].add(stem)
        self._stems_by_key = dict(stems_by_key)
        self._keys = list(self._stems_by_key)

    def match(self, metadata_titles: Iterable[str], first_page: str) -> TitleMatch:
        """Match a PDF's metadata titles and first-page text against the catalogue."""
        candidates = [
            key
            for title in metadata_titles
            if len(key := title_key(title)) >= _MIN_COMPARE_LENGTH
        ]
        candidates += first_page_candidates(first_page)

        best: dict[str, float] = {}
        for candidate in candidates:
            for key, score, _ in process.extract(
                candidate,
                self._keys,
                scorer=fuzz.ratio,
                limit=_PER_CANDIDATE_LIMIT,
                score_cutoff=_SCORE_CUTOFF,
            ):
                if score > best.get(key, 0.0):
                    best[key] = score
        if not best:
            return TitleMatch(stem=None)

        ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
        top_key, top_score = ranked[0]
        if top_score < TITLE_MIN_SCORE:
            return TitleMatch(stem=None)
        runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
        if top_score - runner_up < TITLE_MIN_MARGIN:
            close = [k for k, s in ranked if top_score - s < TITLE_MIN_MARGIN]
            return TitleMatch(stem=None, contenders=self._stems(close))
        if len(self._stems_by_key[top_key]) > 1:
            # Two catalogue works share the title, e.g. a preprint and its article.
            return TitleMatch(stem=None, contenders=self._stems([top_key]))
        if len(top_key) < TITLE_MIN_KEY_LENGTH:
            return TitleMatch(stem=None)
        (stem,) = self._stems_by_key[top_key]
        return TitleMatch(stem=stem)

    def _stems(self, keys: Iterable[str]) -> tuple[str, ...]:
        return tuple(sorted({s for k in keys for s in self._stems_by_key[k]}))
