"""Tests for the shared laglitsynth.ollama.preflight helper."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from laglitsynth.ollama import preflight


def test_invalid_base_url_raises_url_invalid() -> None:
    """OpenAI constructor failure → 'URL invalid' SystemExit."""
    with patch("laglitsynth.ollama.OpenAI", side_effect=Exception("bad scheme")):
        with pytest.raises(SystemExit, match="Ollama URL invalid"):
            preflight(base_url="not-a-url", model="any-model")


def test_unreachable_ollama_raises_unreachable() -> None:
    """models.list() failure → 'unreachable' SystemExit."""
    mock_client = MagicMock()
    mock_client.models.list.side_effect = Exception("connection refused")
    with patch("laglitsynth.ollama.OpenAI", return_value=mock_client):
        with pytest.raises(SystemExit, match="Ollama unreachable"):
            preflight(base_url="http://localhost:11434", model="some-model")


def test_model_not_pulled_raises_not_pulled() -> None:
    """models.list() succeeds but models.retrieve() fails → 'not pulled' SystemExit
    containing the model name."""
    mock_client = MagicMock()
    mock_client.models.list.return_value = MagicMock()
    mock_client.models.retrieve.side_effect = Exception("model not found")
    with patch("laglitsynth.ollama.OpenAI", return_value=mock_client):
        with pytest.raises(SystemExit, match="llama3.1:8b"):
            preflight(base_url="http://localhost:11434", model="llama3.1:8b")
