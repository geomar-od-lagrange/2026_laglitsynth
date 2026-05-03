"""Full-text PDF retrieval via OA URLs, Unpaywall, and manual pickup."""

from __future__ import annotations

import argparse
import logging
import re
import shutil
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.dotenv import load_env_var
from laglitsynth.fulltext_retrieval.models import TOOL_NAME, RetrievalMeta, RetrievalRecord, RetrievalStatus
from laglitsynth.ids import work_id_to_filename
from laglitsynth.io import JsonlReadStats, append_jsonl, read_jsonl, write_meta
from laglitsynth.models import RunMeta
from laglitsynth.screening_abstracts.models import ScreeningVerdict

logger = logging.getLogger(__name__)

_DOI_PREFIX_RE = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)

# Display labels for summary output — aligned with docs/fulltext-retrieval.md.
_STATUS_LABELS: dict[RetrievalStatus, str] = {
    RetrievalStatus.retrieved_oa: "Retrieved (OA)",
    RetrievalStatus.retrieved_unpaywall: "Retrieved (Unpaywall)",
    RetrievalStatus.retrieved_manual: "Retrieved (manual)",
    RetrievalStatus.abstract_only: "Abstract-only",
    RetrievalStatus.failed: "Failed",
}


def _active_works(
    catalogue_path: Path,
    screening_verdicts_path: Path,
    screening_threshold: float,
    stats: JsonlReadStats | None = None,
) -> Iterator[Work]:
    """Yield Works that pass the screening threshold gate.

    Works whose ``ScreeningVerdict.relevance_score`` is None (sentinels:
    ``no-abstract``, ``llm-parse-failure``, ``llm-timeout``) are never
    filtered — absence of a score is not evidence of irrelevance.  Only an
    explicit numeric score strictly below ``screening_threshold`` excludes.

    Works present in the catalogue but absent from the verdicts file are also
    excluded (they were never screened).
    """
    verdicts = {
        v.work_id: v
        for v in read_jsonl(screening_verdicts_path, ScreeningVerdict, stats)
    }
    for w in read_jsonl(catalogue_path, Work, stats):
        sv = verdicts.get(w.id)
        if sv is None:
            continue
        if sv.relevance_score is not None and sv.relevance_score < screening_threshold:
            continue
        yield w


class _RateLimiter:
    """Per-domain rate limiter; encapsulates last-request timestamps."""

    def __init__(self) -> None:
        self._last_request: dict[str, float] = {}

    def wait(self, domain: str, min_interval: float = 1.0) -> None:
        now = time.monotonic()
        if domain in self._last_request:
            last = self._last_request[domain]
            if now - last < min_interval:
                time.sleep(min_interval - (now - last))
        self._last_request[domain] = time.monotonic()


def _validate_pdf(data: bytes) -> bool:
    return data[:4] == b"%PDF"


def _download_pdf(
    url: str,
    dest: Path,
    *,
    client: httpx.Client,
    rate_limiter: _RateLimiter,
) -> None:
    domain = urlparse(url).hostname or "unknown"
    rate_limiter.wait(domain)
    response = client.get(url, follow_redirects=True)
    response.raise_for_status()
    if not _validate_pdf(response.content):
        raise ValueError(f"Response from {url} is not a valid PDF")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd = tempfile.NamedTemporaryFile(
        dir=dest.parent, delete=False, suffix=".tmp"
    )
    try:
        tmp_path = Path(tmp_fd.name)
        tmp_fd.write(response.content)
        tmp_fd.close()
        tmp_path.rename(dest)
    except BaseException:
        tmp_fd.close()
        Path(tmp_fd.name).unlink(missing_ok=True)
        raise


def _try_oa_urls(
    work: Work,
    pdf_dest: Path,
    *,
    client: httpx.Client,
    rate_limiter: _RateLimiter,
) -> tuple[RetrievalStatus, str] | None:
    urls: list[str] = []
    if work.primary_location is not None and work.primary_location.pdf_url is not None:
        urls.append(work.primary_location.pdf_url)
    if work.open_access is not None and work.open_access.oa_url is not None:
        oa_url = work.open_access.oa_url
        if oa_url not in urls:
            urls.append(oa_url)
    if not urls:
        return None
    last_exc: Exception | None = None
    for url in urls:
        try:
            _download_pdf(url, pdf_dest, client=client, rate_limiter=rate_limiter)
            return RetrievalStatus.retrieved_oa, url
        except Exception as exc:
            logger.debug("OA download failed for %s: %s", url, exc)
            last_exc = exc
            continue
    # All URLs were attempted and all failed — signal failure upward.
    raise _AllAttemptsFailedError(last_exc)


class _AllAttemptsFailedError(Exception):
    """Raised when every download attempt for a set of URLs failed."""

    def __init__(self, last_exc: Exception | None) -> None:
        super().__init__(str(last_exc) if last_exc else "all attempts failed")
        self.last_exc = last_exc


def _try_unpaywall(
    work: Work,
    pdf_dest: Path,
    *,
    client: httpx.Client,
    email: str,
    rate_limiter: _RateLimiter,
) -> tuple[RetrievalStatus, str] | None:
    if work.doi is None:
        return None
    doi = _DOI_PREFIX_RE.sub("", work.doi)
    api_url = (
        f"https://api.unpaywall.org/v2/{quote(doi, safe='')}"
        f"?email={quote(email, safe='@.')}"
    )
    domain = "api.unpaywall.org"
    rate_limiter.wait(domain)
    response = client.get(api_url, follow_redirects=True)
    response.raise_for_status()
    data = response.json()
    best_loc = data.get("best_oa_location")
    if best_loc is None:
        return None
    pdf_url = best_loc.get("url_for_pdf")
    if pdf_url is None:
        return None
    _download_pdf(pdf_url, pdf_dest, client=client, rate_limiter=rate_limiter)
    return RetrievalStatus.retrieved_unpaywall, pdf_url


def _try_manual(
    work: Work,
    pdf_dest: Path,
    *,
    manual_dir: Path | None,
) -> tuple[RetrievalStatus, str] | None:
    if manual_dir is None:
        return None
    fname = work_id_to_filename(work.id)
    manual_path = manual_dir / f"{fname}.pdf"
    if not manual_path.is_file():
        return None
    pdf_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manual_path, pdf_dest)
    return RetrievalStatus.retrieved_manual, str(manual_path)


def _retrieve_one(
    work: Work,
    output_dir: Path,
    *,
    client: httpx.Client,
    email: str,
    manual_dir: Path | None,
    dry_run: bool,
    rate_limiter: _RateLimiter,
) -> RetrievalRecord:
    fname = work_id_to_filename(work.id)
    pdf_dest = output_dir / "pdfs" / f"{fname}.pdf"
    pdf_rel = f"pdfs/{fname}.pdf"
    now = datetime.now(UTC).isoformat(timespec="microseconds")

    if dry_run:
        has_oa = (
            (
                work.primary_location is not None
                and work.primary_location.pdf_url is not None
            )
            or (
                work.open_access is not None
                and work.open_access.oa_url is not None
            )
        )
        has_manual = (
            manual_dir is not None
            and (manual_dir / f"{fname}.pdf").is_file()
        )
        if has_manual:
            status = RetrievalStatus.retrieved_manual
        elif has_oa:
            status = RetrievalStatus.retrieved_oa
        else:
            status = RetrievalStatus.abstract_only
        return RetrievalRecord(
            work_id=work.id,
            retrieval_status=status,
            retrieved_at=now,
        )

    attempted = False
    last_exc: Exception | None = None

    # 1. Manual (no network, never marks attempted; manual absence is not a failure)
    result = _try_manual(work, pdf_dest, manual_dir=manual_dir)
    if result is not None:
        return RetrievalRecord(
            work_id=work.id,
            retrieval_status=result[0],
            source_url=result[1],
            pdf_path=pdf_rel,
            retrieved_at=now,
        )

    # 2. OA URLs
    has_oa_urls = (
        (work.primary_location is not None and work.primary_location.pdf_url is not None)
        or (work.open_access is not None and work.open_access.oa_url is not None)
    )
    if has_oa_urls:
        attempted = True
        try:
            result = _try_oa_urls(work, pdf_dest, client=client, rate_limiter=rate_limiter)
            if result is not None:
                return RetrievalRecord(
                    work_id=work.id,
                    retrieval_status=result[0],
                    source_url=result[1],
                    pdf_path=pdf_rel,
                    retrieved_at=now,
                )
        except _AllAttemptsFailedError as exc:
            logger.debug("All OA URLs failed for %s: %s", work.id, exc)
            last_exc = exc.last_exc if exc.last_exc is not None else exc

    # 3. Unpaywall
    if work.doi is not None:
        attempted = True
        try:
            result = _try_unpaywall(
                work, pdf_dest, client=client, email=email, rate_limiter=rate_limiter
            )
            if result is not None:
                return RetrievalRecord(
                    work_id=work.id,
                    retrieval_status=result[0],
                    source_url=result[1],
                    pdf_path=pdf_rel,
                    retrieved_at=now,
                )
        except Exception as exc:
            logger.debug("Unpaywall failed for %s: %s", work.id, exc)
            last_exc = exc

    # 4. No source — or all attempts failed
    if attempted and last_exc is not None:
        return RetrievalRecord(
            work_id=work.id,
            retrieval_status=RetrievalStatus.failed,
            error=str(last_exc),
            retrieved_at=now,
        )
    return RetrievalRecord(
        work_id=work.id,
        retrieval_status=RetrievalStatus.abstract_only,
        retrieved_at=now,
    )


def _write_retrieval_jsonl(records: list[RetrievalRecord], path: Path) -> None:
    """Overwrite retrieval.jsonl with the given records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        for rec in records:
            f.write(rec.model_dump_json() + "\n")


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "fulltext-retrieval",
        help="Retrieve full-text PDFs for works.",
    )
    parser.add_argument(
        "--catalogue",
        type=Path,
        required=True,
        help="Deduplicated catalogue JSONL (data/catalogue-dedup/deduplicated.jsonl)",
    )
    parser.add_argument(
        "--screening-verdicts",
        type=Path,
        required=True,
        help="Stage 3 verdicts JSONL (data/screening-abstracts/<run-id>/verdicts.jsonl)",
    )
    parser.add_argument(
        "--screening-threshold",
        type=float,
        default=50.0,
        help="Relevance score cutoff, 0-100 (default: 50)",
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="Output directory"
    )
    parser.add_argument(
        "--email",
        default=None,
        help=(
            "Contact email for Unpaywall API requests. "
            "Falls back to UNPAYWALL_EMAIL in .env when omitted."
        ),
    )
    parser.add_argument("--manual-dir", type=Path, default=None, help="Manual PDF dir")
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip works already retrieved successfully",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be retrieved without downloading",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    email: str | None = args.email
    if email is None:
        email = load_env_var("UNPAYWALL_EMAIL")
        if email is not None:
            print("Loaded UNPAYWALL_EMAIL from .env", file=sys.stderr)
    if email is None:
        raise SystemExit(
            "UNPAYWALL_EMAIL not set: pass --email or add "
            "UNPAYWALL_EMAIL=... to .env"
        )

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    manual_dir: Path | None = args.manual_dir
    if manual_dir is None:
        manual_dir = output_dir / "manual"

    stats = JsonlReadStats()

    # Load all previously recorded records (regardless of --skip-existing, so
    # we can preserve rows for works not in this run's input).
    retrieval_path = output_dir / "retrieval.jsonl"
    existing: dict[str, RetrievalRecord] = {}
    if retrieval_path.exists():
        for rec in read_jsonl(retrieval_path, RetrievalRecord):
            existing[rec.work_id] = rec

    # Works that already have a successful retrieval status are skipped under
    # --skip-existing.
    skip_ids: set[str] = set()
    if args.skip_existing:
        skip_ids = {
            wid
            for wid, rec in existing.items()
            if rec.retrieval_status.value.startswith("retrieved_")
        }
        if skip_ids:
            print(
                f"Skipping {len(skip_ids)} already-retrieved works.",
                file=sys.stderr,
            )

    works = list(
        _active_works(
            args.catalogue,
            args.screening_verdicts,
            args.screening_threshold,
            stats,
        )
    )
    total = len(works)
    works_by_id: dict[str, Work] = {w.id: w for w in works}
    input_ids = {w.id for w in works}

    # Seed retrieval.jsonl with records we won't re-produce this run:
    # existing records for input works being skipped (under --skip-existing)
    # plus existing records for works not in this run's input. New records
    # are appended per-work below, so a mid-run crash leaves retrieval.jsonl
    # consistent with progress made so far.
    preserved: list[RetrievalRecord] = []
    for work in works:
        if work.id in skip_ids and work.id in existing:
            preserved.append(existing[work.id])
    for wid, rec in existing.items():
        if wid not in input_ids:
            preserved.append(rec)
    if not args.dry_run:
        _write_retrieval_jsonl(preserved, retrieval_path)

    print(f"Retrieving PDFs for {total} works.", file=sys.stderr)

    rate_limiter = _RateLimiter()
    user_agent = f"laglitsynth/0.1 (mailto:{email})"
    client = httpx.Client(
        timeout=30.0,
        headers={"User-Agent": user_agent},
    )

    # new_records maps work_id → freshly processed RetrievalRecord this run.
    new_records: dict[str, RetrievalRecord] = {}

    try:
        for i, work in enumerate(works, 1):
            if work.id in skip_ids:
                continue

            record = _retrieve_one(
                work,
                output_dir,
                client=client,
                email=email,
                manual_dir=manual_dir,
                dry_run=args.dry_run,
                rate_limiter=rate_limiter,
            )
            new_records[work.id] = record
            if not args.dry_run:
                append_jsonl(record, retrieval_path)

            if i % 10 == 0 or i == total:
                print(f"  [{i}/{total}] processed", file=sys.stderr)
    finally:
        client.close()

    # Build an in-memory list matching the final on-disk content, for the
    # summary counters and unretrieved.txt derivation below.
    final_records: list[RetrievalRecord] = []
    for work in works:
        if work.id in new_records:
            final_records.append(new_records[work.id])
        elif work.id in existing:
            final_records.append(existing[work.id])
    for wid, rec in existing.items():
        if wid not in input_ids:
            final_records.append(rec)

    # Derive unretrieved.txt from the final record list.
    unretrieved_path = output_dir / "unretrieved.txt"
    with open(unretrieved_path, "w") as f:
        for rec in final_records:
            if rec.retrieval_status in {
                RetrievalStatus.abstract_only,
                RetrievalStatus.failed,
            }:
                unretrieved_work = works_by_id.get(rec.work_id)
                doi = (unretrieved_work.doi if unretrieved_work is not None else None) or "no-doi"
                fname = work_id_to_filename(rec.work_id)
                f.write(f"{doi}\t{fname}.pdf\n")

    # Derive summary counters from the final record list.
    by_source: Counter[str] = Counter()
    retrieved_count = 0
    abstract_only_count = 0
    failed_count = 0
    for rec in final_records:
        status = rec.retrieval_status
        by_source[status.value] += 1
        if status.value.startswith("retrieved_"):
            retrieved_count += 1
        elif status == RetrievalStatus.abstract_only:
            abstract_only_count += 1
        elif status == RetrievalStatus.failed:
            failed_count += 1

    run_meta = RunMeta(
        tool=TOOL_NAME,
        run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        validation_skipped=stats.skipped,
    )
    write_meta(
        output_dir / "retrieval-meta.json",
        RetrievalMeta(
            run=run_meta,
            total_works=total,
            retrieved_count=retrieved_count,
            abstract_only_count=abstract_only_count,
            failed_count=failed_count,
            by_source=dict(by_source),
        ),
    )

    # Summary
    print("\nRetrieval summary:", file=sys.stderr)
    print(f"  Total works:            {total}", file=sys.stderr)
    for status in RetrievalStatus:
        count = by_source.get(status.value, 0)
        if count == 0:
            continue
        label = _STATUS_LABELS.get(status, status.value)
        pct = 100.0 * count / total if total > 0 else 0.0
        print(f"  {label + ':':<26}{count:>4}  ({pct:.1f}%)", file=sys.stderr)
