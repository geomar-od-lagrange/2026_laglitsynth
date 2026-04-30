"""CSV export of stage 3 verdicts for human spot-checking.

Joins ``verdicts.jsonl`` with the dedup catalogue and writes one
``review.csv`` a reviewer opens in Excel, Numbers, or Keynote. Two
empty columns (``reviewer_decision``, ``reviewer_reason``) are filled
in the spreadsheet; a stage-4 ingestor will read them back.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.io import read_jsonl, read_works_jsonl
from laglitsynth.screening_abstracts.models import ScreeningVerdict

COLUMNS: tuple[str, ...] = (
    "work_id",
    "title",
    "doi",
    "publication_year",
    "abstract",
    "relevance_score",
    "llm_reason",
    "reviewer_decision",
    "reviewer_reason",
    "raw_response",
)


def build_row(verdict: ScreeningVerdict, work: Work) -> dict[str, str]:
    """One CSV row. Missing values render as empty cells."""
    return {
        "work_id": verdict.work_id,
        "title": work.title or "",
        "doi": work.doi or "",
        "publication_year": (
            str(work.publication_year) if work.publication_year is not None else ""
        ),
        "abstract": work.abstract or "",
        "relevance_score": (
            str(verdict.relevance_score)
            if verdict.relevance_score is not None
            else ""
        ),
        "llm_reason": verdict.reason or "",
        "reviewer_decision": "",
        "reviewer_reason": "",
        "raw_response": verdict.raw_response or "",
    }


def export_review_csv(
    verdicts_path: Path,
    catalogue_path: Path,
    output_path: Path,
) -> int:
    """Write the review CSV and return the number of data rows.

    Raises ``ValueError`` naming the first ``work_id`` present in the
    verdicts file but absent from the catalogue.
    """
    catalogue: dict[str, Work] = {w.id: w for w in read_works_jsonl(catalogue_path)}
    rows: list[dict[str, str]] = []
    for verdict in read_jsonl(verdicts_path, ScreeningVerdict):
        work = catalogue.get(verdict.work_id)
        if work is None:
            raise ValueError(
                f"work_id {verdict.work_id!r} in {verdicts_path} not found in "
                f"catalogue {catalogue_path}"
            )
        rows.append(build_row(verdict, work))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "screening-abstracts-export",
        help="Export stage 3 verdicts + catalogue to a review CSV.",
    )
    parser.add_argument(
        "--verdicts",
        type=Path,
        required=True,
        help="Path to verdicts.jsonl from screening-abstracts",
    )
    parser.add_argument(
        "--catalogue",
        type=Path,
        required=True,
        help="Path to the deduplicated catalogue JSONL",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path (default: <verdicts parent>/review.csv)",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    verdicts_path: Path = args.verdicts
    catalogue_path: Path = args.catalogue
    output_path: Path = (
        args.output if args.output is not None else verdicts_path.parent / "review.csv"
    )
    count = export_review_csv(verdicts_path, catalogue_path, output_path)
    print(f"Wrote {count} rows to {output_path}", file=sys.stderr)
