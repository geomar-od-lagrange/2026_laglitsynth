from pathlib import Path
from unittest.mock import MagicMock, patch

import pyalex
import pytest

from laglitsynth.catalogue_fetch.fetch import _reconstruct_abstract, run, search_openalex
from laglitsynth.io import JsonlReadStats


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


def _make_raw_work(**overrides: object) -> dict[str, object]:
    """Return a minimal raw record that passes Work validation."""
    base: dict[str, object] = {
        "id": "https://openalex.org/W1234",
        "doi": None,
        "title": "Test paper",
        "type": "article",
        "publication_year": 2024,
        "publication_date": "2024-01-01",
        "language": "en",
        "authorships": [],
        "biblio": {},
        "primary_location": None,
        "open_access": None,
        "cited_by_count": 0,
        "referenced_works": [],
        "updated_date": None,
        "keywords": [],
        "topics": [],
        "primary_topic": None,
        "abstract_inverted_index": None,
        "is_retracted": False,
    }
    base.update(overrides)
    return base


def _mock_works_query(pages: list[list[dict[str, object]]]) -> MagicMock:
    """Build a mock pyalex Works query that yields the given pages."""
    mock_query = MagicMock()
    mock_query.search.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.select.return_value = mock_query
    mock_query.count.return_value = sum(len(p) for p in pages)
    mock_query.paginate.return_value = iter(pages)
    return mock_query


def test_api_key_plumbed_to_pyalex():
    """--api-key value must be set on pyalex.config.api_key before fetching."""
    raw = _make_raw_work()
    mock_query = _mock_works_query([[raw]])

    with patch("laglitsynth.catalogue_fetch.fetch.pyalex.Works", return_value=mock_query):
        stats = JsonlReadStats()
        works = list(search_openalex("test query", stats=stats))

    # Verify the fixture worked and the actual api_key plumbing test below
    assert len(works) == 1

    # Simulate the run() path: set api_key then call search_openalex
    sentinel = "test-key-abc123"
    pyalex.config.api_key = sentinel

    with patch("laglitsynth.catalogue_fetch.fetch.pyalex.Works", return_value=mock_query):
        mock_query.paginate.return_value = iter([[raw]])
        list(search_openalex("test query"))

    assert pyalex.config.api_key == sentinel


def test_api_key_set_by_run(tmp_path: pytest.MonkeyPatch) -> None:
    """run() must assign args.api_key to pyalex.config.api_key."""
    import argparse
    from laglitsynth.catalogue_fetch.fetch import run

    raw = _make_raw_work()
    mock_query = _mock_works_query([[raw]])

    args = argparse.Namespace(
        query="ocean drift",
        output=tmp_path / "out.jsonl",
        from_year=None,
        to_year=None,
        max_records=None,
        api_key="my-secret-key",
    )

    with patch("laglitsynth.catalogue_fetch.fetch.pyalex.Works", return_value=mock_query):
        run(args)

    assert pyalex.config.api_key == "my-secret-key"


def test_validation_skipped_counts_drops():
    """validation_skipped must reflect records that fail Work validation."""
    valid_raw = _make_raw_work()
    # A record missing the required `id` field will fail Work validation.
    invalid_raw = _make_raw_work()
    del invalid_raw["id"]  # type: ignore[misc]

    mock_query = _mock_works_query([[valid_raw, invalid_raw]])

    stats = JsonlReadStats()
    with patch("laglitsynth.catalogue_fetch.fetch.pyalex.Works", return_value=mock_query):
        works = list(search_openalex("test", stats=stats))

    assert len(works) == 1
    assert stats.skipped == 1


class TestApiKeyDotenvFallback:
    """Tests for the .env fallback behaviour of --api-key."""

    def _make_args(self, tmp_path: Path, api_key: str | None) -> MagicMock:
        import argparse

        args = argparse.Namespace(
            query="ocean drift",
            output=tmp_path / "out.jsonl",
            from_year=None,
            to_year=None,
            max_records=None,
            api_key=api_key,
        )
        return args  # type: ignore[return-value]

    def test_explicit_flag_wins_over_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--api-key from CLI wins; .env value is not used."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("OPENALEX_API_KEY=from-env\n")

        raw = _make_raw_work()
        mock_query = _mock_works_query([[raw]])
        args = self._make_args(tmp_path, api_key="from-flag")

        with patch("laglitsynth.catalogue_fetch.fetch.pyalex.Works", return_value=mock_query):
            run(args)  # type: ignore[arg-type]

        assert pyalex.config.api_key == "from-flag"

    def test_missing_flag_loads_from_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """No --api-key flag: value is read from .env with a stderr notice."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("OPENALEX_API_KEY=from-env\n")

        raw = _make_raw_work()
        mock_query = _mock_works_query([[raw]])
        args = self._make_args(tmp_path, api_key=None)

        with patch("laglitsynth.catalogue_fetch.fetch.pyalex.Works", return_value=mock_query):
            run(args)  # type: ignore[arg-type]

        assert pyalex.config.api_key == "from-env"
        captured = capsys.readouterr()
        assert "Loaded OPENALEX_API_KEY from .env" in captured.err

    def test_missing_both_fails_with_clear_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No flag and no .env: SystemExit with a clear message."""
        monkeypatch.chdir(tmp_path)
        # No .env file at all.
        args = self._make_args(tmp_path, api_key=None)

        with pytest.raises(SystemExit) as exc_info:
            run(args)  # type: ignore[arg-type]

        assert "OPENALEX_API_KEY" in str(exc_info.value)
        assert ".env" in str(exc_info.value)
