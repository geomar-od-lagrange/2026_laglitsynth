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

``--catalogue`` and ``--screening-verdicts`` default to their conventional
paths under ``--data-dir``; the verdicts path takes the run-id from
``data/manifest.json`` (``laglitsynth.layout``).
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
from laglitsynth.layout import PDF_MANIFEST_FILENAME, export_dir, resolve_screening_inputs


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

Exported {export_date}. {work_count} works still need a PDF{no_doi_clause}.

## What's in this folder

- `dois.txt` — one `https://doi.org/...` link per work that has a DOI. Open
  each link through your institution's access and download the PDF.
- `missing.ris` — the same works as one RIS file, importable into a
  reference manager (Zotero, EndNote, ...) instead of working link by link.
{no_doi_bullet}\
- `pdf-manifest.csv` — the full list, with title and year, for reference.
- `README.md` — this file.

## Sending PDFs back

Put the PDFs in one folder and send it back as it is. No renaming is needed:
the import reads each PDF's DOI out of the file itself and matches it to the
list. Any subset of the works is fine — send back whatever you found.

If your reference manager can export a CSV with a `filename` and a `doi`
column for the PDFs, drop it in the same folder; it is used as a fallback
for any PDF whose DOI cannot be read from the file.
"""

_NO_DOI_BULLET = """\
- `no-doi.csv` — the {no_doi_count} {work_word} with no DOI, which `dois.txt`
  cannot list. Search for each by its `title` and `year`.
"""


def _readme_content(*, work_count: int, doi_count: int, export_date: str) -> str:
    no_doi_count = work_count - doi_count
    return _README_TEMPLATE.format(
        export_date=export_date,
        work_count=work_count,
        no_doi_clause=(
            f" ({doi_count} with a DOI, {no_doi_count} without)" if no_doi_count else ""
        ),
        no_doi_bullet=(
            _NO_DOI_BULLET.format(
                no_doi_count=no_doi_count,
                work_word="work" if no_doi_count == 1 else "works",
            )
            if no_doi_count
            else ""
        ),
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

    manifest_path = export_dir / PDF_MANIFEST_FILENAME
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
        default=None,
        help="Deduplicated catalogue JSONL (default: <data-dir>/catalogue-dedup/deduplicated.jsonl).",
    )
    parser.add_argument(
        "--screening-verdicts",
        type=Path,
        default=None,
        help="Stage 3 verdicts JSONL (default: <data-dir>/screening-abstracts/<run-id>/verdicts.jsonl).",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help=(
            "Screening run to read verdicts from (default: the run_id in "
            "<data-dir>/manifest.json). Unused when --screening-verdicts is given."
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
    bundle_dir: Path = (
        args.export_dir if args.export_dir is not None else export_dir(data_dir)
    )

    catalogue, screening_verdicts = resolve_screening_inputs(
        data_dir, args.catalogue, args.screening_verdicts, args.run_id
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
            catalogue, screening_verdicts, args.screening_threshold
        )
        if w.id not in have_pdf
    ]

    count = write_export(selection, bundle_dir)
    with_doi = sum(1 for w in selection if w.doi is not None)
    print(
        f"Exported {count} missing works to {bundle_dir} "
        f"({with_doi} with a DOI in dois.txt).",
        file=sys.stderr,
    )
