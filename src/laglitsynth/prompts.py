"""Shared loader for externalized LLM system prompts.

Several stages keep their system prompt in an external criteria YAML so
swapping topics is configuration work rather than a code change. The YAML
carries a single string-valued ``system_prompt`` field; this helper reads
it (from a path or an already-inlined snapshot mapping) and returns it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from laglitsynth.config import resolve_yaml_arg


class DecisionCriteria(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    true: str
    false: str


class DecisionQuestion(BaseModel):
    """The yes/no question a decisions backend asks about each work."""

    model_config = ConfigDict(extra="forbid")
    instructions: str
    criteria: DecisionCriteria

    def canonical_json(self) -> str:
        return json.dumps(self.model_dump(), sort_keys=True)

    def render(self) -> str:
        """Human-readable form for the meta's ``criterion`` field."""
        return (
            f"{self.instructions}\n"
            f"true: {self.criteria.true}\n"
            f"false: {self.criteria.false}"
        )


def load_decision_question(spec: str | Path | dict[str, Any]) -> DecisionQuestion:
    """Return the ``decision`` block of a criteria YAML as a ``DecisionQuestion``.

    Raises ``ValueError`` naming the spec when the block is absent, so the
    decisions backend fails before any request is sent.
    """
    loaded = resolve_yaml_arg(spec)
    block = loaded.get("decision")
    if not isinstance(block, dict):
        raise ValueError(
            f"criteria spec {spec} has no 'decision' block; the decisions "
            f"backend needs 'decision: {{instructions, criteria: {{true, false}}}}'"
        )
    return DecisionQuestion.model_validate(block)


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
