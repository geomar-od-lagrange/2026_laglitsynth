"""Config-file loading and per-run snapshot writer.

Two distinct uses share the same file format:

- **Input configs** are user-authored or sweep-generated — handwritten
  YAML driving an invocation. They reference the codebook and
  eligibility-criteria files by path. ``--config <input.yaml>`` seeds
  argparse defaults; explicit CLI flags still win.
- **Run snapshots** are written automatically by every LLM stage into
  its run directory. The codebook and eligibility-criteria contents
  are inlined for audit value (so a run is interpretable a year later
  even if the original file has moved or been edited).

The two shapes coexist: input configs keep ``codebook:`` as a path
string; run snapshots contain a mapping. ``resolve_yaml_arg`` handles
both forms so the same code path works for either.

The dispatch sequence — sniff ``--config`` from argv in
``cli.main()``, apply via ``subparsers.choices[cmd].set_defaults``,
then ``parse_args`` — keeps argparse's normal precedence: explicit
CLI flag > set_defaults > add_argument default.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml


def load_config(path: Path) -> dict[str, Any]:
    """Read a YAML config file as a flat mapping of CLI-arg names to values.

    Relative-path values inside the file (any string ending in
    ``.yaml`` / ``.yml``) are resolved against the config file's
    directory so that an input config can sit alongside the YAMLs it
    references regardless of the user's CWD at invocation time.
    """
    path = Path(path)
    with path.open(encoding="utf-8") as fh:
        loaded: Any = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise ValueError(
            f"{path}: expected a top-level mapping, got {type(loaded).__name__}"
        )
    base = path.resolve().parent
    for key, value in list(loaded.items()):
        if isinstance(value, str) and value.lower().endswith((".yaml", ".yml")):
            candidate = Path(value)
            if not candidate.is_absolute():
                loaded[key] = str((base / candidate).resolve())
    return loaded


def register_config_arg(parser: argparse.ArgumentParser) -> None:
    """Register the ``--config`` flag on a stage's subparser.

    Loading + apply-as-defaults happens in ``cli.main`` once the
    active subcommand is known, so this helper only adds the flag —
    nothing reads ``sys.argv`` here.
    """
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="YAML config file whose values seed argparse defaults; explicit CLI flags override.",
    )


def resolve_yaml_arg(value: str | Path | dict[str, Any]) -> dict[str, Any]:
    """Coalesce a polymorphic file/inlined arg to a dict.

    A string or Path is treated as a path to a YAML file. A dict is
    passed through (already-inlined snapshot case).
    """
    if isinstance(value, dict):
        return value
    return load_config(Path(value))


_DROP_KEYS = frozenset({"config", "run", "command", "run_id"})


def save_resolved_config(
    args: argparse.Namespace,
    run_dir: Path,
    *,
    inlines: Iterable[str] = (),
) -> Path:
    """Write a fully-resolved ``config.yaml`` into ``run_dir``.

    Named entries in ``inlines`` are coerced to embedded mappings via
    ``resolve_yaml_arg`` (idempotent for already-inlined snapshots).
    Path values that are not in ``inlines`` are stringified. The
    ``--config`` flag, the dispatch ``run`` callable, the subcommand
    ``command`` field, and the runtime ``run_id`` are dropped — a
    rerun from a saved snapshot must always generate a fresh run_id
    rather than overwrite the original run dir.
    """
    inlines_set = set(inlines)
    payload: dict[str, Any] = {}
    for key, value in vars(args).items():
        if key in _DROP_KEYS or callable(value):
            continue
        if key in inlines_set:
            payload[key] = resolve_yaml_arg(value)
        elif isinstance(value, Path):
            payload[key] = str(value)
        else:
            payload[key] = value
    out = run_dir / "config.yaml"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        yaml.safe_dump(payload, fh, sort_keys=False)
    return out
