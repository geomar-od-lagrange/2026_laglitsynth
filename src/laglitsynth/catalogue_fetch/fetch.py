"""Fetch publications from OpenAlex and write to JSONL."""

from __future__ import annotations

import argparse
import logging
import re
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pyalex
from pydantic import ValidationError

from laglitsynth.io import JsonlReadStats, write_jsonl, write_meta
from laglitsynth.catalogue_fetch.models import TOOL_NAME, FetchMeta, Work
from laglitsynth.models import RunMeta

logger = logging.getLogger(__name__)

FIELDS = (
    "id",
    "doi",
    "title",
    "type",
    "publication_year",
    "publication_date",
    "language",
    "authorships",
    "biblio",
    "primary_location",
    "open_access",
    "cited_by_count",
    "referenced_works",
    "updated_date",
    "keywords",
    "topics",
    "primary_topic",
    "abstract_inverted_index",
    "is_retracted",
)


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str | None:
    """Reconstruct plain-text abstract from OpenAlex inverted index format."""
    if not inverted_index:
        return None
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(word for _, word in word_positions)


def _slugify(query: str) -> str:
    """Convert query string to a filesystem-safe slug."""
    return re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")


def _default_output_path(query: str) -> Path:
    """Generate default output path with timestamped filename."""
    slug = _slugify(query)
    ts = datetime.now(UTC).isoformat(timespec="microseconds").replace(":", "-")
    return Path("data/catalogue-fetch") / f"{slug}_{ts}.jsonl"


def search_openalex(
    query: str,
    *,
    from_year: int | None = None,
    to_year: int | None = None,
    max_results: int | None = None,
    stats: JsonlReadStats | None = None,
) -> Iterator[Work]:
    """Search OpenAlex for works matching a query and yield validated Work models.

    Validation failures are logged and counted in `stats.skipped` (if provided).
    """
    works_query = pyalex.Works().search(query)

    filters: dict[str, str] = {}
    if from_year is not None:
        filters["from_publication_date"] = f"{from_year}-01-01"
    if to_year is not None:
        filters["to_publication_date"] = f"{to_year}-12-31"
    if filters:
        works_query = works_query.filter(**filters)

    works_query = works_query.select(",".join(FIELDS))

    total = works_query.count()
    print(f"Query matches {total} works.", file=sys.stderr)

    yielded = 0

    for page in works_query.paginate(per_page=200, n_max=max_results):
        for raw in page:
            if max_results is not None and yielded >= max_results:
                return

            abstract_index = raw.pop("abstract_inverted_index", None)
            raw["abstract"] = _reconstruct_abstract(abstract_index)

            try:
                work = Work(**raw)
            except ValidationError as exc:
                work_id = raw.get("id", "<unknown>")
                logger.warning("Skipping invalid record %s: %s", work_id, exc)
                if stats is not None:
                    stats.skipped += 1
                continue

            yield work
            yielded += 1

            if yielded % 500 == 0:
                print(
                    f"  fetched {yielded} / {total} ...",
                    file=sys.stderr,
                )


def _preflight() -> None:
    pyalex.config.max_retries = 3
    pyalex.config.retry_backoff_factor = 0.5


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "catalogue-fetch",
        help="Fetch publications from OpenAlex and save as JSONL.",
    )
    parser.add_argument("query", help="Search query string")
    parser.add_argument("-o", "--output", type=Path, help="Output JSONL path")
    parser.add_argument(
        "--from-year", type=int, help="Filter publications from this year onward"
    )
    parser.add_argument(
        "--to-year", type=int, help="Filter publications up to this year"
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Maximum number of results to fetch. Omit to fetch all matching works.",
    )
    parser.add_argument(
        "--api-key",
        required=True,
        help="OpenAlex API key (register at https://openalex.org/settings/api).",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    _preflight()

    pyalex.config.api_key = args.api_key

    output = args.output or _default_output_path(args.query)
    meta_path = output.with_suffix(".meta.json")

    print(f"Output: {output}", file=sys.stderr)
    t0 = time.monotonic()

    fetch_stats = JsonlReadStats()
    works_iter = search_openalex(
        args.query,
        from_year=args.from_year,
        to_year=args.to_year,
        max_results=args.max_records,
        stats=fetch_stats,
    )

    count = write_jsonl(works_iter, output)
    elapsed = time.monotonic() - t0
    file_size = output.stat().st_size

    run_meta = RunMeta(
        tool=TOOL_NAME,
        run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        validation_skipped=fetch_stats.skipped,
    )
    write_meta(
        meta_path,
        FetchMeta(
            run=run_meta,
            query=args.query,
            total_count=count,
            records_written=count,
        ),
    )

    print(
        f"Done: {count} records, {file_size / 1024:.1f} KiB, {elapsed:.1f}s elapsed.",
        file=sys.stderr,
    )
