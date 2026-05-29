"""Backfill missing abstracts by DOI: Semantic Scholar -> OpenAlex -> Crossref.

Reads the deduplicated catalogue and, for each ``Work`` with ``abstract is
None`` and a DOI, looks one up across the three sources in order, stopping at
the first non-empty result.  Writes an ``AbstractRecord`` sidecar keyed by
work id plus an ``AbstractLookupMeta`` of counters.  The catalogue file is
never rewritten in place; the abstract is joined in downstream.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx

from laglitsynth.abstract_lookup.models import (
    SOURCE_CROSSREF,
    SOURCE_OPENALEX,
    SOURCE_SEMANTIC_SCHOLAR,
    TOOL_NAME,
    AbstractLookupMeta,
    AbstractRecord,
)
from laglitsynth.abstract_lookup.sources import (
    fetch_crossref,
    fetch_openalex,
    fetch_semantic_scholar,
)
from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.dotenv import load_env_var
from laglitsynth.io import JsonlReadStats, read_jsonl, write_jsonl, write_meta
from laglitsynth.models import RunMeta

logger = logging.getLogger(__name__)

# A source function: (doi, client, email, api_key) -> abstract or None.
_SourceFn = Callable[..., "str | None"]

# Sources tried in order; first non-empty abstract wins.
_SOURCES: tuple[tuple[str, _SourceFn], ...] = (
    (SOURCE_SEMANTIC_SCHOLAR, fetch_semantic_scholar),
    (SOURCE_OPENALEX, fetch_openalex),
    (SOURCE_CROSSREF, fetch_crossref),
)


def lookup_abstract(
    doi: str,
    *,
    client: httpx.Client,
    email: str,
    api_key: str | None = None,
) -> tuple[str, str] | None:
    """Try each source in order; return ``(abstract, source)`` or ``None``.

    Stops at the first source returning a non-empty abstract.  A source that
    raises (transport error, unexpected payload) is logged and skipped so one
    flaky source never aborts the cascade.  ``None`` is returned only when no
    source produced an abstract.
    """
    for source, fetch in _SOURCES:
        try:
            abstract = fetch(doi, client=client, email=email, api_key=api_key)
        except Exception as exc:  # noqa: BLE001 - one flaky source must not abort
            logger.debug("Source %s failed for %s: %s", source, doi, exc)
            continue
        if abstract:
            return abstract, source
    return None


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "abstract-lookup",
        help="Backfill missing abstracts by DOI (Semantic Scholar -> "
        "OpenAlex -> Crossref).",
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Deduplicated catalogue JSONL "
        "(data/catalogue-dedup/deduplicated.jsonl)",
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="Output directory"
    )
    parser.add_argument(
        "--email",
        default=None,
        help=(
            "Contact email sent as the Crossref / Semantic Scholar polite "
            "contact. Falls back to ABSTRACT_LOOKUP_EMAIL in .env when omitted."
        ),
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help=(
            "Semantic Scholar API key (raises rate limits). Falls back to "
            "SEMANTIC_SCHOLAR_API_KEY in .env when omitted."
        ),
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Re-read an existing sidecar and only look up works still missing "
        "an abstract.",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    email: str | None = args.email
    if email is None:
        email = load_env_var("ABSTRACT_LOOKUP_EMAIL")
        if email is not None:
            print("Loaded ABSTRACT_LOOKUP_EMAIL from .env", file=sys.stderr)
    if email is None:
        raise SystemExit(
            "ABSTRACT_LOOKUP_EMAIL not set: pass --email or add "
            "ABSTRACT_LOOKUP_EMAIL=... to .env"
        )

    api_key: str | None = args.api_key
    if api_key is None:
        api_key = load_env_var("SEMANTIC_SCHOLAR_API_KEY")
        if api_key is not None:
            print("Loaded SEMANTIC_SCHOLAR_API_KEY from .env", file=sys.stderr)

    input_path: Path = args.input
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    sidecar_path = output_dir / "abstracts.jsonl"

    t0 = time.monotonic()
    stats = JsonlReadStats()

    works = list(read_jsonl(input_path, Work, stats))
    input_count = len(works)

    # Under --skip-existing, carry forward records that already resolved an
    # abstract; only works still missing one are looked up again.
    existing: dict[str, AbstractRecord] = {}
    if args.skip_existing and sidecar_path.exists():
        for rec in read_jsonl(sidecar_path, AbstractRecord):
            existing[rec.work_id] = rec
        already = sum(1 for r in existing.values() if r.abstract is not None)
        if already:
            print(
                f"Skipping {already} works with an abstract already resolved.",
                file=sys.stderr,
            )

    client = httpx.Client(timeout=30.0)
    records: list[AbstractRecord] = []
    no_doi_count = 0
    by_source: dict[str, int] = {}

    try:
        for i, work in enumerate(works, 1):
            prior = existing.get(work.id)
            if prior is not None and prior.abstract is not None:
                records.append(prior)
                if prior.source is not None:
                    by_source[prior.source] = by_source.get(prior.source, 0) + 1
                continue

            # Already has an abstract in the catalogue: nothing to look up.
            if work.abstract is not None:
                records.append(
                    AbstractRecord(
                        work_id=work.id, abstract=work.abstract, source=None
                    )
                )
                continue

            if work.doi is None or not work.doi.strip():
                no_doi_count += 1
                records.append(AbstractRecord(work_id=work.id))
                continue

            result = lookup_abstract(
                work.doi, client=client, email=email, api_key=api_key
            )
            if result is None:
                records.append(AbstractRecord(work_id=work.id))
            else:
                abstract, source = result
                records.append(
                    AbstractRecord(
                        work_id=work.id, abstract=abstract, source=source
                    )
                )
                by_source[source] = by_source.get(source, 0) + 1

            if i % 25 == 0 or i == input_count:
                print(f"  [{i}/{input_count}] processed", file=sys.stderr)
    finally:
        client.close()

    write_jsonl(records, sidecar_path)

    filled_count = sum(1 for r in records if r.source is not None)
    still_missing_count = sum(1 for r in records if r.abstract is None)

    run_meta = RunMeta(
        tool=TOOL_NAME,
        run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        validation_skipped=stats.skipped,
    )
    write_meta(
        output_dir / "abstract-lookup-meta.json",
        AbstractLookupMeta(
            run=run_meta,
            input_path=str(input_path),
            input_count=input_count,
            filled_count=filled_count,
            still_missing_count=still_missing_count,
            no_doi_count=no_doi_count,
            by_source=by_source,
        ),
    )

    elapsed = time.monotonic() - t0
    print(
        f"Abstract lookup done: {input_count} in, {filled_count} filled,"
        f" {still_missing_count} still missing, {no_doi_count} without DOI."
        f" ({elapsed:.1f}s)",
        file=sys.stderr,
    )
