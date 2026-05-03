"""Pass-all deduplication MVP."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from laglitsynth.catalogue_dedup.models import TOOL_NAME, DeduplicationMeta
from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.io import JsonlReadStats, read_jsonl, write_jsonl, write_meta
from laglitsynth.models import RunMeta


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "catalogue-dedup",
        help="Deduplicate works (pass-all MVP).",
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
    stats = JsonlReadStats()
    works = list(read_jsonl(args.input, Work, stats))
    input_count = len(works)

    count = write_jsonl(works, output_dir / "deduplicated.jsonl")
    write_jsonl([], output_dir / "dropped.jsonl")

    run_meta = RunMeta(
        tool=TOOL_NAME,
        run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        validation_skipped=stats.skipped,
    )
    write_meta(
        output_dir / "dedup-meta.json",
        DeduplicationMeta(
            run=run_meta,
            input_count=input_count,
            output_count=count,
            duplicates_removed=0,
            by_rule={},
        ),
    )

    elapsed = time.monotonic() - t0
    print(
        f"Dedup done: {input_count} in, {count} out, 0 duplicates removed."
        f" ({elapsed:.1f}s)",
        file=sys.stderr,
    )
