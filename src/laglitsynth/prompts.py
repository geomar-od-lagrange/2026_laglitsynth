"""Shared loader for externalized decision criteria.

Stages 3 and 7 keep their criteria in an external YAML so swapping topics
is configuration work rather than a code change. The YAML's ``decision``
block is read as a ``DecisionQuestion``, from a path or an
already-inlined snapshot mapping.
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
        """The predicate's ``instructions`` text sent to the decisions endpoint.

        A predicate question has no criteria field, so the true and false
        criteria follow the instructions.
        """
        return (
            f"{self.instructions}\n\n"
            f"True if: {self.criteria.true}\n"
            f"False if: {self.criteria.false}"
        )


def load_decision_question(spec: str | Path | dict[str, Any]) -> DecisionQuestion:
    """Return the ``decision`` block of a criteria YAML as a ``DecisionQuestion``.

    Raises ``ValueError`` naming the spec when the block is absent, so the
    stage fails before any request is sent.
    """
    loaded = resolve_yaml_arg(spec)
    block = loaded.get("decision")
    if not isinstance(block, dict):
        raise ValueError(
            f"criteria spec {spec} has no 'decision' block; the decisions "
            f"endpoint needs 'decision: {{instructions, criteria: {{true, false}}}}'"
        )
    return DecisionQuestion.model_validate(block)
