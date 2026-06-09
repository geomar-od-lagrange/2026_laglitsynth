"""XLSX export of stage 8 codebook records for human review.

Single subcommand ``extraction-codebook-export`` that writes a workbook with
an ``Index`` sheet plus one tab per sampled work. The Index sheet is a light
navigation aid: one row per sampled work with bibliographic columns and a
hyperlink to the per-work tab. Per-work tabs are the working surface: a
vertical layout carrying the bibliographic block, then one row per codebook
field with columns ``field | value | context | reviewer_correction``, then a
collapsed LLM-meta block. The field list is driven off the codebook (the
record model is codebook-built), not hard-coded, so a non-default codebook's
field set follows automatically. The criterion is read from the ``--codebook``
spec itself (loaded anyway to reconstruct the record model);
``extraction-codebook-meta.json`` supplies only the LLM fingerprint.

``--n-subset`` + ``--subset-seed`` draw a reproducible random sample;
``n_subset >= len(records)`` (or unset) emits the full set in record order.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import cast

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.hyperlink import Hyperlink
from openpyxl.worksheet.worksheet import Worksheet

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.config import resolve_yaml_arg
from laglitsynth.extraction_codebook.codebook import (
    CodebookContext,
    ExtractionRecordProto,
)
from laglitsynth.extraction_codebook.extract import DEFAULT_CODEBOOK
from laglitsynth.extraction_codebook.models import ExtractionCodebookMeta
from laglitsynth.io import read_jsonl

_SHEET_NAME_MAX_LEN = 31
_FORBIDDEN_SHEET_CHARS = set("/\\?*[]:")
_DEFAULT_SUBSET_SEED = 0

_REVIEWER_CORRECTION_PLACEHOLDER = (
    "<correct the value if wrong — leave blank if the value is right>"
)
_REVIEWER_REMARKS_PLACEHOLDER = (
    "<any general feedback on this record — extraction quality, "
    "metadata gaps, oddities — leave blank if none>"
)

_HYPERLINK_FONT = Font(color="0563C1", underline="single")
_BOLD = Font(bold=True)
_HEADER_FILL = PatternFill("solid", fgColor="E0E0E0")


def sample_records(
    records: list[ExtractionRecordProto],
    n_subset: int | None,
    seed: int,
) -> list[ExtractionRecordProto]:
    """Return a subset of records, preserving their original order.

    ``n_subset is None`` or ``n_subset >= len(records)`` returns the
    full list. Otherwise draw ``n_subset`` work_ids uniformly with
    ``random.Random(seed)`` and emit the matching records in the
    order they appear in ``records``.
    """
    if n_subset is None or n_subset >= len(records):
        return list(records)
    rng = random.Random(seed)
    chosen_ids = set(rng.sample([r.work_id for r in records], k=n_subset))
    return [r for r in records if r.work_id in chosen_ids]


def short_work_id(work_id: str) -> str:
    """Trailing OpenAlex id, sanitised for Excel sheet naming."""
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


def _authors_string(work: Work) -> str:
    names = [a.author.display_name for a in work.authorships]
    return ", ".join(names)


def _journal_name(work: Work) -> str:
    if work.primary_location and work.primary_location.source:
        return work.primary_location.source.display_name or ""
    return ""


def _openalex_external_url(work_id: str) -> str:
    if work_id.startswith("http://") or work_id.startswith("https://"):
        return work_id
    trailing = work_id.rsplit("/", 1)[-1]
    return f"https://openalex.org/{trailing}"


def _doi_url(doi: str) -> str:
    if doi.startswith("http://") or doi.startswith("https://"):
        return doi
    return f"https://doi.org/{doi}"


def build_index_sheet(
    ws: Worksheet, items: list[tuple[ExtractionRecordProto, Work, str]]
) -> None:
    """Index sheet with reviewer-identity header rows above the table.

    The Index is a navigation aid: bibliographic columns plus a
    hyperlink to each per-work tab. The codebook values live on the
    per-work tab, the working surface.
    """
    ws.title = "Index"

    ws.cell(row=1, column=1, value="reviewer_name").font = _BOLD
    ws.cell(row=1, column=2, value="<enter reviewer name here>")
    ws.cell(row=2, column=1, value="reviewer_email").font = _BOLD
    ws.cell(row=2, column=2, value="<enter reviewer email here>")
    ws.cell(row=3, column=1, value="review_date").font = _BOLD
    ws.cell(row=3, column=2, value="<YYYY-MM-DD>")

    headers = (
        "work_id",
        "title",
        "authors",
        "journal",
        "year",
        "sheet",
    )
    for col, header in enumerate(headers, start=1):
        cell = ws.cell(row=5, column=col, value=header)
        cell.font = _BOLD
        cell.fill = _HEADER_FILL

    for row_offset, (record, work, sheet_name) in enumerate(items):
        row = 6 + row_offset
        ws.cell(row=row, column=1, value=record.work_id)
        ws.cell(row=row, column=2, value=work.title or "")
        ws.cell(row=row, column=3, value=_authors_string(work))
        ws.cell(row=row, column=4, value=_journal_name(work))
        ws.cell(row=row, column=5, value=work.publication_year)
        link_cell = ws.cell(row=row, column=6, value=sheet_name)
        link_cell.hyperlink = Hyperlink(
            ref=link_cell.coordinate,
            location=f"'{sheet_name}'!A1",
            display=sheet_name,
        )
        link_cell.font = _HYPERLINK_FONT

    ws.freeze_panes = "A6"
    ws.column_dimensions["A"].width = 36
    ws.column_dimensions["B"].width = 60
    ws.column_dimensions["C"].width = 36
    ws.column_dimensions["D"].width = 28
    ws.column_dimensions["E"].width = 8
    ws.column_dimensions["F"].width = 20


def build_work_sheet(
    ws: Worksheet,
    record: ExtractionRecordProto,
    work: Work,
    *,
    field_names: list[str],
    criterion: str,
    llm_meta: dict[str, object],
) -> None:
    """Per-work sheet: biblio block, codebook field table, collapsed LLM-meta.

    ``field_names`` is the codebook's value-field list (in spec order);
    each row pairs the extracted value with its verbatim ``*_context``
    and an empty ``reviewer_correction`` column. ``criterion`` is the
    codebook system prompt, embedded verbatim. ``llm_meta`` carries the
    model/temperature/prompt_sha256 from ``ExtractionCodebookMeta.llm``.
    """
    wrap_top_left = Alignment(wrap_text=True, vertical="top", horizontal="left")
    top_aligned = Alignment(vertical="top")

    # Row 1: back-to-Index hyperlink
    back_cell = ws.cell(row=1, column=1, value="← back to Index")
    back_cell.hyperlink = Hyperlink(
        ref=back_cell.coordinate,
        location="'Index'!A1",
        display="← back to Index",
    )
    back_cell.font = _HYPERLINK_FONT

    # Row 2: blank

    # Bibliographic block (rows 3-8)
    biblio_rows: list[tuple[str, object]] = [
        ("title", work.title or ""),
        ("authors", _authors_string(work)),
        ("journal", _journal_name(work)),
        ("publication_year", work.publication_year),
        ("doi", None),  # filled separately to attach a hyperlink
        ("openalex", None),  # ditto
    ]
    for offset, (field, value) in enumerate(biblio_rows):
        row = 3 + offset
        f_cell = ws.cell(row=row, column=1, value=field)
        f_cell.font = _BOLD
        f_cell.alignment = top_aligned
        if field == "doi":
            doi = work.doi or ""
            v_cell = ws.cell(row=row, column=2, value=doi)
            if doi:
                v_cell.hyperlink = Hyperlink(
                    ref=v_cell.coordinate,
                    target=_doi_url(doi),
                    display=doi,
                )
                v_cell.font = _HYPERLINK_FONT
            v_cell.alignment = wrap_top_left
        elif field == "openalex":
            url = _openalex_external_url(work.id)
            v_cell = ws.cell(row=row, column=2, value=url)
            v_cell.hyperlink = Hyperlink(
                ref=v_cell.coordinate,
                target=url,
                display=url,
            )
            v_cell.font = _HYPERLINK_FONT
            v_cell.alignment = wrap_top_left
        else:
            v_cell = ws.cell(row=row, column=2, value=value)
            v_cell.alignment = wrap_top_left

    # Row 9: blank

    # Criterion (row 10)
    crit_label = ws.cell(row=10, column=1, value="criterion")
    crit_label.font = _BOLD
    crit_label.alignment = top_aligned
    crit_value = ws.cell(row=10, column=2, value=criterion)
    crit_value.alignment = wrap_top_left

    # Row 11: blank

    # Codebook field table header (row 12)
    table_headers = ("field", "value", "context", "reviewer_correction")
    for col, header in enumerate(table_headers, start=1):
        cell = ws.cell(row=12, column=col, value=header)
        cell.font = _BOLD
        cell.fill = _HEADER_FILL

    # One row per codebook value field (rows 13..).
    first_field_row = 13
    for offset, name in enumerate(field_names):
        row = first_field_row + offset
        ws.cell(row=row, column=1, value=name).font = _BOLD
        value_cell = ws.cell(row=row, column=2, value=getattr(record, name, None))
        value_cell.alignment = wrap_top_left
        context_name = f"{name}_context"
        context_cell = ws.cell(
            row=row, column=3, value=getattr(record, context_name, None)
        )
        context_cell.alignment = wrap_top_left
        corr_cell = ws.cell(
            row=row, column=4, value=_REVIEWER_CORRECTION_PLACEHOLDER
        )
        corr_cell.alignment = wrap_top_left

    next_row = first_field_row + len(field_names)

    # reviewer_remarks row
    rmk_label = ws.cell(row=next_row, column=1, value="reviewer_remarks")
    rmk_label.font = _BOLD
    rmk_label.alignment = top_aligned
    rmk_value = ws.cell(row=next_row, column=2, value=_REVIEWER_REMARKS_PLACEHOLDER)
    rmk_value.alignment = wrap_top_left

    # Row next_row+1: blank

    # LLM details — header visible, following rows collapsed.
    llm_header_row = next_row + 2
    llm_header = ws.cell(
        row=llm_header_row, column=1, value="LLM details (expand to peek)"
    )
    llm_header.font = _BOLD
    llm_header.alignment = top_aligned

    llm_rows: list[tuple[str, object]] = [
        ("reason", record.reason or ""),
        ("seed", record.seed),
        ("truncated", record.truncated),
        ("llm_model", str(llm_meta.get("model", ""))),
        ("llm_temperature", llm_meta.get("temperature")),
        ("llm_prompt_sha256", str(llm_meta.get("prompt_sha256", ""))),
        ("llm_raw_response", record.raw_response or ""),
    ]
    for offset, (label, value) in enumerate(llm_rows):
        row = llm_header_row + 1 + offset
        ws.cell(row=row, column=1, value=label).font = _BOLD
        v_cell = ws.cell(row=row, column=2, value=value)
        v_cell.alignment = wrap_top_left

    # Collapse the LLM rows; +/- button next to the visible header row.
    ws.sheet_properties.outlinePr.summaryBelow = False
    for row_idx in range(llm_header_row + 1, llm_header_row + 1 + len(llm_rows)):
        ws.row_dimensions[row_idx].outline_level = 1
        ws.row_dimensions[row_idx].hidden = True

    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 60
    ws.column_dimensions["C"].width = 60
    ws.column_dimensions["D"].width = 40
    ws.freeze_panes = "B1"


def _value_field_names(ctx: CodebookContext) -> list[str]:
    """Codebook value-field names in spec order (excludes ``*_context``)."""
    return [f.name for f in ctx.spec.fields]


def _load_meta(meta_path: Path | None) -> dict[str, object]:
    """Return the ``llm`` fingerprint dict from an extraction-codebook-meta.json.

    The criterion is read from the codebook spec (``ctx.system_prompt``)
    at export time — stage 8 loads the codebook anyway to reconstruct the
    record model — so this only reads the LLM fingerprint, falling back to
    an empty dict when the meta is missing.
    """
    if meta_path is None or not meta_path.exists():
        print(
            f"WARNING: extraction-codebook-meta.json not found at {meta_path}; "
            f"per-work sheets will omit the LLM fingerprint. Pass --meta to "
            f"point at the right file.",
            file=sys.stderr,
        )
        return {}
    raw = json.loads(meta_path.read_text())
    try:
        meta = ExtractionCodebookMeta.model_validate(raw)
        return {
            "model": meta.llm.model,
            "temperature": meta.llm.temperature,
            "prompt_sha256": meta.llm.prompt_sha256,
        }
    except Exception:
        llm_meta = raw.get("llm", {}) or {}
        return cast(dict[str, object], llm_meta)


def export_review_xlsx(
    records_path: Path,
    catalogue_path: Path,
    output_path: Path,
    *,
    ctx: CodebookContext,
    n_subset: int | None,
    seed: int = _DEFAULT_SUBSET_SEED,
    meta_path: Path | None = None,
) -> int:
    """Build and save the review workbook, returning the per-work sheet count.

    The codebook ``ctx`` reconstructs the dynamic record model used to
    read ``records.jsonl`` and supplies the field list that drives each
    per-work tab. Raises ``ValueError`` naming the first ``work_id``
    present in the records file but absent from the catalogue.
    """
    catalogue: dict[str, Work] = {w.id: w for w in read_jsonl(catalogue_path, Work)}
    records = [
        cast(ExtractionRecordProto, r)
        for r in read_jsonl(records_path, ctx.record_model)
    ]
    for record in records:
        if record.work_id not in catalogue:
            raise ValueError(
                f"work_id {record.work_id!r} in {records_path} not found in "
                f"catalogue {catalogue_path}"
            )

    selected = sample_records(records, n_subset, seed)
    field_names = _value_field_names(ctx)
    llm_meta = _load_meta(meta_path)

    wb = Workbook()
    index_ws = wb.active
    if index_ws is None:
        raise RuntimeError("openpyxl Workbook() did not produce a default worksheet")

    used_names: set[str] = set()
    items: list[tuple[ExtractionRecordProto, Work, str]] = []
    for record in selected:
        sheet_name = _unique_sheet_name(short_work_id(record.work_id), used_names)
        ws = wb.create_sheet(title=sheet_name)
        build_work_sheet(
            ws,
            record,
            catalogue[record.work_id],
            field_names=field_names,
            criterion=ctx.system_prompt,
            llm_meta=llm_meta,
        )
        items.append((record, catalogue[record.work_id], sheet_name))

    build_index_sheet(index_ws, items)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return len(selected)


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "extraction-codebook-export",
        help="Export stage 8 codebook records + catalogue to an XLSX review workbook.",
    )
    parser.add_argument(
        "--records",
        type=Path,
        required=True,
        help="Path to records.jsonl from extraction-codebook",
    )
    parser.add_argument(
        "--catalogue",
        type=Path,
        required=True,
        help="Path to the deduplicated catalogue JSONL",
    )
    parser.add_argument(
        "--codebook",
        default=str(DEFAULT_CODEBOOK),
        help=(
            "Codebook YAML used for the run — reconstructs the dynamic record "
            "model to read the records and drives the per-work field list "
            f"(default: {DEFAULT_CODEBOOK})."
        ),
    )
    parser.add_argument(
        "--meta",
        type=Path,
        default=None,
        help=(
            "Path to extraction-codebook-meta.json. Default: "
            "<records parent>/extraction-codebook-meta.json. Embeds the LLM "
            "fingerprint into each per-work sheet."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file path. Default: <records parent>/review.xlsx.",
    )
    parser.add_argument(
        "--n-subset",
        type=int,
        default=None,
        help="Random sample size; emit all when unset or >= total.",
    )
    parser.add_argument(
        "--subset-seed",
        type=int,
        default=_DEFAULT_SUBSET_SEED,
        help=f"Random seed for --n-subset (default: {_DEFAULT_SUBSET_SEED}).",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    records_path: Path = args.records

    output_path: Path = (
        args.output
        if args.output is not None
        else records_path.parent / "review.xlsx"
    )
    meta_path: Path = (
        args.meta
        if args.meta is not None
        else records_path.parent / "extraction-codebook-meta.json"
    )
    ctx = CodebookContext.from_spec(resolve_yaml_arg(args.codebook))
    count = export_review_xlsx(
        records_path,
        args.catalogue,
        output_path,
        ctx=ctx,
        n_subset=args.n_subset,
        seed=args.subset_seed,
        meta_path=meta_path,
    )
    print(
        f"Wrote workbook with {count} per-work sheets to {output_path}",
        file=sys.stderr,
    )
