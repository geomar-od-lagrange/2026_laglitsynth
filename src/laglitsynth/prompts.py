"""Shared loader for externalized LLM system prompts.

Several stages keep their system prompt in an external criteria YAML so
swapping topics is configuration work rather than a code change. The YAML
carries a single string-valued ``system_prompt`` field; this helper reads
it (from a path or an already-inlined snapshot mapping) and returns it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laglitsynth.config import resolve_yaml_arg


def load_system_prompt(spec: str | Path | dict[str, Any]) -> str:
    """Return the ``system_prompt`` string from a criteria YAML spec.

    ``spec`` may be a path to a YAML file or an already-loaded mapping
    (the inlined-snapshot case). The mapping must carry a string-valued
    ``system_prompt`` field.
    """
    loaded = resolve_yaml_arg(spec)
    prompt = loaded.get("system_prompt")
    if not isinstance(prompt, str):
        raise ValueError(
            "criteria spec must include a string 'system_prompt' field"
        )
    return prompt
