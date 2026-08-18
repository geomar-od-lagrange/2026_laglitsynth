"""Export the still-missing selection as a collaborator handoff bundle.

``fulltext-retrieval-export`` resolves the screening-gated active set, drops
works that already have a non-``missing`` provenance record, and writes a
bundle a collaborator can act on through their own institutional access:

- ``dois.txt`` — one ``https://doi.org/<doi>`` per line (DOI-bearing works).
- ``missing.ris`` — one RIS record per missing work (any reference manager).
- ``pdf-manifest.csv`` — the round-trip key import reads back: columns
  ``work_id``, ``stem``, ``doi``, ``title``, ``year``, ``expected_filename``
  (``<stem>.pdf``).
- ``no-doi.csv`` — the works ``dois.txt`` cannot represent (no DOI), with
  ``work_id``, ``stem``, ``title``, ``year``, ``expected_filename``. Always
  written, with its header row, even when empty.
- ``README.md`` — what the bundle is, what each file is for, and what to
  send back, filled in from a template with the work count and export date.

Inputs are explicit flags (manifest wiring deferred).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
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


def _title_cell(work: Work) -> str:
    return work.title or ""


def _year_cell(work: Work) -> str:
    return str(work.publication_year) if work.publication_year is not None else ""


_README_TEMPLATE = """\
# PDF handoff bundle

Exported {export_date}. {work_count} works still need a PDF ({doi_count} \
with a DOI, {no_doi_count} without).

## What's in this folder

- `dois.txt` — one `https://doi.org/...` link per DOI-bearing work. Open
  each link through your institution's access and download the PDF.
- `no-doi.csv` — the {no_doi_count} works with no DOI, so they can't go
  through `dois.txt`. Search for each by its `title` and `year` column.
- `missing.ris` — the same missing works as one RIS file, importable into
  a reference manager (Zotero, EndNote, ...) instead of working link by
  link.
- `pdf-manifest.csv` — the full list, read back by whoever imports your
  PDFs into the pipeline. Nothing for you to edit.
- `README.md` — this file.

## Sending PDFs back

Return PDFs in either of these forms:

1. A folder of PDFs each named `<stem>.pdf`, where `<stem>` is the `stem`
   column in `pdf-manifest.csv` or `no-doi.csv` (also given as
   `expected_filename` in both files).
2. A folder of PDFs with any filenames, as long as each PDF carries its
   DOI in its own metadata or on the first page of text, as most publisher
   PDFs do. Import reads the DOI out of the file to match it.

Either form works for any subset of the missing works — send back whatever
you found.
"""


def _readme_content(*, work_count: int, doi_count: int, export_date: str) -> str:
    return _README_TEMPLATE.format(
        export_date=export_date,
        work_count=work_count,
        doi_count=doi_count,
        no_doi_count=work_count - doi_count,
    )


def write_export(
    works: Iterable[Work],
    export_dir: Path,
) -> int:
    """Write the handoff bundle for ``works``; return the work count.

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
        writer.writerow(
            ["work_id", "stem", "doi", "title", "year", "expected_filename"]
        )
        for work in works:
            stem = work_id_to_filename(work.id)
            doi = _DOI_PREFIX_RE.sub("", work.doi).strip() if work.doi else ""
            writer.writerow(
                [work.id, stem, doi, _title_cell(work), _year_cell(work), f"{stem}.pdf"]
            )

    no_doi_path = export_dir / "no-doi.csv"
    with open(no_doi_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["work_id", "stem", "title", "year", "expected_filename"])
        for work in works:
            if work.doi is None:
                stem = work_id_to_filename(work.id)
                writer.writerow(
                    [work.id, stem, _title_cell(work), _year_cell(work), f"{stem}.pdf"]
                )

    doi_count = sum(1 for work in works if work.doi is not None)
    readme_path = export_dir / "README.md"
    readme_path.write_text(
        _readme_content(
            work_count=len(works),
            doi_count=doi_count,
            export_date=datetime.now(UTC).date().isoformat(),
        ),
        encoding="utf-8",
    )

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
