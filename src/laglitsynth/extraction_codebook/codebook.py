"""Data-driven extraction codebook.

A ``CodebookSpec`` YAML drives both the LLM-fillable payload pydantic
model (built dynamically at startup via ``pydantic.create_model``) and
the system prompt the extractor sends to the LLM. Adding, removing or
renaming fields becomes a config edit, not a code change.

Each codebook field carries a ``name`` and ``description``. With
``context: true`` (default) the loader also emits a paired
``<name>_context: str | None`` field whose description is the stock
"Short verbatim excerpt supporting <name>." line. ``context: false``
skips the pair entirely (used for ``in_text_locations`` and
``extraction_notes``).

The system prompt may contain a ``{fields}`` placeholder that the
loader substitutes with the rendered field list at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from laglitsynth.config import resolve_yaml_arg
from laglitsynth.extraction_codebook.models import SourceBasis


class ExtractionRecordProto(Protocol):
    """Identification block every extraction record carries.

    The dynamically-built record class adds the codebook's content
    fields on top; those are accessed by name when needed.
    """

    work_id: str
    source_basis: SourceBasis
    reason: str | None
    seed: int | None
    truncated: bool
    raw_response: str | None


class CodebookField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str
    context: bool = True


class CodebookSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    description: str | None = None
    system_prompt: str
    fields: list[CodebookField]


class _PayloadBase(BaseModel):
    """Shared base for the dynamically-built extraction payload model.

    Carries the ``mode="before"`` coercer that flattens common LLM
    type mismatches into the ``str | None`` contract every value /
    context field advertises. The coercer also drops unknown keys
    before pydantic sees them, so ``extra="forbid"`` would never fire
    here — left at pydantic's default.
    """

    @model_validator(mode="before")
    @classmethod
    def _coerce_llm_types(cls, data: Any) -> Any:
        """Coerce natural-JSON LLM responses into the ``str | None`` contract.

        LLMs frequently answer a string-typed field with a JSON bool or
        list. Rather than fail the whole record on a type mismatch,
        flatten to string at ingest. ``None`` and empty lists pass
        through as ``None``. Unknown keys are dropped here rather than
        hitting ``extra="forbid"`` — LLMs at t>0 routinely sprinkle
        extras like ``"confidence"`` or ``"notes"``, and those should
        not nuke the whole record.
        """
        if not isinstance(data, dict):
            return data
        known = set(cls.model_fields)
        coerced: dict[str, Any] = {}
        for key, value in data.items():
            if key not in known:
                continue
            if value is None or isinstance(value, str):
                coerced[key] = value
            elif isinstance(value, bool):  # before int (bool is int subclass)
                coerced[key] = "yes" if value else "no"
            elif isinstance(value, list):
                coerced[key] = (
                    " / ".join(str(item) for item in value) if value else None
                )
            else:
                coerced[key] = str(value)
        return coerced


def _default_context_description(name: str) -> str:
    return f"Short verbatim excerpt supporting {name}."


def load_codebook(
    spec: str | Path | dict[str, Any] | CodebookSpec,
) -> tuple[CodebookSpec, type[BaseModel]]:
    """Build the extraction-payload pydantic model from a codebook spec.

    Returns ``(spec, payload_model)``. ``payload_model`` is a dynamic
    subclass of ``_PayloadBase``: each ``CodebookField`` produces a
    ``str | None`` field whose description comes from the spec, and
    each ``context: true`` field also produces a paired
    ``<name>_context: str | None`` whose description is the stock
    "Short verbatim excerpt supporting {name}." line.
    """
    if isinstance(spec, CodebookSpec):
        parsed = spec
    elif isinstance(spec, dict):
        parsed = CodebookSpec.model_validate(spec)
    else:
        parsed = CodebookSpec.model_validate(resolve_yaml_arg(spec))

    field_defs: dict[str, Any] = {}
    for f in parsed.fields:
        field_defs[f.name] = (
            str | None,
            Field(default=None, description=f.description),
        )
        if f.context:
            field_defs[f"{f.name}_context"] = (
                str | None,
                Field(
                    default=None,
                    description=_default_context_description(f.name),
                ),
            )

    payload_model = create_model(
        "ExtractionPayload",
        __base__=_PayloadBase,
        **field_defs,
    )
    return parsed, payload_model


def render_field_list(payload_model: type[BaseModel]) -> str:
    """Render the codebook field list as the LLM-facing description block.

    One ``- "name": description`` line per field; preserves the field
    definition order from the model schema.
    """
    schema = payload_model.model_json_schema()
    properties: dict[str, dict[str, Any]] = schema.get("properties", {})
    lines: list[str] = []
    for name, info in properties.items():
        description = info.get("description")
        if description:
            lines.append(f'- "{name}": {description}')
        else:
            lines.append(f'- "{name}"')
    return "\n".join(lines)


def render_system_prompt(spec: CodebookSpec, payload_model: type[BaseModel]) -> str:
    """Substitute ``{fields}`` in the spec's system prompt with the field list."""
    return spec.system_prompt.format(fields=render_field_list(payload_model))


@dataclass(frozen=True)
class CodebookContext:
    """Bundle of derived codebook state threaded through the run.

    Built once at the top of ``run()`` from a ``CodebookSpec`` and a
    payload model; the cascade and ``extract_codebook`` consume it
    instead of taking four parallel kwargs.
    """

    spec: CodebookSpec
    payload_model: type[BaseModel]
    record_model: type[BaseModel]
    payload_field_names: tuple[str, ...]
    system_prompt: str

    @classmethod
    def from_spec(
        cls, spec: str | Path | dict[str, Any] | CodebookSpec
    ) -> "CodebookContext":
        parsed, payload_model = load_codebook(spec)
        record_model = build_record_model(payload_model)
        return cls(
            spec=parsed,
            payload_model=payload_model,
            record_model=record_model,
            payload_field_names=tuple(payload_model.model_fields),
            system_prompt=render_system_prompt(parsed, payload_model),
        )


def build_record_model(payload_model: type[BaseModel]) -> type[BaseModel]:
    """Compose the per-work ``ExtractionRecord`` class around ``payload_model``.

    Adds the identification block (``work_id``, ``source_basis``,
    ``reason``, ``seed``, ``truncated``, ``raw_response``) on top of
    the payload model's content fields. Keeping the record class
    dynamic — built once at startup from the loaded codebook — means
    record validation tracks the codebook spec without a class-edit.
    """
    return create_model(
        "ExtractionRecord",
        __base__=payload_model,
        work_id=(str, ...),
        source_basis=(SourceBasis, ...),
        reason=(str | None, ...),
        seed=(int | None, ...),
        truncated=(bool, ...),
        raw_response=(str | None, None),
    )
