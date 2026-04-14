"""Fetch publications from OpenAlex and write to JSONL."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pyalex
from dotenv import load_dotenv
from pydantic import ValidationError

from laglitsynth.openalex.models import Work

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
    return Path("data/openalex") / f"{slug}_{ts}.jsonl"


def search_openalex(
    query: str,
    *,
    from_year: int | None = None,
    to_year: int | None = None,
    max_results: int | None = None,
) -> Iterator[Work]:
    """Search OpenAlex for works matching a query and yield validated Work models."""
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
                continue

            yield work
            yielded += 1

            if yielded % 500 == 0:
                print(
                    f"  fetched {yielded} / {total} ...",
                    file=sys.stderr,
                )


def write_jsonl(works: Iterator[Work], output: Path) -> int:
    """Write Work records to a JSONL file. Returns count of records written."""
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(output, "x") as f:
        for work in works:
            f.write(work.model_dump_json())
            f.write("\n")
            count += 1
    return count


def _write_metadata(
    meta_path: Path,
    *,
    query: str,
    fetched_at: str,
    total_count: int,
    records_written: int,
) -> None:
    """Write sidecar metadata JSON file."""
    meta = {
        "tool": "laglitsynth.openalex.fetch",
        "tool_version": "alpha",
        "query": query,
        "fetched_at": fetched_at,
        "total_count": total_count,
        "records_written": records_written,
    }
    with open(meta_path, "w") as f:
        json.dump(meta, f, indent=2)
        f.write("\n")


def main(argv: list[str] | None = None) -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Fetch publications from OpenAlex and save as JSONL."
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
        "--max-results",
        type=int,
        default=None,
        help="Maximum number of results to fetch (default: 199)",
    )
    args = parser.parse_args(argv)

    max_results_defaulted = args.max_results is None
    if max_results_defaulted:
        args.max_results = 199
    max_results_warning = (
        "Warning: --max-results not set, defaulting to 199. "
        "Pass --max-results explicitly to fetch more."
    )
    if max_results_defaulted:
        print(max_results_warning, file=sys.stderr)

    api_key = os.environ.get("OPENALEX_API_KEY")
    if not api_key:
        print(
            "Error: OPENALEX_API_KEY environment variable is not set.\n"
            "Register at https://openalex.org/settings/api to get a free key.",
            file=sys.stderr,
        )
        sys.exit(1)

    pyalex.config.api_key = api_key
    pyalex.config.max_retries = 3
    pyalex.config.retry_backoff_factor = 0.5

    output = args.output or _default_output_path(args.query)
    meta_path = output.with_suffix(".meta.json")

    if output.exists():
        print(f"Error: output file already exists: {output}", file=sys.stderr)
        sys.exit(1)

    print(f"Output: {output}", file=sys.stderr)
    t0 = time.monotonic()

    works_iter = search_openalex(
        args.query,
        from_year=args.from_year,
        to_year=args.to_year,
        max_results=args.max_results,
    )

    count = write_jsonl(works_iter, output)
    elapsed = time.monotonic() - t0
    file_size = output.stat().st_size

    _write_metadata(
        meta_path,
        query=args.query,
        fetched_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        total_count=count,
        records_written=count,
    )

    print(
        f"Done: {count} records, {file_size / 1024:.1f} KiB, {elapsed:.1f}s elapsed.",
        file=sys.stderr,
    )

    if max_results_defaulted:
        print(max_results_warning, file=sys.stderr)


if __name__ == "__main__":
    main()
