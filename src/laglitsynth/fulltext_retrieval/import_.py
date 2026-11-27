"""Ingest a collaborator's returned folder of PDFs into the shared store.

``fulltext-retrieval-import`` matches each returned PDF against the whole
``--catalogue`` (default: the deduplicated catalogue), so a folder needs no
prior export: any PDF of a known work lands. It validates that the file is a
real PDF, dedups by content hash, copies matched PDFs into
``<data-dir>/pdfs/``, and upserts each work's provenance with the given
``--source`` (``zotero-desktop`` | ``browser`` | ``institutional`` |
``manual``).

Stem resolution order, per work, never guessed:

1. a self-identifying DOI that appears in the catalogue's DOI→stem map, taken
   from the Info dictionary, XMP metadata, a title-page link annotation, or
   first-page text. Every candidate is gathered and the first one present in
   the catalogue wins, because a publisher's Info dictionary often carries
   the journal's DOI rather than the article's;
2. a filename whose stem is a catalogue stem (``<stem>.pdf``, the name the
   RIS ``AN`` field gives);
3. a returned sidecar ``*.csv`` / ``*.json`` mapping the collaborator's
   filenames to DOIs;
4. a catalogue title the PDF carries, in its metadata title or the title
   block of page 1, accepted only as a clear winner (see ``title_match``),
   and only when the Info dictionary and XMP carry no DOI at all.

A PDF that resolves to no catalogue stem, or is not a real PDF, is reported
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

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.fulltext_retrieval.models import PdfProvenanceRecord, PdfSource
from laglitsynth.fulltext_retrieval.retrieve import _DOI_PREFIX_RE
from laglitsynth.fulltext_retrieval.store import (
    file_has_pdf_magic,
    load_provenance,
    sha256_of,
    store_pdf_path,
    upsert_provenance,
)
from laglitsynth.fulltext_retrieval.title_match import TitleIndex
from laglitsynth.ids import work_id_to_filename
from laglitsynth.io import read_jsonl
from laglitsynth.layout import deduplicated_path, require_input

# DOI syntax per Crossref's recommended regex (case-insensitive), plus the
# angle brackets AMS DOIs carry:
# 10.1175/1520-0485(1997)027<1038:KOTPEU>2.0.CO;2 truncates without them.
_DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:a-z0-9<>]+", re.IGNORECASE)

_IMPORT_SOURCES = {
    source.value: source
    for source in (
        PdfSource.zotero_desktop,
        PdfSource.browser,
        PdfSource.institutional,
        PdfSource.manual,
    )
}


@dataclass
class CatalogueEntry:
    work_id: str
    stem: str
    doi: str | None  # bare, as the catalogue spells it; None for DOI-less works
    title: str | None


@dataclass
class _ImportSummary:
    copied: int = 0
    skipped_not_pdf: list[str] = field(default_factory=list)
    skipped_unmatched: list[str] = field(default_factory=list)
    skipped_duplicate: list[str] = field(default_factory=list)
    skipped_existing_differs: list[str] = field(default_factory=list)


def _bare_doi(doi: str | None) -> str | None:
    """Strip any ``https://doi.org/`` / ``doi:`` prefix; keep the case (stored form)."""
    if doi is None:
        return None
    return _DOI_PREFIX_RE.sub("", doi).strip() or None


def _normalise_doi(doi: str | None) -> str | None:
    """The match key: the bare DOI, lowercased (DOIs are case-insensitive)."""
    bare = _bare_doi(doi)
    return bare.lower() if bare is not None else None


def read_catalogue_entries(path: Path) -> list[CatalogueEntry]:
    """Return the work id, stem, bare DOI and title of every work in a catalogue."""
    return [
        CatalogueEntry(
            work_id=work.id,
            stem=work_id_to_filename(work.id),
            doi=_bare_doi(work.doi),
            title=work.title,
        )
        for work in read_jsonl(path, Work)
    ]


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
    catalogue DOI ``10.1234/abc``.
    """
    return doi.rstrip(".,;:)")


@dataclass
class _PdfEvidence:
    """What a PDF says about itself, read once and used by every tier.

    ``document`` holds DOIs from the Info dictionary and XMP, and
    ``annotations`` those from title-page link annotations: each names the
    document itself. ``text`` holds every DOI on the first page, which also
    catches a corrigendum's "Correction to:" line or a cited companion paper,
    so it is only trusted when unambiguous. ``titles`` holds the Info
    ``/Title`` and XMP ``dc:title``, and ``first_page`` the extracted text of
    page 1, for title matching.
    """

    document: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    text: list[str] = field(default_factory=list)
    titles: list[str] = field(default_factory=list)
    first_page: str = ""


def _read_evidence(pdf_path: Path) -> _PdfEvidence:
    """Return every self-identifying DOI in a PDF, grouped by trust, and its titles.

    The caller keeps the first metadata candidate that appears in the
    catalogue, so returning several is what lets a wrong one be discarded — an
    AGU or Wiley Info dictionary carries the journal's ISSN-DOI, and the
    article's own DOI is printed on page 1.

    Never reads the reference list or deep body text. This corpus is topically
    clustered, so a bibliography very likely cites other works in the same
    catalogue, and a DOI harvested there would file the PDF under the wrong
    work.
    """
    found = _PdfEvidence()

    def add(into: list[str], text: str) -> None:
        for match in _DOI_RE.finditer(text):
            doi = _strip_trailing_punct(match.group(0))
            if doi not in into:
                into.append(doi)

    try:
        reader = PdfReader(str(pdf_path))
        meta = reader.metadata
        if meta is not None:
            # Indexing, unlike values(), resolves indirect objects.
            for key in list(meta.keys()):
                value = meta[key]
                if isinstance(value, str):
                    add(found.document, value)
            # An undecodable or numeric /Title comes back as a bytes or number
            # object, which says nothing usable about the title.
            title = meta.title
            if isinstance(title, str) and title:
                found.titles.append(title)
        try:
            xmp = reader.xmp_metadata
            if xmp is not None:
                add(found.document, str(xmp.stream.get_data(), "utf-8", "replace"))
                dc_title = xmp.dc_title
                if isinstance(dc_title, dict):
                    found.titles += [t for t in dc_title.values() if isinstance(t, str)]
        except (PyPdfError, OSError, ValueError, TypeError, AttributeError):
            # Malformed XMP is common and never fatal: other sources remain.
            pass
        try:
            # Two pages: some publishers put a cover sheet ahead of the title page.
            for page in reader.pages[:2]:
                for annotation in page.get("/Annots") or []:
                    uri = (annotation.get_object().get("/A") or {}).get("/URI")
                    if uri is not None:
                        add(found.annotations, str(uri))
        except (PyPdfError, OSError, ValueError, TypeError, AttributeError):
            # Damaged or absent annotation objects; other sources remain.
            pass
        if reader.pages:
            found.first_page = reader.pages[0].extract_text() or ""
            add(found.text, found.first_page)
    except (PyPdfError, OSError, ValueError) as exc:
        # A malformed PDF yields no embedded DOI or title; later strategies still apply.
        print(f"  ! could not parse {pdf_path.name} for DOI or title: {exc}", file=sys.stderr)
    return found


def _resolve_stem(
    pdf_path: Path,
    *,
    doi_to_stem: dict[str, str],
    stems: set[str],
    sidecar: dict[str, str],
    titles: TitleIndex,
) -> str | None:
    """Resolve a returned PDF to a catalogue stem, or None if unmatched."""
    # (a) a self-identifying DOI that lands in the catalogue. Membership
    # decides, not position: the first candidate is often a journal-level DOI.
    found = _read_evidence(pdf_path)
    for candidate in found.document + found.annotations:
        embedded = _normalise_doi(candidate)
        if embedded is not None and embedded in doi_to_stem:
            return doi_to_stem[embedded]
    # First-page text is accepted only when it points at exactly one work;
    # two catalogue DOIs on page 1 is a correction notice or a cited companion,
    # and guessing between them would file the PDF under the wrong work.
    text_hits = {
        doi_to_stem[embedded]
        for candidate in found.text
        if (embedded := _normalise_doi(candidate)) is not None
        and embedded in doi_to_stem
    }
    if len(text_hits) == 1:
        return text_hits.pop()
    if len(text_hits) > 1:
        print(
            f"  ! {pdf_path.name}: first page names {len(text_hits)} catalogue works "
            f"({', '.join(sorted(text_hits))}); not guessing",
            file=sys.stderr,
        )

    # (b) filename stem matching a catalogue stem directly.
    if pdf_path.stem in stems:
        return pdf_path.stem

    # (c) sidecar filename → DOI → stem.
    sidecar_doi = _normalise_doi(sidecar.get(pdf_path.name))
    if sidecar_doi is not None and sidecar_doi in doi_to_stem:
        return doi_to_stem[sidecar_doi]

    # (d) a catalogue title, only as a clear winner; a near-tie is reported.
    # Only for a PDF that does not name itself: an Info or XMP DOI outside the
    # catalogue says this is some other document (an erratum, a reply, a book
    # review) even when it prints a catalogue title. Link annotations and
    # page text do not count, since they also point at cited or corrected
    # works.
    if found.document:
        print(
            f"  ! {pdf_path.name}: metadata names DOI {found.document[0]}, "
            "not in the catalogue; not matching by title",
            file=sys.stderr,
        )
        return None
    by_title = titles.match(found.titles, found.first_page)
    if by_title.contenders:
        print(
            f"  ! {pdf_path.name}: title fits {len(by_title.contenders)} catalogue works "
            f"about equally ({', '.join(by_title.contenders)}); not guessing",
            file=sys.stderr,
        )
    return by_title.stem


def import_pdfs(
    import_dir: Path,
    catalogue_path: Path,
    data_dir: Path,
    source: PdfSource,
    *,
    overwrite: bool,
) -> tuple[_ImportSummary, int]:
    """Import PDFs from ``import_dir``; return ``(summary, still_missing)``.

    ``still_missing`` is the number of catalogue works without a non-missing
    provenance record after this import.
    """
    entries = read_catalogue_entries(catalogue_path)
    by_stem = {e.stem: e for e in entries}
    stems = set(by_stem)
    doi_to_stem: dict[str, str] = {}
    for e in entries:
        norm = _normalise_doi(e.doi)
        if norm is not None:
            doi_to_stem[norm] = e.stem
    sidecar = _load_sidecar(import_dir)
    titles = TitleIndex({e.stem: e.title for e in entries})

    provenance = load_provenance(data_dir)
    summary = _ImportSummary()

    for pdf_path in sorted(import_dir.glob("*.pdf")):
        if not file_has_pdf_magic(pdf_path):
            summary.skipped_not_pdf.append(pdf_path.name)
            continue

        stem = _resolve_stem(
            pdf_path,
            doi_to_stem=doi_to_stem,
            stems=stems,
            sidecar=sidecar,
            titles=titles,
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
        "--catalogue",
        type=Path,
        default=None,
        help=(
            "Catalogue JSONL whose works the PDFs are matched against "
            "(default: <data-dir>/catalogue-dedup/deduplicated.jsonl)."
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
    catalogue = require_input(
        args.catalogue if args.catalogue is not None else deduplicated_path(data_dir),
        flag="--catalogue",
        upstream="catalogue-dedup",
    )
    summary, still_missing = import_pdfs(
        args.import_dir,
        catalogue,
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
            f"  Skipped (no catalogue match): {len(summary.skipped_unmatched)} "
            f"({', '.join(summary.skipped_unmatched)})",
            file=sys.stderr,
        )
    print(f"  Catalogue works still missing: {still_missing}", file=sys.stderr)
