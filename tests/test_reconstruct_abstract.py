from laglitsynth.openalex.fetch import _reconstruct_abstract


def test_simple_sentence():
    index = {"The": [0], "cat": [1], "sat": [2]}
    assert _reconstruct_abstract(index) == "The cat sat"


def test_word_order_from_positions():
    index = {"world": [1], "hello": [0]}
    assert _reconstruct_abstract(index) == "hello world"


def test_repeated_word():
    index = {"the": [0, 2], "cat": [1], "dog": [3]}
    assert _reconstruct_abstract(index) == "the cat the dog"


def test_none_returns_none():
    assert _reconstruct_abstract(None) is None


def test_empty_dict_returns_none():
    assert _reconstruct_abstract({}) is None
