"""Export the still-missing selection as a collaborator handoff bundle.

``fulltext-retrieval-export`` resolves the screening-gated active set, drops
works that already have a non-``missing`` provenance record, and writes a
bundle a collaborator can act on through their own institutional access:

- ``dois.txt`` — one ``https://doi.org/<doi>`` per line (DOI-bearing works).
- ``missing.ris`` — one RIS record per missing work (any reference manager).
- ``pdf-manifest.csv`` — the round-trip key import reads back: columns
  ``work_id``, ``stem``, ``doi``, ``expected_filename`` (``<stem>.pdf``).

Inputs are explicit flags (manifest wiring deferred).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Iterable
from pathlib import Path

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.fulltext_retrieval.models import PdfSource
from laglitsynth.fulltext_retrieval.retrieve import _DOI_PREFIX_RE, _active_works
from laglitsynth.fulltext_retrieval.store import load_provenance
from laglitsynth.ids import work_id_to_filename

DEFAULT_EXPORT_SUBDIR = "pdfs/export"


def _doi_url(doi: str) -> str:
    bare = _DOI_PREFIX_RE.sub("", doi).strip()
    return f"https://doi.org/{bare}"


def _journal_name(work: Work) -> str:
    if work.primary_location is not None and work.primary_location.source is not None:
        return work.primary_location.source.display_name or ""
    return ""


def _ris_record(work: Work) -> str:
    """Render one RIS record for a work.

    Uses ``JOUR`` as the reference type. Missing fields are omitted (their
    tags are simply not emitted), per "None means None". Each record ends
    with the mandatory ``ER  -`` terminator.
    """
    lines: list[str] = ["TY  - JOUR"]
    if work.title is not None:
        lines.append(f"TI  - {work.title}")
    for authorship in work.authorships:
        lines.append(f"AU  - {authorship.author.display_name}")
    if work.publication_year is not None:
        lines.append(f"PY  - {work.publication_year}")
    journal = _journal_name(work)
    if journal:
        lines.append(f"JO  - {journal}")
    if work.doi is not None:
        lines.append(f"DO  - {_DOI_PREFIX_RE.sub('', work.doi).strip()}")
    lines.append("ER  - ")
    return "\n".join(lines)


def write_export(
    works: Iterable[Work],
    export_dir: Path,
) -> int:
    """Write the three handoff files for ``works``; return the work count.

    ``works`` is the already-filtered missing selection.
    """
    works = list(works)
    export_dir.mkdir(parents=True, exist_ok=True)

    dois_path = export_dir / "dois.txt"
    with open(dois_path, "w", encoding="utf-8") as f:
        for work in works:
            if work.doi is not None:
                f.write(_doi_url(work.doi) + "\n")

    ris_path = export_dir / "missing.ris"
    with open(ris_path, "w", encoding="utf-8") as f:
        for work in works:
            f.write(_ris_record(work) + "\n")

    manifest_path = export_dir / "pdf-manifest.csv"
    with open(manifest_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["work_id", "stem", "doi", "expected_filename"])
        for work in works:
            stem = work_id_to_filename(work.id)
            doi = _DOI_PREFIX_RE.sub("", work.doi).strip() if work.doi else ""
            writer.writerow([work.id, stem, doi, f"{stem}.pdf"])

    return len(works)


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "fulltext-retrieval-export",
        help="Export the still-missing selection as a collaborator handoff bundle.",
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
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Project data directory holding the pdfs/ store (default: data).",
    )
    parser.add_argument(
        "--export-dir",
        type=Path,
        default=None,
        help="Bundle output directory (default: <data-dir>/pdfs/export/).",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    data_dir: Path = args.data_dir
    export_dir: Path = (
        args.export_dir
        if args.export_dir is not None
        else data_dir / DEFAULT_EXPORT_SUBDIR
    )

    provenance = load_provenance(data_dir)
    have_pdf = {
        wid
        for wid, rec in provenance.items()
        if rec.source != PdfSource.missing
    }

    selection = [
        w
        for w in _active_works(
            args.catalogue, args.screening_verdicts, args.screening_threshold
        )
        if w.id not in have_pdf
    ]

    count = write_export(selection, export_dir)
    with_doi = sum(1 for w in selection if w.doi is not None)
    print(
        f"Exported {count} missing works to {export_dir} "
        f"({with_doi} with a DOI in dois.txt).",
        file=sys.stderr,
    )
