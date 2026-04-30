"""CSV and XLSX export of stage 3 verdicts for human review.

Single subcommand ``screening-abstracts-export`` with ``--format csv|xlsx``.

CSV mode writes a flat ``review.csv`` a reviewer opens in Excel, Numbers, or
Keynote. Two empty columns (``reviewer_decision``, ``reviewer_reason``) are
filled in the spreadsheet; a stage-4 ingestor will read them back.

XLSX mode writes a workbook with an ``Index`` sheet plus one tab per included
work. Per-work tabs use a vertical ``Field | Value`` layout so abstract and
``raw_response`` wrap vertically without horizontal scrolling.
``--n-subset`` + ``--subset-seed`` draw a reproducible random sample;
``n_subset >= len(verdicts)`` (or unset) emits the full set in verdict order.
``--n-subset`` and ``--subset-seed`` are valid only with ``--format xlsx``.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.hyperlink import Hyperlink
from openpyxl.worksheet.worksheet import Worksheet

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.io import read_jsonl, read_works_jsonl
from laglitsynth.screening_abstracts.models import ScreeningVerdict

# ── CSV ───────────────────────────────────────────────────────────────────────

COLUMNS: tuple[str, ...] = (
    "work_id",
    "title",
    "doi",
    "publication_year",
    "abstract",
    "relevance_score",
    "llm_reason",
    "reviewer_decision",
    "reviewer_reason",
    "raw_response",
)


def build_row(verdict: ScreeningVerdict, work: Work) -> dict[str, str]:
    """One CSV row. Missing values render as empty cells."""
    return {
        "work_id": verdict.work_id,
        "title": work.title or "",
        "doi": work.doi or "",
        "publication_year": (
            str(work.publication_year) if work.publication_year is not None else ""
        ),
        "abstract": work.abstract or "",
        "relevance_score": (
            str(verdict.relevance_score)
            if verdict.relevance_score is not None
            else ""
        ),
        "llm_reason": verdict.reason or "",
        "reviewer_decision": "",
        "reviewer_reason": "",
        "raw_response": verdict.raw_response or "",
    }


def export_review_csv(
    verdicts_path: Path,
    catalogue_path: Path,
    output_path: Path,
) -> int:
    """Write the review CSV and return the number of data rows.

    Raises ``ValueError`` naming the first ``work_id`` present in the
    verdicts file but absent from the catalogue.
    """
    catalogue: dict[str, Work] = {w.id: w for w in read_works_jsonl(catalogue_path)}
    rows: list[dict[str, str]] = []
    for verdict in read_jsonl(verdicts_path, ScreeningVerdict):
        work = catalogue.get(verdict.work_id)
        if work is None:
            raise ValueError(
                f"work_id {verdict.work_id!r} in {verdicts_path} not found in "
                f"catalogue {catalogue_path}"
            )
        rows.append(build_row(verdict, work))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(COLUMNS))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


# ── XLSX ──────────────────────────────────────────────────────────────────────

_SHEET_NAME_MAX_LEN = 31
_FORBIDDEN_SHEET_CHARS = set("/\\?*[]:")
_DEFAULT_SUBSET_SEED = 0


def sample_verdicts(
    verdicts: list[ScreeningVerdict],
    n_subset: int | None,
    seed: int,
) -> list[ScreeningVerdict]:
    """Return a subset of verdicts, preserving their original order.

    ``n_subset is None`` or ``n_subset >= len(verdicts)`` returns the
    full list. Otherwise draw ``n_subset`` work_ids uniformly with
    ``random.Random(seed)`` and emit the matching verdicts in the
    order they appear in ``verdicts``.
    """
    if n_subset is None or n_subset >= len(verdicts):
        return list(verdicts)
    rng = random.Random(seed)
    chosen_ids = set(rng.sample([v.work_id for v in verdicts], k=n_subset))
    return [v for v in verdicts if v.work_id in chosen_ids]


def short_work_id(work_id: str) -> str:
    """Trailing OpenAlex id, sanitised for Excel sheet naming.

    Strips host/path prefix, replaces characters Excel forbids in
    sheet names with ``_``, truncates to 31 chars.
    """
    name = work_id.rsplit("/", 1)[-1] if "/" in work_id else work_id
    name = "".join("_" if c in _FORBIDDEN_SHEET_CHARS else c for c in name)
    name = name.strip("'")
    return name[:_SHEET_NAME_MAX_LEN] or "work"


def _unique_sheet_name(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    for i in range(2, 10**6):
        suffix = f"_{i}"
        candidate = f"{base[: _SHEET_NAME_MAX_LEN - len(suffix)]}{suffix}"
        if candidate not in used:
            used.add(candidate)
            return candidate
    raise RuntimeError(f"Could not resolve unique sheet name for {base!r}")


def build_index_sheet(
    ws: Worksheet, items: list[tuple[ScreeningVerdict, Work, str]]
) -> None:
    ws.title = "Index"
    bold = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="E0E0E0")

    headers = ("work_id", "title", "relevance_score", "llm_reason", "sheet")
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = bold
        cell.fill = header_fill

    for row_idx, (verdict, work, sheet_name) in enumerate(items, start=2):
        ws.cell(row=row_idx, column=1, value=verdict.work_id)
        ws.cell(row=row_idx, column=2, value=work.title or "")
        ws.cell(row=row_idx, column=3, value=verdict.relevance_score)
        ws.cell(row=row_idx, column=4, value=verdict.reason or "")
        link_cell = ws.cell(row=row_idx, column=5, value=sheet_name)
        # Canonical in-workbook hyperlink: location="'Sheet'!A1" — Excel,
        # Numbers, and LibreOffice all follow this form.
        link_cell.hyperlink = Hyperlink(
            ref=link_cell.coordinate,
            location=f"'{sheet_name}'!A1",
            display=sheet_name,
        )
        link_cell.font = Font(color="0563C1", underline="single")

    ws.freeze_panes = "A2"
    ws.column_dimensions["A"].width = 36
    ws.column_dimensions["B"].width = 80
    ws.column_dimensions["C"].width = 16
    ws.column_dimensions["D"].width = 60
    ws.column_dimensions["E"].width = 20


def build_work_sheet(ws: Worksheet, verdict: ScreeningVerdict, work: Work) -> None:
    bold = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")

    # (field, value, row_height_points) — height=None keeps the default.
    rows: list[tuple[str, object, int | None]] = [
        ("work_id", verdict.work_id, None),
        ("title", work.title or "", 30),
        ("doi", work.doi or "", None),
        ("publication_year", work.publication_year, None),
        ("", "", None),
        ("abstract", work.abstract or "", 200),
        ("", "", None),
        ("relevance_score", verdict.relevance_score, None),
        ("llm_reason", verdict.reason or "", 60),
        ("", "", None),
        ("reviewer_decision", "", None),
        ("reviewer_reason", "", 60),
        ("", "", None),
        ("raw_response", verdict.raw_response or "", 260),
    ]

    for idx, (field, value, height) in enumerate(rows, start=1):
        field_cell = ws.cell(row=idx, column=1, value=field)
        field_cell.font = bold
        value_cell = ws.cell(row=idx, column=2, value=value)
        value_cell.alignment = wrap
        if height is not None:
            ws.row_dimensions[idx].height = height

    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 110
    ws.freeze_panes = "B1"


def export_review_xlsx(
    verdicts_path: Path,
    catalogue_path: Path,
    output_path: Path,
    *,
    n_subset: int | None,
    seed: int = _DEFAULT_SUBSET_SEED,
) -> int:
    """Build and save the review workbook, returning the per-work sheet count."""
    catalogue: dict[str, Work] = {w.id: w for w in read_works_jsonl(catalogue_path)}
    verdicts = list(read_jsonl(verdicts_path, ScreeningVerdict))
    for verdict in verdicts:
        if verdict.work_id not in catalogue:
            raise ValueError(
                f"work_id {verdict.work_id!r} in {verdicts_path} not found in "
                f"catalogue {catalogue_path}"
            )

    selected = sample_verdicts(verdicts, n_subset, seed)

    wb = Workbook()
    index_ws = wb.active
    if index_ws is None:
        raise RuntimeError("openpyxl Workbook() did not produce a default worksheet")

    used_names: set[str] = set()
    items: list[tuple[ScreeningVerdict, Work, str]] = []
    for verdict in selected:
        sheet_name = _unique_sheet_name(short_work_id(verdict.work_id), used_names)
        ws = wb.create_sheet(title=sheet_name)
        build_work_sheet(ws, verdict, catalogue[verdict.work_id])
        items.append((verdict, catalogue[verdict.work_id], sheet_name))

    build_index_sheet(index_ws, items)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return len(selected)


# ── CLI ───────────────────────────────────────────────────────────────────────


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "screening-abstracts-export",
        help="Export stage 3 verdicts + catalogue to CSV or XLSX.",
    )
    parser.add_argument(
        "--format",
        choices=["csv", "xlsx"],
        required=True,
        help="Output format: 'csv' for flat review spreadsheet, 'xlsx' for per-work workbook.",
    )
    parser.add_argument(
        "--verdicts",
        type=Path,
        required=True,
        help="Path to verdicts.jsonl from screening-abstracts",
    )
    parser.add_argument(
        "--catalogue",
        type=Path,
        required=True,
        help="Path to the deduplicated catalogue JSONL",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Output file path. Default: <verdicts parent>/review.csv (CSV) or "
            "<verdicts parent>/review.xlsx (XLSX)."
        ),
    )
    parser.add_argument(
        "--n-subset",
        type=int,
        default=None,
        help="Random sample size; emit all when unset or >= total. XLSX only.",
    )
    parser.add_argument(
        "--subset-seed",
        type=int,
        default=_DEFAULT_SUBSET_SEED,
        help=f"Random seed for --n-subset (default: {_DEFAULT_SUBSET_SEED}). XLSX only.",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    verdicts_path: Path = args.verdicts

    if args.format == "csv":
        if args.n_subset is not None or args.subset_seed != _DEFAULT_SUBSET_SEED:
            sys.exit(
                "--n-subset and --subset-seed are only valid with --format xlsx"
            )
        output_path: Path = (
            args.output
            if args.output is not None
            else verdicts_path.parent / "review.csv"
        )
        count = export_review_csv(verdicts_path, args.catalogue, output_path)
        print(f"Wrote {count} rows to {output_path}", file=sys.stderr)

    else:  # xlsx
        output_path = (
            args.output
            if args.output is not None
            else verdicts_path.parent / "review.xlsx"
        )
        count = export_review_xlsx(
            verdicts_path,
            args.catalogue,
            output_path,
            n_subset=args.n_subset,
            seed=args.subset_seed,
        )
        print(
            f"Wrote workbook with {count} per-work sheets to {output_path}",
            file=sys.stderr,
        )
