"""Pass-through adjudication MVP."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.screening_adjudication.models import TOOL_NAME, AdjudicationMeta, AdjudicationVerdict
from laglitsynth.screening_abstracts.models import ScreeningVerdict
from laglitsynth.io import JsonlReadStats, read_jsonl, write_jsonl, write_meta
from laglitsynth.models import RunMeta

logger = logging.getLogger(__name__)


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "screening-adjudication",
        help="Adjudicate screening verdicts (pass-through MVP).",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Stage 3 verdicts JSONL file (data/screening-abstracts/verdicts.jsonl)",
    )
    parser.add_argument(
        "--catalogue",
        type=Path,
        required=True,
        help="Deduplicated catalogue JSONL (data/catalogue-dedup/deduplicated.jsonl)",
    )
    parser.add_argument(
        "--screening-threshold",
        type=int,
        default=50,
        help="Relevance score cutoff, 0-100 (default: 50)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/screening-adjudication"),
        help="Output directory (default: data/screening-adjudication/)",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    threshold: int = args.screening_threshold

    t0 = time.monotonic()

    stats = JsonlReadStats()
    # Load catalogue into a lookup dict by work_id
    works_by_id = {w.id: w for w in read_jsonl(args.catalogue, Work, stats)}

    # Load stage-3 verdicts
    verdicts = list(read_jsonl(args.input, ScreeningVerdict, stats))
    input_count = len(verdicts)

    now = datetime.now(UTC).isoformat(timespec="microseconds")

    adj_verdicts: list[AdjudicationVerdict] = []
    accepted_works = []
    missing_in_catalogue = 0

    for verdict in verdicts:
        if verdict.relevance_score is None or verdict.relevance_score < threshold:
            continue
        work = works_by_id.get(verdict.work_id)
        if work is None:
            missing_in_catalogue += 1
            logger.warning(
                "Verdict above threshold has no matching work in catalogue: %s",
                verdict.work_id,
            )
            continue
        adj_verdicts.append(
            AdjudicationVerdict(
                work_id=verdict.work_id,
                decision="accept",
                reviewer="pass-through",
                adjudicated_at=now,
                reason=None,
            )
        )
        accepted_works.append(work)

    accepted_count = len(adj_verdicts)

    write_jsonl(adj_verdicts, output_dir / "verdicts.jsonl")
    write_jsonl(accepted_works, output_dir / "included.jsonl")

    run_meta = RunMeta(
        tool=TOOL_NAME,
        run_at=now,
        validation_skipped=stats.skipped,
    )
    write_meta(
        output_dir / "adjudication-meta.json",
        AdjudicationMeta(
            run=run_meta,
            threshold=threshold,
            input_count=input_count,
            accepted_count=accepted_count,
            # Pass-through MVP emits no reject/skip verdicts; real human
            # adjudication will populate this.
            rejected_count=0,
            missing_in_catalogue=missing_in_catalogue,
        ),
    )

    elapsed = time.monotonic() - t0
    print(
        f"Adjudication done: {input_count} verdicts in, {accepted_count} accepted, "
        f"{missing_in_catalogue} missing in catalogue "
        f"(threshold={threshold}), mode=pass-through. ({elapsed:.1f}s)",
        file=sys.stderr,
    )
