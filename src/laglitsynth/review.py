"""Per-review config model and the ``review-config`` emitter subcommand.

A *review config* is one handwritten YAML that captures the
review-defining knobs the runner ([scripts/run-pipeline.sh]) needs:
the query, the year window and record cap, the three criteria/codebook
files, and the per-stage thresholds, models, context windows, and
concurrency. It is distinct from the per-stage ``config.yaml``
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

import yaml
from pydantic import BaseModel, ConfigDict

# Criteria/codebook fields whose relative values resolve against the
# config file's own directory (mirrors ``config.load_config``).
_PATH_FIELDS = ("screening_criteria", "eligibility_criteria", "codebook")


class _Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    retrieval: float | None = None
    eligibility: float | None = None


class _Models(BaseModel):
    model_config = ConfigDict(extra="forbid")
    screening: str | None = None
    eligibility: str | None = None
    extraction: str | None = None


class _NumCtx(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligibility: int | None = None
    extraction: int | None = None


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
    thresholds: _Thresholds = _Thresholds()
    models: _Models = _Models()
    num_ctx: _NumCtx = _NumCtx()
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
        ("CFG_RETRIEVAL_THRESHOLD", config.thresholds.retrieval),
        ("CFG_ELIGIBILITY_THRESHOLD", config.thresholds.eligibility),
        ("CFG_SCREENING_MODEL", config.models.screening),
        ("CFG_ELIGIBILITY_MODEL", config.models.eligibility),
        ("CFG_EXTRACTION_MODEL", config.models.extraction),
        ("CFG_ELIGIBILITY_NUM_CTX", config.num_ctx.eligibility),
        ("CFG_EXTRACTION_NUM_CTX", config.num_ctx.extraction),
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
