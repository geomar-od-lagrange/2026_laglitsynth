"""Full-text extraction via GROBID TEI XML parsing."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
from lxml import etree

from laglitsynth.fulltext_extraction.models import (
    ExtractedDocument,
    ExtractionMeta,
    TextSection,
)
from laglitsynth.ids import filename_to_work_id
from laglitsynth.io import append_jsonl, read_jsonl, write_meta

logger = logging.getLogger(__name__)

TEI_NS = "{http://www.tei-c.org/ns/1.0}"

# Hardened parser: no external-entity resolution, no network access.
_TEI_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


def _element_text(el: etree._Element) -> str:
    result: str = etree.tostring(el, method="text", encoding="unicode")
    return result.strip()


def parse_tei(xml_bytes: bytes) -> list[TextSection]:
    root = etree.fromstring(xml_bytes, parser=_TEI_PARSER)
    body = root.find(f".//{TEI_NS}body")
    if body is None:
        return []

    # Strip figure elements (captions are noisy)
    for fig in body.findall(f".//{TEI_NS}figure"):
        parent = fig.getparent()
        if parent is not None:
            parent.remove(fig)

    divs = body.findall(f"{TEI_NS}div")

    if not divs:
        # No div sections — treat entire body as one section
        text = _element_text(body)
        if not text:
            return []
        return [TextSection(title="Body", text=text)]

    sections: list[TextSection] = []
    for div in divs:
        head = div.find(f"{TEI_NS}head")
        title = "Untitled section"
        if head is not None:
            head_text = _element_text(head)
            if head_text:
                title = head_text

        paragraphs: list[str] = []
        for p in div.findall(f"{TEI_NS}p"):
            p_text = _element_text(p)
            if p_text:
                paragraphs.append(p_text)

        if not paragraphs:
            continue

        sections.append(TextSection(title=title, text="\n\n".join(paragraphs)))

    return sections


def _grobid_health(grobid_url: str, client: httpx.Client) -> bool:
    try:
        resp = client.get(f"{grobid_url}/api/isalive")
        return resp.status_code == 200
    except Exception:
        return False


def _grobid_version(grobid_url: str, client: httpx.Client) -> str:
    try:
        resp = client.get(f"{grobid_url}/api/version")
        if resp.status_code == 200:
            return resp.text.strip()
    except Exception:
        pass
    return "unknown"


def _process_pdf(
    pdf_path: Path,
    grobid_url: str,
    client: httpx.Client,
    *,
    timeout: float,
) -> bytes:
    with open(pdf_path, "rb") as f:
        resp = client.post(
            f"{grobid_url}/api/processFulltextDocument",
            files={"input": (pdf_path.name, f, "application/pdf")},
            data={"consolidateCitations": "0"},
            timeout=timeout,
        )
    resp.raise_for_status()
    return resp.content


def _load_existing(output_dir: Path) -> set[str]:
    extraction_path = output_dir / "extraction.jsonl"
    if not extraction_path.exists():
        return set()
    return {
        rec.work_id for rec in read_jsonl(extraction_path, ExtractedDocument)
    }


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "fulltext-extraction",
        help="Extract structured text from PDFs via GROBID.",
    )
    parser.add_argument(
        "--pdf-dir", type=Path, required=True, help="Directory containing PDFs"
    )
    parser.add_argument(
        "--output-dir", type=Path, required=True, help="Output directory"
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
        help="Skip PDFs that already have an ExtractedDocument record",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    tei_dir = output_dir / "tei"
    tei_dir.mkdir(parents=True, exist_ok=True)

    # Short-timeout client for preflight health/version checks.
    preflight_client = httpx.Client(timeout=5.0)

    if not _grobid_health(args.grobid_url, preflight_client):
        preflight_client.close()
        raise SystemExit(
            f"GROBID is not running at {args.grobid_url}.\n"
            "Start it with: docker run --rm -p 8070:8070 lfoppiano/grobid:0.8.0\n"
            "Wait 30-60 seconds for startup, then retry."
        )

    version = _grobid_version(args.grobid_url, preflight_client)
    preflight_client.close()

    skip_ids: set[str] = set()
    if args.skip_existing:
        skip_ids = _load_existing(output_dir)
        if skip_ids:
            print(
                f"Skipping {len(skip_ids)} already-extracted PDFs.",
                file=sys.stderr,
            )

    pdfs = sorted(args.pdf_dir.glob("*.pdf"))
    total = len(pdfs)
    print(f"Extracting {total} PDFs via GROBID ({version}).", file=sys.stderr)

    extraction_path = output_dir / "extraction.jsonl"
    t0 = time.monotonic()
    extracted_count = 0
    failed_count = 0
    invalid_stem_count = 0

    # Per-paper client uses the full (potentially long) timeout.
    client = httpx.Client(timeout=args.timeout)
    try:
        for i, pdf in enumerate(pdfs, 1):
            work_id = filename_to_work_id(pdf.stem)
            if work_id is None:
                invalid_stem_count += 1
                logger.warning(
                    "Skipping %s: stem %r does not match ^W\\d+$ pattern",
                    pdf,
                    pdf.stem,
                )
                print(
                    f"  WARNING: skipping {pdf.name} — stem {pdf.stem!r} is not a valid OpenAlex W-ID",
                    file=sys.stderr,
                )
                continue

            if work_id in skip_ids:
                continue

            try:
                tei_bytes = _process_pdf(
                    pdf, args.grobid_url, client, timeout=args.timeout
                )
            except Exception as exc:
                failed_count += 1
                print(
                    f"  [{i}/{total}] FAILED {pdf.stem}: {exc}",
                    file=sys.stderr,
                )
                continue

            # Save raw TEI
            tei_path = tei_dir / f"{pdf.stem}.tei.xml"
            tei_path.write_bytes(tei_bytes)

            # Parse
            sections = parse_tei(tei_bytes)
            raw_text = "\n\n".join(
                f"## {s.title}\n\n{s.text}" for s in sections
            )

            doc = ExtractedDocument(
                work_id=work_id,
                sections=sections,
                raw_text=raw_text,
                extracted_at=datetime.now(UTC).isoformat(timespec="microseconds"),
            )
            append_jsonl(doc, extraction_path)
            extracted_count += 1

            if i % 10 == 0 or i == total:
                print(f"  [{i}/{total}] processed", file=sys.stderr)
    finally:
        client.close()

    elapsed = time.monotonic() - t0

    write_meta(
        output_dir / "extraction-meta.json",
        ExtractionMeta(
            grobid_version=version,
            extracted_at=datetime.now(UTC).isoformat(timespec="microseconds"),
            total_pdfs=total,
            extracted_count=extracted_count,
            failed_count=failed_count,
            invalid_stem_count=invalid_stem_count,
        ),
    )

    print(
        f"\nExtraction done: {extracted_count} extracted, {failed_count} failed"
        f", {invalid_stem_count} skipped (invalid stem). ({elapsed:.1f}s)",
        file=sys.stderr,
    )
