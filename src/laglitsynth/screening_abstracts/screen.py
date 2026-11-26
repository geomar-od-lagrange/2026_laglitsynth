"""Abstract screening on the decisions endpoint.

Each work's title/authors/year/abstract listing is sent as the ``input``
of one decision whose predicate question comes from the screening-criteria
YAML's ``decision`` block. The answer's probability that the work is
relevant is stored as ``relevance_score = round(100 * p)``.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.concurrency import map_concurrent
from laglitsynth.config import register_config_arg, save_resolved_config
from laglitsynth.io import (
    JsonlReadStats,
    append_jsonl,
    read_jsonl,
    write_meta,
)
from laglitsynth.layout import (
    SCREENING_SUBDIR,
    deduplicated_path,
    require_input,
    warn_existing_run_dir,
)
from laglitsynth.llm import (
    LlmEndpoint,
    add_endpoint_args,
    decide,
    decisions_client,
    resolve_endpoint,
)
from laglitsynth.manifest import resolve_run_id
from laglitsynth.models import RunMeta
from laglitsynth.prompts import DecisionQuestion, load_decision_question
from laglitsynth.screening_abstracts.models import TOOL_NAME, ScreeningMeta, ScreeningVerdict

DEFAULT_SCREENING_CRITERIA = Path(
    "examples/screening-criteria/lagrangian-oceanography.yaml"
)

# Answer name of the screening predicate question.
QUESTION_NAME = "relevant"

logger = logging.getLogger(__name__)

# Marker for the work-listing render sent as the decision input. Folded
# into prompt_sha256 so the hash pins both the question and the shape of
# the input.
USER_TEMPLATE = "title/authors/year/abstract"


def format_screening_input(work: Work) -> str:
    """Render the title/authors/year/abstract block sent as the input.

    Title and publication_year render as ``<unknown>`` when None;
    an empty author list renders as ``<unknown>``. The abstract is
    expected to be present — caller skips works with no abstract.
    """
    title = work.title if work.title is not None else "<unknown>"
    year = (
        str(work.publication_year)
        if work.publication_year is not None
        else "<unknown>"
    )
    authors_list = [a.author.display_name for a in work.authorships]
    authors = ", ".join(authors_list) if authors_list else "<unknown>"
    abstract = work.abstract if work.abstract is not None else ""
    return (
        f"Title: {title}\n"
        f"Authors: {authors}\n"
        f"Year: {year}\n"
        f"Abstract: {abstract}"
    )


def _no_abstract_verdict(work_id: str) -> ScreeningVerdict:
    return ScreeningVerdict(work_id=work_id, relevance_score=None, reason="no-abstract")


def _load_works(input_path: Path, max_records: int | None) -> list[Work]:
    works: list[Work] = []
    for idx, work in enumerate(read_jsonl(input_path, Work)):
        if max_records is not None and idx >= max_records:
            break
        works.append(work)
    return works


def decide_works(
    input_path: Path,
    question: DecisionQuestion,
    *,
    endpoint: LlmEndpoint,
    max_records: int | None,
    concurrency: int = 1,
) -> Iterator[ScreeningVerdict]:
    """Yield a verdict per input work.

    Works without an abstract yield a ``no-abstract`` sentinel first, with
    no request. The others are decided through ``map_concurrent`` with
    ``concurrency`` workers and yielded in completion order.
    """
    works = _load_works(input_path, max_records)
    with_abstract: list[Work] = []
    for work in works:
        if work.abstract is None:
            logger.warning("Skipping work %s: no abstract", work.id)
            yield _no_abstract_verdict(work.id)
        else:
            with_abstract.append(work)

    client = decisions_client(endpoint)
    try:

        def _decide_one(work: Work) -> ScreeningVerdict:
            decision = decide(
                client,
                endpoint,
                input=format_screening_input(work),
                name=QUESTION_NAME,
                question=question,
            )
            score = None if decision.probability is None else round(100 * decision.probability)
            return ScreeningVerdict(
                work_id=work.id,
                relevance_score=score,
                reason=decision.reason,
                raw_response=decision.raw_response,
            )

        yield from map_concurrent(_decide_one, with_abstract, max_workers=concurrency)
    finally:
        client.close()


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "screening-abstracts",
        help="Score JSONL works for abstract relevance on the decisions endpoint.",
    )
    parser.add_argument(
        "input",
        type=Path,
        nargs="?",
        default=None,
        help="Input JSONL file path (default: <data-dir>/catalogue-dedup/deduplicated.jsonl).",
    )
    parser.add_argument(
        "--screening-criteria",
        type=Path,
        default=DEFAULT_SCREENING_CRITERIA,
        help=(
            f"Screening-criteria YAML carrying a 'decision' block (the "
            f"relevance question). On reload from a saved snapshot, the "
            f"inlined mapping is consumed directly "
            f"(default: {DEFAULT_SCREENING_CRITERIA})."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Bucket root for stage outputs (default: data/)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Run identifier (default: the run_id in <data-dir>/manifest.json).",
    )
    add_endpoint_args(parser, decisions=True)
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Process only the first N works",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print verdicts to stderr without writing output",
    )
    register_config_arg(parser)
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    data_dir: Path = Path(args.data_dir)
    args.run_id = resolve_run_id(data_dir, args.run_id)
    args.input = require_input(
        args.input if args.input is not None else deduplicated_path(data_dir),
        flag="the input path",
        upstream="catalogue-dedup",
    )
    endpoint = resolve_endpoint(args)
    # Pass the un-resolved spec so a missing block names the file.
    try:
        question = load_decision_question(args.screening_criteria)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    output_dir: Path = data_dir / SCREENING_SUBDIR / args.run_id
    verdicts_path = output_dir / "verdicts.jsonl"
    meta_path = output_dir / "screening-meta.json"
    if not args.dry_run:
        warn_existing_run_dir(output_dir, "verdicts.jsonl")

    prompt_sha256 = hashlib.sha256(
        (question.canonical_json() + "\n" + USER_TEMPLATE).encode("utf-8")
    ).hexdigest()
    llm_meta = endpoint.llm_meta(prompt_sha256)

    stats = JsonlReadStats()
    total = sum(1 for _ in read_jsonl(args.input, Work, stats))

    print(f"Screening {total} works with model {endpoint.model}", file=sys.stderr)
    if not args.dry_run:
        print(f"Output dir: {output_dir}", file=sys.stderr)

    # Clean-rerun: truncate any prior verdicts file before streaming
    # append. Resume is explicitly not supported — see
    # docs/llm-concurrency.md.
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        save_resolved_config(args, output_dir, inlines=["screening_criteria"])
        verdicts_path.unlink(missing_ok=True)

    counts = {"llm-parse-failure": 0, "llm-timeout": 0, "llm-rejected": 0}

    def _build_meta(*, scored: int, skipped: int) -> ScreeningMeta:
        return ScreeningMeta(
            run=RunMeta(
                tool=TOOL_NAME,
                run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
                validation_skipped=stats.skipped,
            ),
            llm=llm_meta,
            input_path=str(args.input),
            input_count=total,
            scored_count=scored,
            skipped_count=skipped,
            llm_parse_failure_count=counts["llm-parse-failure"],
            llm_timeout_count=counts["llm-timeout"],
            llm_rejected_count=counts["llm-rejected"],
        )

    # Write meta upfront so a mid-run reader sees the LLM fingerprint.
    # Counts start at zero and get rewritten at the end.
    if not args.dry_run:
        write_meta(meta_path, _build_meta(scored=0, skipped=0))

    t0 = time.monotonic()
    scored_count = 0
    skipped_count = 0
    index = 0

    for verdict in decide_works(
        args.input,
        question,
        endpoint=endpoint,
        max_records=args.max_records,
        concurrency=args.concurrency,
    ):
        index += 1

        if verdict.relevance_score is None:
            skipped_count += 1
            if verdict.reason in counts:
                counts[verdict.reason] += 1
            print(
                f"  [{index}/{total}] skipped ({verdict.reason})"
                f" — {verdict.work_id[-12:]}",
                file=sys.stderr,
            )
        else:
            scored_count += 1
            print(
                f"  [{index}/{total}] score {verdict.relevance_score}"
                f" — {verdict.work_id[-12:]}",
                file=sys.stderr,
            )

        if not args.dry_run:
            append_jsonl(verdict, verdicts_path)

    elapsed = time.monotonic() - t0

    print(
        f"\nDone in {elapsed:.1f}s: {scored_count} scored, {skipped_count} skipped.",
        file=sys.stderr,
    )

    if not args.dry_run:
        write_meta(meta_path, _build_meta(scored=scored_count, skipped=skipped_count))
        print(f"Run dir: {output_dir}", file=sys.stderr)
