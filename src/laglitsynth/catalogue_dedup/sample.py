"""Seeded random sample of a catalogue.

``catalogue-sample`` reads a catalogue JSONL and writes ``--n`` works chosen
with ``random.Random(seed)``, in catalogue order, plus a ``SampleMeta``
sidecar. The sample is an ordinary catalogue: downstream stages take it via
their ``--catalogue`` flag. ``--seed`` is required so the choice is always
recorded rather than defaulted.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import UTC, datetime
from pathlib import Path

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.catalogue_dedup.models import SAMPLE_TOOL_NAME, SampleMeta
from laglitsynth.io import JsonlReadStats, read_jsonl, write_jsonl, write_meta
from laglitsynth.layout import dedup_dir, deduplicated_path, require_input
from laglitsynth.models import RunMeta


def sample_works(works: list[Work], n: int, seed: int) -> list[Work]:
    """Return ``n`` works chosen by ``random.Random(seed)``, in input order.

    When ``n`` is at least the catalogue size every work is returned.
    """
    if n >= len(works):
        return list(works)
    chosen = set(random.Random(seed).sample(range(len(works)), n))
    return [w for i, w in enumerate(works) if i in chosen]


def sample_path(data_dir: Path, n: int, seed: int) -> Path:
    return dedup_dir(data_dir) / f"sample-{n}-seed{seed}.jsonl"


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "catalogue-sample",
        help="Write a seeded random sample of a catalogue as its own catalogue.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Catalogue JSONL (default: <data-dir>/catalogue-dedup/deduplicated.jsonl).",
    )
    parser.add_argument("--n", type=int, required=True, help="Sample size.")
    parser.add_argument(
        "--seed", type=int, required=True, help="Random seed; recorded in the meta."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output JSONL (default: <data-dir>/catalogue-dedup/sample-<n>-seed<seed>.jsonl).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Bucket root for stage outputs (default: data/)",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    data_dir: Path = Path(args.data_dir)
    input_path = require_input(
        args.input if args.input is not None else deduplicated_path(data_dir),
        flag="--input",
        upstream="catalogue-dedup",
    )
    if args.n < 1:
        raise SystemExit("--n must be at least 1")
    output: Path = (
        args.output if args.output is not None else sample_path(data_dir, args.n, args.seed)
    )

    stats = JsonlReadStats()
    works = list(read_jsonl(input_path, Work, stats))
    chosen = sample_works(works, args.n, args.seed)

    output.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(chosen, output)
    write_meta(
        output.with_suffix(".meta.json"),
        SampleMeta(
            run=RunMeta(
                tool=SAMPLE_TOOL_NAME,
                run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
                validation_skipped=stats.skipped,
            ),
            input_path=str(input_path),
            input_count=len(works),
            n=args.n,
            seed=args.seed,
            output_count=len(chosen),
        ),
    )
    print(
        f"Sampled {len(chosen)} of {len(works)} works (seed {args.seed}) to {output}",
        file=sys.stderr,
    )
