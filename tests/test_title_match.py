"""Unit tests for title matching: keys, candidates and the acceptance rule."""

from __future__ import annotations

from laglitsynth.fulltext_retrieval.title_match import (
    TITLE_MIN_KEY_LENGTH,
    TITLE_MIN_MARGIN,
    TITLE_MIN_SCORE,
    TitleIndex,
    TitleMatch,
    first_page_candidates,
    title_key,
)

TITLE = "Lagrangian pathways of intermediate water inferred from synthetic floats"

# Letters only, so a key equals its title. Replacing k of n letters with
# digits gives fuzz.ratio 100 * (1 - k / n) against the original.
_ALPHABET = "abcdefghijklmnopqrstuvwxyz"


def _letters(n: int) -> str:
    return (_ALPHABET * 3)[:n]


def _substituted(text: str, k: int) -> str:
    return text[:-k] + "0123456789"[:k]


class TestTitleKey:
    def test_case_spacing_and_punctuation_do_not_matter(self) -> None:
        assert title_key("Lagrangian  Pathways\nof: Inter-mediate") == title_key(
            "lagrangian pathways of intermediate"
        )

    def test_ligatures_and_markup_are_normalised(self) -> None:
        assert title_key("<i>ﬁ</i>eld &amp; ﬂow") == "fieldflow"

    def test_subscripts_and_mathml_are_dropped_but_their_text_kept(self) -> None:
        assert title_key("CO<sub>2</sub> flux") == "co2flux"
        assert title_key('<mml:math xmlns:mml="m"><mml:mi>x</mml:mi></mml:math>') == "x"

    def test_comparison_signs_are_not_mistaken_for_markup(self) -> None:
        assert title_key("u &lt; 0 for x &gt; 1") == "u0forx1"
        assert title_key("a<b and c>d") == "abandcd"

    def test_end_of_line_hyphenation_joins_the_word(self) -> None:
        assert title_key("Lagran-\ngian") == "lagrangian"


class TestFirstPageCandidates:
    def test_a_title_over_three_lines_is_one_candidate(self) -> None:
        page = (
            "Lagrangian Pathways of Intermediate\nWater Inferred from\n"
            "Synthetic Floats\nA. Author"
        )
        assert title_key(TITLE) in first_page_candidates(page)

    def test_lines_past_the_top_of_the_page_are_ignored(self) -> None:
        page = "\n".join(f"filler line number {i}" for i in range(40)) + "\n" + TITLE
        assert title_key(TITLE) not in first_page_candidates(page)


class TestTitleIndex:
    def test_an_exact_title_is_accepted(self) -> None:
        index = TitleIndex({"W1": TITLE, "W2": "Something else entirely about tides"})
        assert index.match([], TITLE).stem == "W1"

    def test_a_small_extraction_error_is_tolerated(self) -> None:
        index = TitleIndex({"W1": TITLE})
        assert index.match([TITLE.replace("inferred", "infered")], "").stem == "W1"

    def test_a_title_far_from_any_catalogue_title_is_unmatched(self) -> None:
        index = TitleIndex({"W1": TITLE})
        result = index.match(["Tidal mixing over rough topography revisited"], "")
        assert result.stem is None
        assert result.contenders == ()

    def test_a_related_but_different_title_is_not_accepted(self) -> None:
        index = TitleIndex({"W1": TITLE})
        other = "Lagrangian pathways of mode water inferred from drifters"
        assert index.match([other], "") == TitleMatch(stem=None)

    def test_a_work_without_a_title_is_not_indexed(self) -> None:
        index = TitleIndex({"W1": None, "W2": TITLE})
        assert index.match([TITLE], "").stem == "W2"

    def test_a_shared_title_names_both_works(self) -> None:
        index = TitleIndex({"W1": TITLE, "W2": TITLE.upper()})
        result = index.match([TITLE], "")
        assert result.stem is None
        assert result.contenders == ("W1", "W2")


class TestAcceptanceBoundaries:
    def test_a_score_of_exactly_the_minimum_is_accepted(self) -> None:
        title = _letters(40)  # two substitutions in 40: ratio 95.0
        assert TITLE_MIN_SCORE == 95.0
        assert TitleIndex({"W1": title}).match([_substituted(title, 2)], "").stem == "W1"

    def test_a_score_just_below_the_minimum_is_not(self) -> None:
        title = _letters(39)  # two substitutions in 39: ratio 94.9
        result = TitleIndex({"W1": title}).match([_substituted(title, 2)], "")
        assert result == TitleMatch(stem=None)

    def test_a_margin_of_exactly_the_minimum_is_accepted(self) -> None:
        title = _letters(40)  # the runner-up scores 90.0
        assert TITLE_MIN_MARGIN == 10.0
        index = TitleIndex({"W1": title, "W2": _substituted(title, 4)})
        assert index.match([title], "").stem == "W1"

    def test_a_margin_just_below_the_minimum_is_refused(self) -> None:
        title = _letters(41)  # the runner-up scores 90.2
        index = TitleIndex({"W1": title, "W2": _substituted(title, 4)})
        assert index.match([title], "") == TitleMatch(stem=None, contenders=("W1", "W2"))

    def test_a_key_of_the_minimum_length_is_accepted(self) -> None:
        title = _letters(TITLE_MIN_KEY_LENGTH)
        assert TitleIndex({"W1": title}).match([title], "").stem == "W1"

    def test_a_key_one_shorter_is_not(self) -> None:
        title = _letters(TITLE_MIN_KEY_LENGTH - 1)
        assert TitleIndex({"W1": title}).match([title], "") == TitleMatch(stem=None)

    WORDS = [
        "particle", "tracking", "currents", "drifters", "isopycna",
        "seasonal", "boundary", "westerly", "gyrecore",
    ]  # fmt: skip

    def test_a_title_over_eight_lines_is_matched(self) -> None:
        title = " ".join(self.WORDS[:8])
        page = "\n".join(self.WORDS[:8])
        assert TitleIndex({"W1": title}).match([], page).stem == "W1"

    def test_a_title_over_nine_lines_is_not_seen_whole(self) -> None:
        # The best window holds 8 of 9 equal lines: ratio 94.1.
        title = " ".join(self.WORDS)
        page = "\n".join(self.WORDS)
        assert TitleIndex({"W1": title}).match([], page) == TitleMatch(stem=None)
