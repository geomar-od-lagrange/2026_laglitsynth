"""Pass-through adjudication MVP."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from laglitsynth.screening_adjudication.models import AdjudicationMeta
from laglitsynth.io import read_works_jsonl, write_jsonl, write_meta


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "screening-adjudication",
        help="Adjudicate screening verdicts (pass-through MVP).",
    )
    parser.add_argument("--input", type=Path, required=True, help="Input JSONL file")
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="Output directory"
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.monotonic()
    works = list(read_works_jsonl(args.input))
    input_count = len(works)

    count = write_jsonl(works, output_dir / "included.jsonl")

    write_meta(
        output_dir / "adjudication-meta.json",
        AdjudicationMeta(
            adjudicated_at=datetime.now(UTC).isoformat(timespec="microseconds"),
            input_count=input_count,
            output_count=count,
        ),
    )

    elapsed = time.monotonic() - t0
    print(
        f"Adjudication done: {input_count} in, {count} out, "
        f"mode=pass_through. ({elapsed:.1f}s)",
        file=sys.stderr,
    )
