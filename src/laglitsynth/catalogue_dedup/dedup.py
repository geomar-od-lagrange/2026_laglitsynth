"""Three-rule deduplication for the work catalogue."""

from __future__ import annotations

import argparse
import glob
import re
import sys
import time
import unicodedata
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from laglitsynth.catalogue_dedup.models import TOOL_NAME, DeduplicationMeta, DroppedRecord
from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.io import JsonlReadStats, read_jsonl, write_jsonl, write_meta
from laglitsynth.models import RunMeta

_DOI_PREFIXES = re.compile(
    r"^https?://(dx\.)?doi\.org/", re.IGNORECASE
)


def _normalise_doi(doi: str | None) -> str | None:
    """Lowercase and strip standard DOI URL prefixes."""
    if doi is None:
        return None
    normalised = _DOI_PREFIXES.sub("", doi.strip()).lower()
    return normalised if normalised else None


def _normalise_title(title: str | None) -> str | None:
    """Lowercase, replace all Unicode punctuation with spaces, collapse whitespace.

    Uses ``unicodedata.category`` to identify punctuation characters
    (category starting with "P") so that Unicode dashes (—, –) and
    curly quotes (" " ' ') are handled alongside ASCII punctuation.
    Punctuation is replaced with a space rather than deleted so that
    "Sea—Level" becomes "sea level" rather than "sealevel".
    """
    if title is None:
        return None
    lowered = title.lower()
    spaced = "".join(
        " " if unicodedata.category(c).startswith("P") else c for c in lowered
    )
    collapsed = " ".join(spaced.split())
    return collapsed if collapsed else None


def _first_author_lower(work: Work) -> str | None:
    """Return lowercased display_name of first authorship, or None."""
    if not work.authorships:
        return None
    return work.authorships[0].author.display_name.lower()


def _completeness_key(work: Work) -> tuple[int, int]:
    """Higher is more complete: (has_doi, authorship_count).

    A whitespace-only DOI string is treated as absent.
    """
    return (1 if work.doi and work.doi.strip() else 0, len(work.authorships))


def _iter_inputs(patterns: list[str]) -> Iterator[Path]:
    """Yield deduplicated paths matched by one or more glob patterns."""
    seen: set[Path] = set()
    for pattern in patterns:
        # Try as literal path first; if it doesn't exist, expand as glob.
        literal = Path(pattern)
        if literal.exists():
            if literal not in seen:
                seen.add(literal)
                yield literal
        else:
            for match in sorted(glob.glob(pattern)):
                p = Path(match)
                if p not in seen:
                    seen.add(p)
                    yield p


def deduplicate(
    works: list[Work],
) -> tuple[list[Work], list[DroppedRecord]]:
    """Apply the three dedup rules; return (survivors, dropped_records)."""

    # Maps from key → work_id of surviving record.
    by_id: dict[str, str] = {}
    by_doi: dict[str, str] = {}
    by_title: dict[tuple[str, str, int], str] = {}

    survivors: dict[str, Work] = {}  # work_id → Work (surviving records in order)
    dropped: list[DroppedRecord] = []

    def _title_author_year_key(
        w: Work,
    ) -> tuple[str, str, int] | None:
        title_norm = _normalise_title(w.title)
        author = _first_author_lower(w)
        if title_norm is None or author is None or w.publication_year is None:
            return None
        return (title_norm, author, w.publication_year)

    def _evict(existing_id: str, winning_id: str, rule: str) -> None:
        """Remove *existing_id* from all indexes and record it as dropped."""
        existing = survivors.pop(existing_id)
        by_id.pop(existing.id, None)
        existing_doi_norm = _normalise_doi(existing.doi)
        if existing_doi_norm:
            by_doi.pop(existing_doi_norm, None)
        existing_title_key = _title_author_year_key(existing)
        if existing_title_key:
            by_title.pop(existing_title_key, None)
        dropped.append(
            DroppedRecord(
                dropped_work_id=existing.id,
                survived_work_id=winning_id,
                rule=rule,
            )
        )

    def _replace_if_better(existing_id: str, challenger: Work, rule: str) -> None:
        """Compare challenger against existing survivor; swap if challenger is better.

        When the challenger wins, it is evicted-then-re-inserted via
        ``_insert``, which re-runs the full match cascade to catch any
        secondary collisions the challenger's DOI or title might create
        against *other* already-registered survivors.
        """
        existing = survivors[existing_id]
        if _completeness_key(challenger) > _completeness_key(existing):
            # Challenger wins: evict existing, then insert challenger properly.
            _evict(existing_id, challenger.id, rule)
            _insert(challenger)
        else:
            # Existing wins: challenger is dropped.
            dropped.append(
                DroppedRecord(
                    dropped_work_id=challenger.id,
                    survived_work_id=existing_id,
                    rule=rule,
                )
            )

    def _insert(w: Work) -> None:
        """Register *w* as a survivor, resolving any secondary collisions first.

        After a swap-survivor eviction, the challenger's DOI or title may
        collide with a *different* existing survivor.  We re-run rules 2 and 3
        here so those collisions are resolved correctly rather than silently
        overwriting index pointers.

        Rule 1 (same OpenAlex id) is deliberately skipped: the caller already
        verified no other survivor shares *w*'s id, or has evicted the one
        that did.
        """
        # Rule 2: DOI collision with a *different* survivor?
        doi_norm = _normalise_doi(w.doi)
        if doi_norm and doi_norm in by_doi and by_doi[doi_norm] != w.id:
            _replace_if_better(by_doi[doi_norm], w, "doi")
            return

        # Rule 3: title-key collision with a *different* survivor?
        title_key = _title_author_year_key(w)
        if title_key and title_key in by_title and by_title[title_key] != w.id:
            _replace_if_better(by_title[title_key], w, "title_author_year")
            return

        # No collisions: register directly.
        survivors[w.id] = w
        by_id[w.id] = w.id
        if doi_norm:
            by_doi[doi_norm] = w.id
        if title_key:
            by_title[title_key] = w.id

    for work in works:
        # Rule 1: exact OpenAlex ID match.
        if work.id in by_id:
            _replace_if_better(by_id[work.id], work, "openalex_id")
            continue

        # Rule 2: normalised DOI match.
        doi_norm = _normalise_doi(work.doi)
        if doi_norm and doi_norm in by_doi:
            _replace_if_better(by_doi[doi_norm], work, "doi")
            continue

        # Rule 3: title + first author + year.
        title_key = _title_author_year_key(work)
        if title_key and title_key in by_title:
            _replace_if_better(by_title[title_key], work, "title_author_year")
            continue

        # No duplicate found: register as new survivor.
        _insert(work)

    return list(survivors.values()), dropped


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "catalogue-dedup",
        help="Deduplicate works catalogue using three matching rules.",
    )
    parser.add_argument(
        "--input",
        type=str,
        required=True,
        nargs="+",
        metavar="GLOB_OR_PATH",
        help=(
            "One or more input JSONL files or glob patterns "
            '(e.g. "data/catalogue-fetch/*.jsonl").'
        ),
    )
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

    input_patterns: list[str] = args.input
    works: list[Work] = []
    for path in _iter_inputs(input_patterns):
        works.extend(read_jsonl(path, Work, stats))

    input_count = len(works)

    survivors, dropped_records = deduplicate(works)

    write_jsonl(survivors, output_dir / "deduplicated.jsonl")
    write_jsonl(dropped_records, output_dir / "dropped.jsonl")

    by_rule: dict[str, int] = {}
    for rec in dropped_records:
        by_rule[rec.rule] = by_rule.get(rec.rule, 0) + 1

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
            output_count=len(survivors),
            duplicates_removed=len(dropped_records),
            by_rule=by_rule,
        ),
    )

    elapsed = time.monotonic() - t0
    print(
        f"Dedup done: {input_count} in, {len(survivors)} out,"
        f" {len(dropped_records)} duplicates removed."
        f" ({elapsed:.1f}s)",
        file=sys.stderr,
    )
