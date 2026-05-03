"""Shared Ollama helpers used across LLM-backed stages."""

from __future__ import annotations

from openai import OpenAI


def preflight(*, base_url: str, model: str) -> None:
    """Raise SystemExit with a specific message if Ollama is unreachable
    or the model isn't pulled.
    """
    try:
        client = OpenAI(base_url=f"{base_url}/v1", api_key="ollama")
    except Exception as exc:
        raise SystemExit(f"Ollama URL invalid: {base_url} — {exc}")
    try:
        client.models.list()
    except Exception:
        raise SystemExit(
            f"Ollama unreachable at {base_url}. Start it with "
            f"`ollama serve` or check your --base-url / SSH tunnel."
        )
    try:
        client.models.retrieve(model)
    except Exception:
        raise SystemExit(
            f"Ollama responds at {base_url} but model {model!r} is "
            f"not pulled. Run `ollama pull {model}` first."
        )
