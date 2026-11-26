"""``corpus-assemble``: the selected works with copies of their PDFs.

The one place the screening threshold is applied. Every catalogue work
whose screening verdict scores ``None`` or at least the threshold is
selected; a work without a verdict row stops the command unless
``--allow-unscreened`` includes it. The command writes into
``<data-dir>/corpus.tmp/`` and then replaces ``<data-dir>/corpus/`` with
it, so the corpus is always rewritten whole:

- ``works.jsonl``: the selected ``Work`` records.
- ``verdicts.jsonl``: their ``ScreeningVerdict`` rows.
- ``pdfs/<stem>.pdf``: copies from the store ``<data-dir>/pdfs/``.
- ``provenance.jsonl``: one ``CorpusPdf`` per selected work.
- ``corpus-meta.json``: ``CorpusMeta``.

``corpus_id`` hashes membership and PDF bytes only, so an identical
re-assembly keeps the id and the analysis run-id.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.corpus.models import (
    META_FILENAME,
    PDFS_SUBDIR,
    PROVENANCE_FILENAME,
    TOOL_NAME,
    VERDICTS_FILENAME,
    WORKS_FILENAME,
    CorpusMeta,
    CorpusPdf,
)
from laglitsynth.fulltext_retrieval.models import PdfProvenanceRecord, PdfSource
from laglitsynth.fulltext_retrieval.store import (
    file_has_pdf_magic,
    load_provenance,
    sha256_of,
    store_pdf_path,
)
from laglitsynth.ids import generate_run_id
from laglitsynth.io import JsonlReadStats, read_jsonl, write_jsonl, write_meta
from laglitsynth.layout import corpus_dir, resolve_screening_inputs
from laglitsynth.manifest import resolve_run_id
from laglitsynth.models import RunMeta
from laglitsynth.screening_abstracts.models import ScreeningMeta, ScreeningVerdict

SCREENING_META_FILENAME = "screening-meta.json"


def compute_corpus_id(pdfs: list[CorpusPdf]) -> str:
    """Return the sha256 of the sorted ``work_id\\tcontent_sha256`` lines.

    Independent of catalogue order, threshold and assembly time; changes
    with membership and with any PDF's bytes.
    """
    lines = sorted(f"{p.work_id}\t{p.content_sha256 or ''}" for p in pdfs)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _load_screening_meta(verdicts_path: Path) -> ScreeningMeta:
    path = verdicts_path.parent / SCREENING_META_FILENAME
    if not path.exists():
        raise SystemExit(
            f"{path} not found; corpus-assemble needs the screening meta "
            f"next to {verdicts_path}"
        )
    try:
        return ScreeningMeta.model_validate_json(path.read_text())
    except ValidationError as exc:
        raise SystemExit(
            f"{path} does not validate as ScreeningMeta:\n{exc}"
        ) from exc


def _load_verdicts(path: Path) -> dict[str, ScreeningVerdict]:
    stats = JsonlReadStats()
    verdicts = {v.work_id: v for v in read_jsonl(path, ScreeningVerdict, stats)}
    if stats.skipped:
        raise SystemExit(
            f"{path}: {stats.skipped} row(s) fail validation as ScreeningVerdict; "
            f"nothing written"
        )
    return verdicts


def _previous_meta(directory: Path) -> CorpusMeta | None:
    path = directory / META_FILENAME
    if not path.exists():
        return None
    try:
        return CorpusMeta.model_validate_json(path.read_text())
    except ValidationError:
        print(
            f"{path} does not validate as CorpusMeta; minting a fresh analysis run-id.",
            file=sys.stderr,
        )
        return None


def _copy_pdf(
    data_dir: Path,
    record: PdfProvenanceRecord | None,
    pdfs_out: Path,
) -> CorpusPdf | None:
    """Copy a store PDF into the corpus; ``None`` when the store holds none."""
    if record is None or record.source == PdfSource.missing:
        return None
    src = store_pdf_path(data_dir, record.stem)
    if not src.is_file():
        raise SystemExit(
            f"{src} is absent, but {record.work_id} has a {record.source.value} "
            f"record in the store's provenance"
        )
    if not file_has_pdf_magic(src):
        raise SystemExit(
            f"{src} is not a PDF (an unfetched Git LFS pointer?); "
            f"run `git lfs pull` in {data_dir}"
        )
    dest = pdfs_out / f"{record.stem}.pdf"
    shutil.copyfile(src, dest)
    return CorpusPdf(
        work_id=record.work_id,
        pdf_path=f"{PDFS_SUBDIR}/{record.stem}.pdf",
        content_sha256=sha256_of(dest),
        store_record=record,
    )


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "corpus-assemble",
        help="Select screened works and copy their PDFs into <data-dir>/corpus/.",
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
        help=(
            "Stage 3 verdicts JSONL, with screening-meta.json beside it "
            "(default: <data-dir>/screening-abstracts/<run-id>/verdicts.jsonl)."
        ),
    )
    parser.add_argument(
        "--screening-run-id",
        default=None,
        help=(
            "Screening run to read verdicts from (default: the run_id in "
            "<data-dir>/manifest.json). Unused when --screening-verdicts is given."
        ),
    )
    parser.add_argument(
        "--screening-threshold",
        type=float,
        default=None,
        help="Relevance score cutoff, 0-100; works scoring below it are left out (required).",
    )
    parser.add_argument(
        "--allow-unscreened",
        action="store_true",
        help="Include catalogue works that have no verdict row instead of exiting.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Project data directory holding the pdfs/ store and corpus/ (default: data).",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    data_dir: Path = args.data_dir
    if args.screening_threshold is None:
        raise SystemExit("--screening-threshold is required")
    threshold: float = args.screening_threshold
    allow_unscreened: bool = args.allow_unscreened

    screening_run_id: str | None = (
        None
        if args.screening_verdicts is not None
        else resolve_run_id(data_dir, args.screening_run_id)
    )
    catalogue_path, verdicts_path = resolve_screening_inputs(
        data_dir, args.catalogue, args.screening_verdicts, screening_run_id
    )
    screening_meta = _load_screening_meta(verdicts_path)
    verdicts = _load_verdicts(verdicts_path)

    catalogue_stats = JsonlReadStats()
    catalogue = list(read_jsonl(catalogue_path, Work, catalogue_stats))

    selected: list[Work] = []
    unscored = below = 0
    no_verdict: list[Work] = []
    for work in catalogue:
        verdict = verdicts.get(work.id)
        if verdict is None:
            no_verdict.append(work)
            if allow_unscreened:
                selected.append(work)
        elif verdict.relevance_score is None:
            unscored += 1
            selected.append(work)
        elif verdict.relevance_score >= threshold:
            selected.append(work)
        else:
            below += 1
    if no_verdict and not allow_unscreened:
        raise SystemExit(
            f"{len(no_verdict)} catalogue work(s) have no verdict row in "
            f"{verdicts_path} (first: {no_verdict[0].id}); finish screening or "
            f"pass --allow-unscreened to include them. Nothing written."
        )

    out_dir = corpus_dir(data_dir)
    tmp_dir = out_dir.with_name(out_dir.name + ".tmp")
    previous = _previous_meta(out_dir)
    provenance = load_provenance(data_dir)

    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    pdfs_out = tmp_dir / PDFS_SUBDIR
    pdfs_out.mkdir(parents=True)
    try:
        corpus_pdfs: list[CorpusPdf] = []
        for work in selected:
            record = provenance.get(work.id)
            copied = _copy_pdf(data_dir, record, pdfs_out)
            corpus_pdfs.append(
                copied
                if copied is not None
                else CorpusPdf(
                    work_id=work.id,
                    pdf_path=None,
                    content_sha256=None,
                    store_record=record,
                )
            )

        corpus_id = compute_corpus_id(corpus_pdfs)
        analysis_run_id = (
            previous.analysis_run_id
            if previous is not None and previous.corpus_id == corpus_id
            else generate_run_id()
        )

        write_jsonl(selected, tmp_dir / WORKS_FILENAME)
        write_jsonl(
            (verdicts[w.id] for w in selected if w.id in verdicts),
            tmp_dir / VERDICTS_FILENAME,
        )
        write_jsonl(corpus_pdfs, tmp_dir / PROVENANCE_FILENAME)
        with_pdf = sum(1 for p in corpus_pdfs if p.pdf_path is not None)
        write_meta(
            tmp_dir / META_FILENAME,
            CorpusMeta(
                run=RunMeta(
                    tool=TOOL_NAME,
                    run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
                    validation_skipped=catalogue_stats.skipped,
                ),
                corpus_id=corpus_id,
                analysis_run_id=analysis_run_id,
                catalogue=str(catalogue_path),
                screening_verdicts=str(verdicts_path),
                screening_run_id=screening_run_id,
                screening_llm=screening_meta.llm,
                screening_threshold=threshold,
                catalogue_count=len(catalogue),
                selected_count=len(selected),
                unscored_count=unscored,
                below_threshold_count=below,
                no_verdict_count=len(no_verdict),
                with_pdf_count=with_pdf,
            ),
        )
    except BaseException:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise

    if out_dir.exists():
        shutil.rmtree(out_dir)
    tmp_dir.rename(out_dir)

    kept = "kept" if previous is not None and previous.analysis_run_id == analysis_run_id else "new"
    print(
        f"Assembled {len(selected)} of {len(catalogue)} works into {out_dir} "
        f"({with_pdf} with a PDF, {unscored} unscored, {len(no_verdict)} "
        f"without a verdict, {below} below {threshold:g}); corpus_id "
        f"{corpus_id[:12]}, analysis run-id {analysis_run_id} ({kept}).",
        file=sys.stderr,
    )
