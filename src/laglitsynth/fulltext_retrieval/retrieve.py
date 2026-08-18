"""Full-text PDF retrieval via OA URLs and Unpaywall into the shared store.

PDFs land in the persistent, work-keyed store ``<data-dir>/pdfs/<stem>.pdf``
with a work-keyed provenance sidecar ``<data-dir>/pdfs/provenance.jsonl``.
Manual ingestion is no longer an inline step here — collaborators' PDFs are
brought in via ``fulltext-retrieval-import``; the export step supersedes the
old ``unretrieved.txt`` derivation.
"""

from __future__ import annotations

import argparse
import logging
import re
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
from laglitsynth.fulltext_retrieval.models import (
    TOOL_NAME,
    PdfProvenanceRecord,
    PdfSource,
    RetrievalMeta,
)
from laglitsynth.fulltext_retrieval.store import (
    load_provenance,
    pdfs_dir,
    sha256_of,
    store_pdf_path,
    upsert_provenance,
)
from laglitsynth.ids import work_id_to_filename
from laglitsynth.io import JsonlReadStats, read_jsonl, write_meta
from laglitsynth.manifest import record_stage, resolve_input
from laglitsynth.models import RunMeta
from laglitsynth.screening_abstracts.models import ScreeningVerdict

logger = logging.getLogger(__name__)

_DOI_PREFIX_RE = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)

# Display labels for summary output — aligned with docs/fulltext-retrieval.md.
_SOURCE_LABELS: dict[PdfSource, str] = {
    PdfSource.oa: "Retrieved (OA)",
    PdfSource.unpaywall: "Retrieved (Unpaywall)",
    PdfSource.zotero_import: "Imported (Zotero)",
    PdfSource.manual: "Imported (manual)",
    PdfSource.missing: "Missing",
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
) -> tuple[PdfSource, str] | None:
    urls: list[str] = []
    if work.primary_location is not None and work.primary_location.pdf_url is not None:
        urls.append(work.primary_location.pdf_url)
    if work.open_access is not None and work.open_access.oa_url is not None:
        oa_url = work.open_access.oa_url
        if oa_url not in urls:
            urls.append(oa_url)
    if not urls:
        return None
    for url in urls:
        try:
            _download_pdf(url, pdf_dest, client=client, rate_limiter=rate_limiter)
            return PdfSource.oa, url
        except Exception as exc:
            logger.debug("OA download failed for %s: %s", url, exc)
            continue
    # No URLs, or every attempt failed: the caller falls through to Unpaywall
    # identically in both cases, so a single `None` carries the whole result.
    return None


def _try_unpaywall(
    work: Work,
    pdf_dest: Path,
    *,
    client: httpx.Client,
    email: str,
    rate_limiter: _RateLimiter,
) -> tuple[PdfSource, str] | None:
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
    return PdfSource.unpaywall, pdf_url


def _missing_record(work: Work, now: str) -> PdfProvenanceRecord:
    return PdfProvenanceRecord(
        work_id=work.id,
        stem=work_id_to_filename(work.id),
        doi=work.doi,
        source=PdfSource.missing,
        source_url=None,
        pdf_path=None,
        content_sha256=None,
        obtained_at=now,
    )


def _retrieve_one(
    work: Work,
    data_dir: Path,
    *,
    client: httpx.Client,
    email: str,
    dry_run: bool,
    rate_limiter: _RateLimiter,
) -> PdfProvenanceRecord:
    stem = work_id_to_filename(work.id)
    pdf_dest = store_pdf_path(data_dir, stem)
    pdf_rel = f"pdfs/{stem}.pdf"
    now = datetime.now(UTC).isoformat(timespec="microseconds")

    has_oa_urls = (
        (work.primary_location is not None and work.primary_location.pdf_url is not None)
        or (work.open_access is not None and work.open_access.oa_url is not None)
    )

    if dry_run:
        if has_oa_urls:
            return PdfProvenanceRecord(
                work_id=work.id,
                stem=stem,
                doi=work.doi,
                source=PdfSource.oa,
                source_url=None,
                pdf_path=None,
                content_sha256=None,
                obtained_at=now,
            )
        return _missing_record(work, now)

    def _retrieved(source: PdfSource, url: str) -> PdfProvenanceRecord:
        return PdfProvenanceRecord(
            work_id=work.id,
            stem=stem,
            doi=work.doi,
            source=source,
            source_url=url,
            pdf_path=pdf_rel,
            content_sha256=sha256_of(pdf_dest),
            obtained_at=now,
        )

    # 1. OA URLs
    if has_oa_urls:
        result = _try_oa_urls(work, pdf_dest, client=client, rate_limiter=rate_limiter)
        if result is not None:
            return _retrieved(result[0], result[1])

    # 2. Unpaywall
    if work.doi is not None:
        try:
            result = _try_unpaywall(
                work, pdf_dest, client=client, email=email, rate_limiter=rate_limiter
            )
            if result is not None:
                return _retrieved(result[0], result[1])
        except Exception as exc:
            logger.debug("Unpaywall failed for %s: %s", work.id, exc)

    # 3. No source obtained — or all attempts failed: the store only cares
    # whether a PDF exists, so both collapse to `missing`.
    return _missing_record(work, now)


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "fulltext-retrieval",
        help="Retrieve full-text PDFs for works into the shared store.",
    )
    parser.add_argument(
        "--catalogue",
        type=Path,
        default=None,
        help=(
            "Deduplicated catalogue JSONL (data/catalogue-dedup/deduplicated.jsonl). "
            "Resolved from data/manifest.json's catalogue-dedup entry when omitted."
        ),
    )
    parser.add_argument(
        "--screening-verdicts",
        type=Path,
        default=None,
        help=(
            "Stage 3 verdicts JSONL (data/screening-abstracts/<run-id>/verdicts.jsonl). "
            "Resolved from data/manifest.json's screening-abstracts entry when omitted."
        ),
    )
    parser.add_argument(
        "--screening-threshold",
        type=float,
        default=50.0,
        help="Relevance score cutoff, 0-100 (default: 50)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help=(
            "Project data directory. PDFs and provenance live under "
            "<data-dir>/pdfs/ (default: data)."
        ),
    )
    parser.add_argument(
        "--email",
        default=None,
        help=(
            "Contact email for Unpaywall API requests. "
            "Falls back to UNPAYWALL_EMAIL in .env when omitted."
        ),
    )
    parser.add_argument(
        "--refetch",
        action="store_true",
        help=(
            "Re-download works that already have a PDF. By default such works "
            "are skipped (only missing/unseen works are attempted)."
        ),
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

    data_dir: Path = args.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    catalogue = resolve_input(
        data_dir,
        args.catalogue,
        upstream_stage="catalogue-dedup",
        flag_name="--catalogue",
    )
    screening_verdicts = resolve_input(
        data_dir,
        args.screening_verdicts,
        upstream_stage="screening-abstracts",
        flag_name="--screening-verdicts",
    )

    stats = JsonlReadStats()

    # Load the whole provenance store keyed by work_id (single-writer,
    # last-write-wins).
    provenance = load_provenance(data_dir)

    # Retrieval is sticky by default: works that already have a non-missing
    # provenance record are skipped (only missing/unseen works are attempted).
    # --refetch opts back into re-downloading held works.
    skip_ids: set[str] = set()
    if not args.refetch:
        skip_ids = {
            wid
            for wid, rec in provenance.items()
            if rec.source != PdfSource.missing
        }
        if skip_ids:
            print(
                f"Skipping {len(skip_ids)} works that already have a PDF "
                f"(use --refetch to re-download).",
                file=sys.stderr,
            )

    works = list(
        _active_works(
            catalogue,
            screening_verdicts,
            args.screening_threshold,
            stats,
        )
    )
    total = len(works)

    print(f"Retrieving PDFs for {total} works.", file=sys.stderr)

    rate_limiter = _RateLimiter()
    user_agent = f"laglitsynth/0.1 (mailto:{email})"
    client = httpx.Client(
        timeout=30.0,
        headers={"User-Agent": user_agent},
    )

    try:
        for i, work in enumerate(works, 1):
            if work.id in skip_ids:
                continue

            record = _retrieve_one(
                work,
                data_dir,
                client=client,
                email=email,
                dry_run=args.dry_run,
                rate_limiter=rate_limiter,
            )
            if not args.dry_run:
                # No-downgrade guard: a failed attempt yields a `missing`
                # record, but if a prior non-missing record (with its PDF on
                # disk) exists, keep it rather than clobber a held PDF's
                # provenance. Only fires on --refetch (the sticky default
                # never processes held works), and is what makes --refetch
                # safe: _download_pdf writes via tmp+rename, so the prior
                # <stem>.pdf is untouched by a failed re-fetch.
                prior = provenance.get(work.id)
                if (
                    record.source is PdfSource.missing
                    and prior is not None
                    and prior.source is not PdfSource.missing
                ):
                    logger.debug(
                        "Keeping prior %s record for %s; re-fetch failed, "
                        "not downgrading to missing",
                        prior.source.value,
                        work.id,
                    )
                else:
                    upsert_provenance(data_dir, provenance, record)
            else:
                provenance[work.id] = record

            if i % 10 == 0 or i == total:
                print(f"  [{i}/{total}] processed", file=sys.stderr)
    finally:
        client.close()

    # Summary counters are computed over this run's active works only.
    active_ids = {w.id for w in works}
    by_source: Counter[str] = Counter()
    retrieved_count = 0
    missing_count = 0
    for wid in active_ids:
        rec = provenance.get(wid)
        if rec is None:
            continue
        by_source[rec.source.value] += 1
        if rec.source == PdfSource.missing:
            missing_count += 1
        else:
            retrieved_count += 1

    run_meta = RunMeta(
        tool=TOOL_NAME,
        run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        validation_skipped=stats.skipped,
    )
    meta_path = data_dir / "fulltext-retrieval" / "retrieval-meta.json"
    write_meta(
        meta_path,
        RetrievalMeta(
            run=run_meta,
            total_works=total,
            retrieved_count=retrieved_count,
            missing_count=missing_count,
            by_source=dict(by_source),
        ),
    )

    # Summary
    print("\nRetrieval summary:", file=sys.stderr)
    print(f"  Total works:            {total}", file=sys.stderr)
    for source in PdfSource:
        count = by_source.get(source.value, 0)
        if count == 0:
            continue
        label = _SOURCE_LABELS.get(source, source.value)
        pct = 100.0 * count / total if total > 0 else 0.0
        print(f"  {label + ':':<26}{count:>4}  ({pct:.1f}%)", file=sys.stderr)

    # A dry run fetched nothing, so it has no output to record. Screening,
    # eligibility, and extraction take the same line.
    if not args.dry_run:
        record_stage(
            data_dir,
            stage="fulltext-retrieval",
            inputs={"catalogue": catalogue, "screening_verdicts": screening_verdicts},
            output=pdfs_dir(data_dir),
            meta_path=meta_path,
        )
