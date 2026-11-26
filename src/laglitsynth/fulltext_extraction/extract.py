"""Full-text extraction via GROBID: corpus PDF in, TEI XML on disk.

Iterates the ``CorpusPdf`` rows of ``data/corpus/provenance.jsonl`` that
carry a PDF. Each ``ExtractedDocument`` records the corpus copy's sha256 so
later stages can tell an extraction of a since-changed PDF apart.

``--concurrency N`` keeps N GROBID requests in flight through
``map_concurrent``; the main thread writes every TEI file and record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import httpx

from laglitsynth.concurrency import map_concurrent, positive_int
from laglitsynth.corpus.load import load_corpus_meta, read_corpus_pdfs
from laglitsynth.corpus.models import CorpusPdf
from laglitsynth.fulltext_extraction.models import (
    EXTRACTIONS_FILENAME,
    META_FILENAME,
    TOOL_NAME,
    ExtractedDocument,
    ExtractionMeta,
)
from laglitsynth.io import append_jsonl, read_jsonl, write_meta
from laglitsynth.layout import corpus_dir, extraction_dir
from laglitsynth.models import RunMeta

logger = logging.getLogger(__name__)


class _Outcome(NamedTuple):
    """One worker's GROBID result: the TEI bytes, or the error that stopped it."""

    row: CorpusPdf
    tei_bytes: bytes | None  # None when the request failed
    error: str | None  # None when the request succeeded


def _grobid_health(grobid_url: str, client: httpx.Client) -> bool:
    try:
        resp = client.get(f"{grobid_url}/api/isalive")
        return resp.status_code == 200
    except Exception:
        return False


def _grobid_version(grobid_url: str, client: httpx.Client) -> str:
    """Return GROBID's version string, or ``"unknown"`` if the probe fails.

    GROBID 0.9.0 answers ``/api/version`` with JSON
    (``{"version": "0.9.0", "revision": "0.9.0"}``); 0.8.0 answered with
    the bare version as plain text. Both shapes are accepted.
    """
    try:
        resp = client.get(f"{grobid_url}/api/version")
        if resp.status_code != 200:
            return "unknown"
        text = resp.text.strip()
        try:
            payload = json.loads(text)
        except ValueError:
            return text or "unknown"
        if isinstance(payload, dict):
            version = payload.get("version")
            if isinstance(version, str) and version:
                return version
        return text or "unknown"
    except Exception:
        return "unknown"


# Seconds to wait before each retry of a request GROBID answered with 503
# (its own request pool is full); after the last one the 503 is final.
_BUSY_RETRY_DELAYS: tuple[float, ...] = (2.0, 5.0, 10.0)


def _process_pdf(
    pdf_path: Path,
    grobid_url: str,
    client: httpx.Client,
    *,
    timeout: float,
    consolidate_citations: str,
) -> bytes:
    """POST one PDF to GROBID and return the TEI bytes.

    A 503 means GROBID's server-side request pool is full; the request is
    retried after each of ``_BUSY_RETRY_DELAYS``. Any other error status
    raises at once.
    """
    for delay in (*_BUSY_RETRY_DELAYS, None):
        with open(pdf_path, "rb") as f:
            resp = client.post(
                f"{grobid_url}/api/processFulltextDocument",
                files={"input": (pdf_path.name, f, "application/pdf")},
                data={"consolidateCitations": consolidate_citations},
                timeout=timeout,
            )
        if resp.status_code != 503 or delay is None:
            break
        logger.info("GROBID busy (503) on %s; retrying in %.0fs", pdf_path.name, delay)
        time.sleep(delay)
    resp.raise_for_status()
    return resp.content


def _load_latest(output_dir: Path) -> dict[str, ExtractedDocument]:
    """Return the latest record per work in ``extraction.jsonl`` (appended)."""
    extraction_path = output_dir / EXTRACTIONS_FILENAME
    if not extraction_path.exists():
        return {}
    return {
        rec.work_id: rec for rec in read_jsonl(extraction_path, ExtractedDocument)
    }


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "fulltext-extraction",
        help="Extract structured text from PDFs via GROBID.",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
        help="Corpus written by corpus-assemble (default: <data-dir>/corpus/)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Output directory (default: <data-dir>/fulltext-extraction/)",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Bucket root for stage outputs (default: data/)",
    )
    parser.add_argument(
        "--grobid-url",
        type=str,
        default="http://localhost:8070",
        help="GROBID API endpoint (default: http://localhost:8070)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Per-paper GROBID timeout in seconds (default: 120)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help=(
            "Skip works whose latest ExtractedDocument was made from the "
            "current corpus PDF (same pdf_sha256)"
        ),
    )
    parser.add_argument(
        "--consolidate-citations",
        choices=["0", "1"],
        default="0",
        help=(
            "GROBID consolidateCitations flag: 0 (default) leaves extracted "
            "references as-is; 1 enriches them via Crossref/Semantic Scholar "
            "(needs internet, slower). OpenAlex is the metadata authority, so "
            "0 is the recommended default."
        ),
    )
    parser.add_argument(
        "--concurrency",
        type=positive_int,
        default=1,
        help="In-flight GROBID requests (default: 1). See docs/interfaces.md.",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    data_dir: Path = args.data_dir
    corpus: Path = args.corpus_dir if args.corpus_dir is not None else corpus_dir(data_dir)
    corpus_meta = load_corpus_meta(corpus)
    corpus_pdfs = [p for p in read_corpus_pdfs(corpus) if p.pdf_path is not None]
    output_dir: Path = (
        args.output_dir if args.output_dir is not None else extraction_dir(data_dir)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    tei_dir = output_dir / "tei"
    tei_dir.mkdir(parents=True, exist_ok=True)

    # Short-timeout client for preflight health/version checks.
    preflight_client = httpx.Client(timeout=5.0)

    if not _grobid_health(args.grobid_url, preflight_client):
        preflight_client.close()
        raise SystemExit(
            f"GROBID is not running at {args.grobid_url}.\n"
            "Start it with: docker run --rm -p 8070:8070 grobid/grobid:0.9.0-crf\n"
            "Wait until /api/isalive answers, then retry."
        )

    version = _grobid_version(args.grobid_url, preflight_client)
    preflight_client.close()

    # A work is skipped only when its latest record came from the same PDF
    # bytes as the corpus copy; a changed PDF is extracted again.
    skip_ids: set[str] = set()
    if args.skip_existing:
        latest = _load_latest(output_dir)
        skip_ids = {
            p.work_id
            for p in corpus_pdfs
            if p.work_id in latest
            and latest[p.work_id].pdf_sha256 == p.content_sha256
        }
        if skip_ids:
            print(
                f"Skipping {len(skip_ids)} already-extracted PDFs.",
                file=sys.stderr,
            )

    total = len(corpus_pdfs)
    print(
        f"Extracting {total - len(skip_ids)} of {total} corpus PDFs via GROBID ({version}).",
        file=sys.stderr,
    )

    extraction_path = output_dir / EXTRACTIONS_FILENAME
    todo = [row for row in corpus_pdfs if row.work_id not in skip_ids]
    concurrency: int = args.concurrency
    t0 = time.monotonic()
    extracted_count = 0
    failed_count = 0

    # One client shared by the workers (httpx.Client is thread-safe); the
    # pool holds a connection per worker. It uses the full (potentially
    # long) per-paper timeout.
    client = httpx.Client(
        timeout=args.timeout,
        limits=httpx.Limits(
            max_connections=concurrency, max_keepalive_connections=concurrency
        ),
    )

    def _extract_one(row: CorpusPdf) -> _Outcome:
        assert row.pdf_path is not None
        try:
            tei_bytes = _process_pdf(
                corpus / row.pdf_path,
                args.grobid_url,
                client,
                timeout=args.timeout,
                consolidate_citations=args.consolidate_citations,
            )
        except Exception as exc:
            return _Outcome(row=row, tei_bytes=None, error=str(exc))
        return _Outcome(row=row, tei_bytes=tei_bytes, error=None)

    # Workers only talk to GROBID; this thread writes every TEI file and
    # appends every record, so there is no shared writer to lock.
    try:
        for index, outcome in enumerate(
            map_concurrent(_extract_one, todo, max_workers=concurrency), 1
        ):
            row = outcome.row
            assert row.pdf_path is not None and row.content_sha256 is not None
            stem = Path(row.pdf_path).stem

            if outcome.tei_bytes is None:
                failed_count += 1
                print(
                    f"  [{index}/{len(todo)}] FAILED {stem}: {outcome.error}",
                    file=sys.stderr,
                )
                continue

            # Save raw TEI — the canonical extraction artefact.
            tei_filename = f"{stem}.tei.xml"
            (tei_dir / tei_filename).write_bytes(outcome.tei_bytes)

            doc = ExtractedDocument(
                work_id=row.work_id,
                tei_path=f"tei/{tei_filename}",
                content_sha256=hashlib.sha256(outcome.tei_bytes).hexdigest(),
                pdf_sha256=row.content_sha256,
                extracted_at=datetime.now(UTC).isoformat(timespec="microseconds"),
            )
            append_jsonl(doc, extraction_path)
            extracted_count += 1

            if index % 10 == 0 or index == len(todo):
                print(f"  [{index}/{len(todo)}] processed", file=sys.stderr)
    finally:
        client.close()

    elapsed = time.monotonic() - t0

    run_meta = RunMeta(
        tool=TOOL_NAME,
        run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        validation_skipped=0,  # the corpus readers exit on invalid rows
    )
    write_meta(
        output_dir / META_FILENAME,
        ExtractionMeta(
            run=run_meta,
            corpus_id=corpus_meta.corpus_id,
            grobid_version=version,
            total_pdfs=total,
            extracted_count=extracted_count,
            failed_count=failed_count,
        ),
    )

    print(
        f"\nExtraction done: {extracted_count} extracted, {failed_count} failed, "
        f"{len(skip_ids)} skipped (already extracted). ({elapsed:.1f}s)",
        file=sys.stderr,
    )
