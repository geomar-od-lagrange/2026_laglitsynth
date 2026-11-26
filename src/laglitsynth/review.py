"""Per-review config model and the ``review-config`` emitter subcommand.

A *review config* is one handwritten YAML that captures the
review-defining knobs the runner ([scripts/run-pipeline.sh]) needs:
the query, the year window and record cap, the three criteria/codebook
files, the screening threshold, the LLM endpoint URLs, the per-stage
models with stage 8's reasoning effort, and concurrency. The API key is never part of it. It is distinct from the per-stage ``config.yaml``
run-snapshots written by the LLM stages (see ``laglitsynth.config`` and
[docs/configs.md]) — those record one resolved stage invocation; this
describes a whole review up front and drives the runner.

The ``review-config`` subcommand validates such a file against the
typed :class:`ReviewConfig` and prints one ``CFG_<NAME>=<value>`` line
per knob to stdout, shell-quoted so the runner can ``eval`` the block.
Every optional field that is ``None`` (absent in the YAML) emits an
empty string; the runner then supplies its own built-in default via
``VAR="${VAR:-${CFG_VAR:-default}}"`` precedence (env > config >
default). Following "None means None", an absent knob is not invented
here — it is left empty for the runner to fill.
"""

from __future__ import annotations

import argparse
import shlex
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

# Criteria/codebook fields whose relative values resolve against the
# config file's own directory (mirrors ``config.load_config``).
_PATH_FIELDS = ("screening_criteria", "eligibility_criteria", "codebook")


class _Models(BaseModel):
    model_config = ConfigDict(extra="forbid")
    screening: str | None = None
    eligibility: str | None = None
    extraction: str | None = None
    extraction_reasoning_effort: Literal["low", "medium", "high"] | None = None


class _Concurrency(BaseModel):
    model_config = ConfigDict(extra="forbid")
    llm: int | None = None
    extraction: int | None = None


class ReviewConfig(BaseModel):
    """The review-defining knobs the runner reads from one YAML.

    Every optional field means "fall back to the runner's built-in
    default" when absent — the emitter leaves it empty rather than
    inventing a value here.
    """

    model_config = ConfigDict(extra="forbid")

    query: str
    from_year: int | None = None
    to_year: int | None = None
    max_records: int | None = None
    screening_criteria: Path | None = None
    eligibility_criteria: Path | None = None
    codebook: Path | None = None
    screening_threshold: float | None = None
    llm_base_url: str | None = None
    llm_decisions_url: str | None = None
    models: _Models = _Models()
    concurrency: _Concurrency = _Concurrency()


def load_review_config(path: Path) -> ReviewConfig:
    """Load + validate a review YAML, resolving criteria paths.

    The three criteria/codebook paths are resolved against the config
    file's own directory (mirroring ``config.load_config``) so a config
    can sit alongside the YAMLs it references regardless of CWD.
    """
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise ValueError(
            f"{path}: expected a top-level mapping, got {type(loaded).__name__}"
        )
    config = ReviewConfig.model_validate(loaded)
    base = path.resolve().parent
    for field in _PATH_FIELDS:
        value = getattr(config, field)
        if value is not None and not value.is_absolute():
            setattr(config, field, (base / value).resolve())
    return config


def _shell_value(value: object) -> str:
    """Render a config value as a shell-quoted assignment RHS.

    ``None`` becomes the empty string (the runner supplies the
    default); everything else is stringified and ``shlex.quote``-d so
    queries with spaces or quotes survive ``eval``.
    """
    if value is None:
        return "''"
    return shlex.quote(str(value))


def emit_cfg_lines(config: ReviewConfig) -> list[str]:
    """Return the ``CFG_<NAME>=<value>`` lines for one review config.

    The name set is stable and documented (see [docs/configs.md]); the
    runner reads exactly these. Order is fixed for deterministic output.
    """
    pairs: list[tuple[str, object]] = [
        ("CFG_QUERY", config.query),
        ("CFG_FROM_YEAR", config.from_year),
        ("CFG_TO_YEAR", config.to_year),
        ("CFG_MAX_RECORDS", config.max_records),
        ("CFG_SCREENING_CRITERIA", config.screening_criteria),
        ("CFG_ELIGIBILITY_CRITERIA", config.eligibility_criteria),
        ("CFG_CODEBOOK", config.codebook),
        ("CFG_SCREENING_THRESHOLD", config.screening_threshold),
        ("CFG_LLM_BASE_URL", config.llm_base_url),
        ("CFG_LLM_DECISIONS_URL", config.llm_decisions_url),
        ("CFG_SCREENING_MODEL", config.models.screening),
        ("CFG_ELIGIBILITY_MODEL", config.models.eligibility),
        ("CFG_EXTRACTION_MODEL", config.models.extraction),
        ("CFG_EXTRACTION_REASONING_EFFORT", config.models.extraction_reasoning_effort),
        ("CFG_LLM_CONCURRENCY", config.concurrency.llm),
        ("CFG_EXTRACTION_CONCURRENCY", config.concurrency.extraction),
    ]
    return [f"{name}={_shell_value(value)}" for name, value in pairs]


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "review-config",
        help=(
            "Validate a per-review config YAML and print CFG_* assignments "
            "for the runner to eval."
        ),
    )
    parser.add_argument(
        "path",
        type=Path,
        help="Per-review config YAML (see examples/reviews/).",
    )
    parser.set_defaults(run=_run_cli)
    return parser


def _run_cli(args: argparse.Namespace) -> None:
    config = load_review_config(args.path)
    for line in emit_cfg_lines(config):
        print(line)
