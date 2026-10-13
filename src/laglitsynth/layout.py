"""The fixed layout of a review's ``data/`` directory.

Every stage writes to, and reads from, a conventional path under
``--data-dir``. A stage whose input flag is omitted looks here for the
upstream output; see [docs/run-manifest.md](../../docs/run-manifest.md).
Only the run-id leaf of stages 3, 7, and 8 is not derivable from the
layout, and the manifest (``laglitsynth.manifest``) carries that.
"""

from __future__ import annotations

import sys
from pathlib import Path

CATALOGUE_FETCH_SUBDIR = "catalogue-fetch"
CATALOGUE_DEDUP_SUBDIR = "catalogue-dedup"
SCREENING_SUBDIR = "screening-abstracts"
EXTRACTION_SUBDIR = "fulltext-extraction"
ELIGIBILITY_SUBDIR = "fulltext-eligibility"
EXTRACTION_CODEBOOK_SUBDIR = "extraction-codebook"
EXPORT_SUBDIR = "pdfs/export"

PDF_MANIFEST_FILENAME = "pdf-manifest.csv"


def catalogue_fetch_glob(data_dir: Path) -> str:
    return str(data_dir / CATALOGUE_FETCH_SUBDIR / "*.jsonl")


def catalogue_fetch_path(data_dir: Path) -> Path:
    return data_dir / CATALOGUE_FETCH_SUBDIR / "catalogue.jsonl"


def dedup_dir(data_dir: Path) -> Path:
    return data_dir / CATALOGUE_DEDUP_SUBDIR


def deduplicated_path(data_dir: Path) -> Path:
    return dedup_dir(data_dir) / "deduplicated.jsonl"


def screening_verdicts_path(data_dir: Path, run_id: str) -> Path:
    return data_dir / SCREENING_SUBDIR / run_id / "verdicts.jsonl"


def extraction_dir(data_dir: Path) -> Path:
    return data_dir / EXTRACTION_SUBDIR


def extractions_path(data_dir: Path) -> Path:
    return extraction_dir(data_dir) / "extraction.jsonl"


def eligibility_verdicts_path(data_dir: Path, run_id: str) -> Path:
    return data_dir / ELIGIBILITY_SUBDIR / run_id / "verdicts.jsonl"


def export_dir(data_dir: Path) -> Path:
    return data_dir / EXPORT_SUBDIR


def pdf_manifest_path(data_dir: Path) -> Path:
    return export_dir(data_dir) / PDF_MANIFEST_FILENAME


def require_input(path: Path, *, flag: str, upstream: str) -> Path:
    """Return ``path`` if it exists; else exit naming the stage that writes it.

    Used on every resolved input, whether it came from the flag or from the
    layout, so a missing upstream output is reported before any work starts
    rather than surfacing as a stack trace or an unrelated preflight error.
    """
    if not path.exists():
        raise SystemExit(
            f"{path} not found; run {upstream} first or pass {flag}"
        )
    return path


def resolve_screening_inputs(
    data_dir: Path,
    catalogue: Path | None,
    screening_verdicts: Path | None,
    run_id: str | None,
) -> tuple[Path, Path]:
    """Resolve the catalogue + screening-verdicts pair stages 5 and 7 join on.

    Each omitted flag takes its conventional path; the verdicts path needs
    the run-id, which comes from ``run_id`` or the manifest.
    """
    from laglitsynth.manifest import resolve_run_id

    catalogue = require_input(
        catalogue if catalogue is not None else deduplicated_path(data_dir),
        flag="--catalogue",
        upstream="catalogue-dedup",
    )
    if screening_verdicts is None:
        screening_verdicts = screening_verdicts_path(
            data_dir, resolve_run_id(data_dir, run_id)
        )
    screening_verdicts = require_input(
        screening_verdicts, flag="--screening-verdicts", upstream="screening-abstracts"
    )
    return catalogue, screening_verdicts


def warn_existing_run_dir(output_dir: Path, filename: str) -> None:
    """Say on stderr that a run dir is being reused and its output rewritten."""
    if (output_dir / filename).exists():
        print(
            f"Reusing run dir {output_dir}; {filename} is rewritten "
            f"(pass --run-id to write a fresh one).",
            file=sys.stderr,
        )
