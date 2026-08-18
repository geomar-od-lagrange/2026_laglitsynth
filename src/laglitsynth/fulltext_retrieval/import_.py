"""Ingest a collaborator's returned folder of PDFs into the shared store.

``fulltext-retrieval-import`` reads the ``pdf-manifest.csv`` written by a
matching export, resolves each returned PDF to a work stem, validates it is
a real PDF, dedups by content hash, copies matched PDFs into
``<data-dir>/pdfs/``, and upserts each work's provenance with the given
``--source`` (``zotero-import`` | ``manual``).

Stem resolution order, per work, never guessed:

1. a self-identifying DOI that appears in the manifest's DOI→stem map, taken
   from the Info dictionary, XMP metadata, a title-page link annotation, or
   first-page text. Every candidate is gathered and the first one present in
   the manifest wins, because a publisher's Info dictionary often carries the
   journal's DOI rather than the article's;
2. a filename whose stem matches the manifest directly (plain folder drop);
3. a returned sidecar ``*.csv`` / ``*.json`` mapping the collaborator's
   filenames to DOIs.

A PDF that resolves to no manifest stem, or is not a real PDF, is reported
and skipped.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PyPdfError

from laglitsynth.fulltext_retrieval.export import PDF_MANIFEST_FILENAME
from laglitsynth.fulltext_retrieval.models import PdfProvenanceRecord, PdfSource
from laglitsynth.fulltext_retrieval.retrieve import _DOI_PREFIX_RE, _validate_pdf
from laglitsynth.fulltext_retrieval.store import (
    load_provenance,
    pdfs_dir,
    sha256_of,
    store_pdf_path,
    upsert_provenance,
)
from laglitsynth.manifest import record_stage, resolve_input

# DOI syntax per Crossref's recommended regex (case-insensitive), plus the
# angle brackets AMS DOIs carry:
# 10.1175/1520-0485(1997)027<1038:KOTPEU>2.0.CO;2 truncates without them.
_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9<>]+", re.IGNORECASE)

_IMPORT_SOURCES = {
    "zotero-import": PdfSource.zotero_import,
    "manual": PdfSource.manual,
}


@dataclass
class ManifestEntry:
    work_id: str
    stem: str
    doi: str | None
    title: str | None
    year: int | None


@dataclass
class _ImportSummary:
    copied: int = 0
    skipped_not_pdf: list[str] = field(default_factory=list)
    skipped_unmatched: list[str] = field(default_factory=list)
    skipped_duplicate: list[str] = field(default_factory=list)
    skipped_existing_differs: list[str] = field(default_factory=list)


def _normalise_doi(doi: str | None) -> str | None:
    if doi is None:
        return None
    bare = _DOI_PREFIX_RE.sub("", doi).strip().lower()
    return bare or None


def read_manifest(path: Path) -> list[ManifestEntry]:
    """Parse ``pdf-manifest.csv`` into ``ManifestEntry`` rows."""
    entries: list[ManifestEntry] = []
    with open(path, newline="", encoding="utf-8") as f:
        # The export also writes an ``expected_filename`` column; it is an
        # advisory round-trip hint for the collaborator (written, never read
        # back here), so it is not pulled into the entry. ``title``/``year``
        # are read but not yet used for matching (precursor for a future
        # title-matching tier).
        for row in csv.DictReader(f):
            doi = row.get("doi") or None
            title = row.get("title") or None
            year_raw = row.get("year") or None
            entries.append(
                ManifestEntry(
                    work_id=row["work_id"],
                    stem=row["stem"],
                    doi=doi if doi else None,
                    title=title,
                    year=int(year_raw) if year_raw else None,
                )
            )
    return entries


def _load_sidecar(import_dir: Path) -> dict[str, str]:
    """Return a filename→DOI map from any ``*.csv``/``*.json`` sidecars.

    A CSV sidecar must have ``filename`` and ``doi`` columns. A JSON sidecar
    must be an object mapping filename to DOI. Sidecars are optional; absent
    ones contribute nothing.
    """
    mapping: dict[str, str] = {}
    for csv_path in sorted(import_dir.glob("*.csv")):
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or "filename" not in reader.fieldnames:
                continue
            for row in reader:
                fname = row.get("filename")
                doi = row.get("doi")
                if fname and doi:
                    mapping[fname] = doi
    for json_path in sorted(import_dir.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            for fname, doi in data.items():
                if isinstance(fname, str) and isinstance(doi, str):
                    mapping[fname] = doi
    return mapping


def _strip_trailing_punct(doi: str) -> str:
    """Drop trailing sentence punctuation a DOI match may pick up from prose.

    ``_DOI_RE`` greedily matches ``[-._;()/:a-z0-9]`` characters, so a DOI
    rendered in running text as ``10.1234/abc.`` (or followed by ``,;:)``)
    captures the punctuation. Stripping it lets such a match equal the bare
    manifest DOI ``10.1234/abc``.
    """
    return doi.rstrip(".,;:)")


def _embedded_dois(pdf_path: Path) -> list[str]:
    """Return every self-identifying DOI in a PDF, most trustworthy first.

    Four sources, in trust order: the Info dictionary, XMP metadata,
    title-page link annotations, and first-page text. The caller keeps the
    first candidate that appears in the manifest, so returning several is what
    lets a wrong one be discarded — an AGU or Wiley Info dictionary carries the
    journal's ISSN-DOI, and the article's own DOI is printed on page 1.

    Never reads the reference list or deep body text. This corpus is topically
    clustered, so a bibliography very likely cites other works in the same
    manifest, and a DOI harvested there would file the PDF under the wrong
    work.
    """
    candidates: list[str] = []

    def add(text: str) -> None:
        for match in _DOI_RE.finditer(text):
            doi = _strip_trailing_punct(match.group(0))
            if doi not in candidates:
                candidates.append(doi)

    try:
        reader = PdfReader(str(pdf_path))
        meta = reader.metadata
        if meta is not None:
            for value in meta.values():
                if isinstance(value, str):
                    add(value)
        try:
            xmp = reader.xmp_metadata
            if xmp is not None:
                add(str(xmp.stream.get_data(), "utf-8", "replace"))
        except (PyPdfError, OSError, ValueError, TypeError, AttributeError):
            # Malformed XMP is common and never fatal: other sources remain.
            pass
        try:
            for page in reader.pages[:1]:
                for annotation in page.get("/Annots") or []:
                    uri = (annotation.get_object().get("/A") or {}).get("/URI")
                    if uri is not None:
                        add(str(uri))
        except (PyPdfError, OSError, ValueError, TypeError, AttributeError):
            # Damaged or absent annotation objects; other sources remain.
            pass
        if reader.pages:
            add(reader.pages[0].extract_text() or "")
    except (PyPdfError, OSError, ValueError) as exc:
        # A malformed PDF yields no embedded DOI; later strategies still apply.
        print(f"  ! could not parse {pdf_path.name} for embedded DOI: {exc}", file=sys.stderr)
    return candidates


def _resolve_stem(
    pdf_path: Path,
    *,
    doi_to_stem: dict[str, str],
    stems: set[str],
    sidecar: dict[str, str],
) -> str | None:
    """Resolve a returned PDF to a manifest stem, or None if unmatched."""
    # (a) a self-identifying DOI that lands in the manifest. Membership
    # decides, not position: the first candidate is often a journal-level DOI.
    for candidate in _embedded_dois(pdf_path):
        embedded = _normalise_doi(candidate)
        if embedded is not None and embedded in doi_to_stem:
            return doi_to_stem[embedded]

    # (b) filename stem matching the manifest directly.
    if pdf_path.stem in stems:
        return pdf_path.stem

    # (c) sidecar filename → DOI → stem.
    sidecar_doi = _normalise_doi(sidecar.get(pdf_path.name))
    if sidecar_doi is not None and sidecar_doi in doi_to_stem:
        return doi_to_stem[sidecar_doi]

    return None


def import_pdfs(
    import_dir: Path,
    manifest_path: Path,
    data_dir: Path,
    source: PdfSource,
    *,
    overwrite: bool,
) -> tuple[_ImportSummary, int]:
    """Import PDFs from ``import_dir``; return ``(summary, still_missing)``.

    ``still_missing`` is the number of manifest works without a non-missing
    provenance record after this import.
    """
    entries = read_manifest(manifest_path)
    by_stem = {e.stem: e for e in entries}
    stems = set(by_stem)
    doi_to_stem: dict[str, str] = {}
    for e in entries:
        norm = _normalise_doi(e.doi)
        if norm is not None:
            doi_to_stem[norm] = e.stem
    sidecar = _load_sidecar(import_dir)

    provenance = load_provenance(data_dir)
    summary = _ImportSummary()

    for pdf_path in sorted(import_dir.glob("*.pdf")):
        with open(pdf_path, "rb") as f:
            head = f.read(4)
        if not _validate_pdf(head):
            summary.skipped_not_pdf.append(pdf_path.name)
            continue

        stem = _resolve_stem(
            pdf_path, doi_to_stem=doi_to_stem, stems=stems, sidecar=sidecar
        )
        if stem is None:
            summary.skipped_unmatched.append(pdf_path.name)
            continue

        new_sha = sha256_of(pdf_path)
        dest = store_pdf_path(data_dir, stem)
        existing = provenance.get(by_stem[stem].work_id)
        if dest.exists():
            if existing is not None and existing.content_sha256 == new_sha:
                summary.skipped_duplicate.append(pdf_path.name)
                continue
            if not overwrite:
                summary.skipped_existing_differs.append(pdf_path.name)
                continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, dest)
        entry = by_stem[stem]
        upsert_provenance(
            data_dir,
            provenance,
            PdfProvenanceRecord(
                work_id=entry.work_id,
                stem=stem,
                doi=entry.doi,
                source=source,
                source_url=None,
                pdf_path=f"pdfs/{stem}.pdf",
                content_sha256=new_sha,
                obtained_at=_now(),
            ),
        )
        summary.copied += 1

    still_missing = sum(
        1
        for e in entries
        if (rec := provenance.get(e.work_id)) is None
        or rec.source == PdfSource.missing
    )
    return summary, still_missing


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "fulltext-retrieval-import",
        help="Ingest a collaborator's returned folder of PDFs into the store.",
    )
    parser.add_argument(
        "--import-dir",
        type=Path,
        required=True,
        help="Folder of PDFs a collaborator shipped back.",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help=(
            "pdf-manifest.csv from the matching export. Resolved from "
            "data/manifest.json's fulltext-retrieval-export entry when omitted."
        ),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Project data directory holding the pdfs/ store (default: data).",
    )
    parser.add_argument(
        "--source",
        choices=sorted(_IMPORT_SOURCES),
        required=True,
        help="Provenance source to record for ingested PDFs.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing PDF whose content differs (default: keep existing).",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    data_dir: Path = args.data_dir
    source = _IMPORT_SOURCES[args.source]
    manifest_path = resolve_input(
        data_dir,
        args.manifest,
        upstream_stage="fulltext-retrieval-export",
        flag_name="--manifest",
        subpath=PDF_MANIFEST_FILENAME,
    )
    summary, still_missing = import_pdfs(
        args.import_dir,
        manifest_path,
        data_dir,
        source,
        overwrite=args.overwrite,
    )

    print("\nImport summary:", file=sys.stderr)
    print(f"  Matched and copied:     {summary.copied}", file=sys.stderr)
    if summary.skipped_duplicate:
        print(
            f"  Skipped (already present): {len(summary.skipped_duplicate)}",
            file=sys.stderr,
        )
    if summary.skipped_existing_differs:
        print(
            f"  Skipped (differs, no --overwrite): "
            f"{len(summary.skipped_existing_differs)}",
            file=sys.stderr,
        )
    if summary.skipped_not_pdf:
        print(
            f"  Skipped (not a PDF): {len(summary.skipped_not_pdf)} "
            f"({', '.join(summary.skipped_not_pdf)})",
            file=sys.stderr,
        )
    if summary.skipped_unmatched:
        print(
            f"  Skipped (no manifest match): {len(summary.skipped_unmatched)} "
            f"({', '.join(summary.skipped_unmatched)})",
            file=sys.stderr,
        )
    print(f"  Manifest works still missing: {still_missing}", file=sys.stderr)

    record_stage(
        data_dir,
        stage="fulltext-retrieval-import",
        inputs={"import_dir": args.import_dir, "manifest": manifest_path},
        output=pdfs_dir(data_dir),
        meta_path=None,
    )
