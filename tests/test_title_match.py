"""Unit tests for title matching: keys, candidates and the acceptance rule."""

from __future__ import annotations

from laglitsynth.fulltext_retrieval.title_match import (
    TitleIndex,
    TitleMatch,
    first_page_candidates,
    title_key,
)

TITLE = "Deep Lagrangian connectivity in the global ocean inferred from Argo floats"


class TestTitleKey:
    def test_case_spacing_and_punctuation_do_not_matter(self) -> None:
        assert title_key("Deep  Lagrangian\nConnectivity: in-the") == title_key(
            "deep lagrangian connectivity in the"
        )

    def test_ligatures_and_markup_are_normalised(self) -> None:
        assert title_key("<i>ﬁ</i>eld &amp; ﬂow") == "fieldflow"

    def test_end_of_line_hyphenation_joins_the_word(self) -> None:
        assert title_key("Lagran-\ngian") == "lagrangian"


class TestFirstPageCandidates:
    def test_a_title_over_three_lines_is_one_candidate(self) -> None:
        page = "Deep Lagrangian Connectivity\nin the Global Ocean Inferred\nfrom Argo Floats\nA. Author"
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
        other = "Deep Lagrangian connectivity in the Indian ocean inferred from drifters"
        assert index.match([other], "") == TitleMatch(stem=None)

    def test_a_work_without_a_title_is_not_indexed(self) -> None:
        index = TitleIndex({"W1": None, "W2": TITLE})
        assert index.match([TITLE], "").stem == "W2"

    def test_a_shared_title_names_both_works(self) -> None:
        index = TitleIndex({"W1": TITLE, "W2": TITLE.upper()})
        result = index.match([TITLE], "")
        assert result.stem is None
        assert result.contenders == ("W1", "W2")
