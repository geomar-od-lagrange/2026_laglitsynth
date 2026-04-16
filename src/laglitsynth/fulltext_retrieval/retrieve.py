"""Full-text PDF retrieval via OA URLs, Unpaywall, and manual pickup."""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.fulltext_retrieval.models import RetrievalMeta, RetrievalRecord, RetrievalStatus
from laglitsynth.io import append_jsonl, read_jsonl, read_works_jsonl, write_meta

logger = logging.getLogger(__name__)

_last_request: dict[str, float] = {}


def _rate_limit(domain: str, min_interval: float = 1.0) -> None:
    now = time.monotonic()
    if domain in _last_request:
        last = _last_request[domain]
        if now - last < min_interval:
            time.sleep(min_interval - (now - last))
    _last_request[domain] = time.monotonic()


def work_id_to_filename(work_id: str) -> str:
    return work_id.rsplit("/", 1)[-1]


def _validate_pdf(data: bytes, content_type: str | None) -> bool:
    if data[:4] == b"%PDF":
        return True
    if content_type is not None and "application/pdf" in content_type:
        return True
    return False


def _download_pdf(
    url: str,
    dest: Path,
    *,
    client: httpx.Client,
) -> None:
    domain = urlparse(url).hostname or "unknown"
    _rate_limit(domain)
    response = client.get(url, follow_redirects=True)
    response.raise_for_status()
    content_type = response.headers.get("content-type")
    if not _validate_pdf(response.content, content_type):
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
) -> tuple[RetrievalStatus, str] | None:
    urls: list[str] = []
    if work.primary_location is not None and work.primary_location.pdf_url is not None:
        urls.append(work.primary_location.pdf_url)
    if work.open_access is not None and work.open_access.oa_url is not None:
        oa_url = work.open_access.oa_url
        if oa_url not in urls:
            urls.append(oa_url)
    for url in urls:
        try:
            _download_pdf(url, pdf_dest, client=client)
            return RetrievalStatus.retrieved_oa, url
        except Exception as exc:
            logger.debug("OA download failed for %s: %s", url, exc)
            continue
    return None


def _try_unpaywall(
    work: Work,
    pdf_dest: Path,
    *,
    client: httpx.Client,
    email: str,
) -> tuple[RetrievalStatus, str] | None:
    if work.doi is None:
        return None
    doi = work.doi.replace("https://doi.org/", "")
    api_url = (
        f"https://api.unpaywall.org/v2/{quote(doi, safe='')}"
        f"?email={quote(email, safe='@.')}"
    )
    domain = "api.unpaywall.org"
    _rate_limit(domain)
    response = client.get(api_url, follow_redirects=True)
    response.raise_for_status()
    data = response.json()
    best_loc = data.get("best_oa_location")
    if best_loc is None:
        return None
    pdf_url = best_loc.get("url_for_pdf")
    if pdf_url is None:
        return None
    _download_pdf(pdf_url, pdf_dest, client=client)
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
        has_doi = work.doi is not None
        has_manual = (
            manual_dir is not None
            and (manual_dir / f"{fname}.pdf").is_file()
        )
        if has_manual:
            status = RetrievalStatus.retrieved_manual
        elif has_oa:
            status = RetrievalStatus.retrieved_oa
        elif has_doi:
            status = RetrievalStatus.retrieved_unpaywall
        else:
            status = RetrievalStatus.abstract_only
        return RetrievalRecord(
            work_id=work.id,
            retrieval_status=status,
            retrieved_at=now,
        )

    # 1. Manual
    result = _try_manual(work, pdf_dest, manual_dir=manual_dir)
    if result is not None:
        return RetrievalRecord(
            work_id=work.id,
            retrieval_status=result[0],
            source_url=result[1],
            pdf_path=pdf_rel,
            retrieved_at=now,
        )

    # 2. OA URLs (exceptions handled per-URL inside _try_oa_urls)
    result = _try_oa_urls(work, pdf_dest, client=client)
    if result is not None:
        return RetrievalRecord(
            work_id=work.id,
            retrieval_status=result[0],
            source_url=result[1],
            pdf_path=pdf_rel,
            retrieved_at=now,
        )

    # 3. Unpaywall (failure here falls through to abstract_only)
    try:
        result = _try_unpaywall(work, pdf_dest, client=client, email=email)
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

    # 4. No source
    return RetrievalRecord(
        work_id=work.id,
        retrieval_status=RetrievalStatus.abstract_only,
        retrieved_at=now,
    )


def _load_existing(output_dir: Path) -> set[str]:
    retrieval_path = output_dir / "retrieval.jsonl"
    if not retrieval_path.exists():
        return set()
    skip_ids: set[str] = set()
    for rec in read_jsonl(retrieval_path, RetrievalRecord):
        if rec.retrieval_status.value.startswith("retrieved_"):
            skip_ids.add(rec.work_id)
    return skip_ids


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "fulltext-retrieval",
        help="Retrieve full-text PDFs for works.",
    )
    parser.add_argument("--input", type=Path, required=True, help="Input JSONL file")
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="Output directory"
    )
    parser.add_argument("--email", type=str, required=True, help="Email for Unpaywall")
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
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    manual_dir: Path | None = args.manual_dir
    if manual_dir is None:
        manual_dir = output_dir / "manual"

    skip_ids: set[str] = set()
    if args.skip_existing:
        skip_ids = _load_existing(output_dir)
        if skip_ids:
            print(
                f"Skipping {len(skip_ids)} already-retrieved works.",
                file=sys.stderr,
            )

    retrieval_path = output_dir / "retrieval.jsonl"
    works = list(read_works_jsonl(args.input))
    total = len(works)

    print(f"Retrieving PDFs for {total} works.", file=sys.stderr)

    by_source: Counter[str] = Counter()
    retrieved_count = 0
    abstract_only_count = 0
    failed_count = 0

    user_agent = f"laglitsynth/0.1 (mailto:{args.email})"
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
                output_dir,
                client=client,
                email=args.email,
                manual_dir=manual_dir,
                dry_run=args.dry_run,
            )

            if not args.dry_run:
                append_jsonl(record, retrieval_path)

            status = record.retrieval_status
            by_source[status.value] += 1
            if status.value.startswith("retrieved_"):
                retrieved_count += 1
            elif status == RetrievalStatus.abstract_only:
                abstract_only_count += 1
            elif status == RetrievalStatus.failed:
                failed_count += 1

            if i % 10 == 0 or i == total:
                print(f"  [{i}/{total}] processed", file=sys.stderr)
    finally:
        client.close()

    # Write unretrieved.txt
    unretrieved_path = output_dir / "unretrieved.txt"
    with open(unretrieved_path, "w") as f:
        for work in works:
            if work.id in skip_ids:
                continue
            fname = work_id_to_filename(work.id)
            # Check if this work was abstract_only or failed
            # We need to check the by_source counter, but we need per-work info
            # Re-scan: just check if the PDF exists
            pdf_path = output_dir / "pdfs" / f"{fname}.pdf"
            if not pdf_path.exists() and not args.dry_run:
                doi = work.doi or "no-doi"
                f.write(f"{doi}\t{fname}.pdf\n")

    write_meta(
        output_dir / "retrieval-meta.json",
        RetrievalMeta(
            retrieved_at=datetime.now(UTC).isoformat(timespec="microseconds"),
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
    for source, count in sorted(by_source.items()):
        pct = 100.0 * count / total if total > 0 else 0.0
        label = source.replace("_", " ").title()
        print(f"  {label + ':':<24}{count:>4}  ({pct:.1f}%)", file=sys.stderr)
