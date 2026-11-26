"""Readers for ``data/corpus/``, shared by the analysis stages 6–8.

The corpus is written whole by ``corpus-assemble``, so a missing file or a
row that fails validation means the corpus is broken or stale; every reader
exits naming the file instead of skipping rows.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.corpus.models import (
    META_FILENAME,
    PROVENANCE_FILENAME,
    WORKS_FILENAME,
    CorpusMeta,
    CorpusPdf,
)
from laglitsynth.fulltext_extraction.models import (
    META_FILENAME as EXTRACTION_META_FILENAME,
)
from laglitsynth.fulltext_extraction.models import (
    ExtractedDocument,
    ExtractionMeta,
)
from laglitsynth.io import JsonlReadStats, read_jsonl
from laglitsynth.layout import require_input

_T = TypeVar("_T", bound=BaseModel)

_UPSTREAM = "corpus-assemble"


def corpus_meta_path(corpus_dir: Path) -> Path:
    return corpus_dir / META_FILENAME


def load_corpus_meta(corpus_dir: Path) -> CorpusMeta:
    """Return the corpus's ``CorpusMeta``; exit when it is missing or invalid."""
    path = require_input(
        corpus_meta_path(corpus_dir), flag="--corpus-dir", upstream=_UPSTREAM
    )
    try:
        return CorpusMeta.model_validate_json(path.read_text())
    except ValidationError as exc:
        raise SystemExit(
            f"{path} does not validate as CorpusMeta; re-run {_UPSTREAM}:\n{exc}"
        ) from exc


def _read_strict(path: Path, model: type[_T]) -> list[_T]:
    require_input(path, flag="--corpus-dir", upstream=_UPSTREAM)
    stats = JsonlReadStats()
    rows = list(read_jsonl(path, model, stats))
    if stats.skipped:
        raise SystemExit(
            f"{path}: {stats.skipped} row(s) fail validation as {model.__name__}; "
            f"re-run {_UPSTREAM}"
        )
    return rows


def read_corpus_works(corpus_dir: Path) -> list[Work]:
    """Return the corpus's ``Work`` records in assembly order."""
    return _read_strict(corpus_dir / WORKS_FILENAME, Work)


def read_corpus_pdfs(corpus_dir: Path) -> list[CorpusPdf]:
    """Return one ``CorpusPdf`` per corpus work, works without a PDF included."""
    return _read_strict(corpus_dir / PROVENANCE_FILENAME, CorpusPdf)


def require_corpus(recorded: str, current: str, source: Path, *, rerun: str) -> None:
    """Exit when an output recorded on another corpus than the current one.

    ``recorded`` is the ``corpus_id`` stored in ``source`` (an upstream
    meta file); ``current`` is the id in ``corpus-meta.json``. ``rerun``
    names the stage that rewrites ``source`` on the current corpus.
    """
    if recorded != current:
        raise SystemExit(
            f"{source} was made on corpus {recorded}, but the corpus is now "
            f"{current}; re-run {rerun}"
        )


def load_upstream_meta(path: Path, model: type[_T], *, flag: str, upstream: str) -> _T:
    """Return an upstream stage's meta file; exit when missing or invalid."""
    require_input(path, flag=flag, upstream=upstream)
    try:
        return model.model_validate_json(path.read_text())
    except ValidationError as exc:
        raise SystemExit(
            f"{path} does not validate as {model.__name__}; re-run {upstream}:\n{exc}"
        ) from exc


def read_current_extractions(
    extractions: Path,
    corpus_meta: CorpusMeta,
    pdfs: list[CorpusPdf],
    stats: JsonlReadStats,
) -> dict[str, ExtractedDocument]:
    """Return the latest extraction per corpus work made from its corpus PDF.

    Exits when ``extraction-meta.json`` beside ``extractions`` is missing,
    invalid, or names another corpus. A record whose ``pdf_sha256`` differs
    from the corpus copy (the PDF changed since it was extracted) is
    ignored, as is a record for a work the corpus has no PDF for.
    """
    meta_path = extractions.parent / EXTRACTION_META_FILENAME
    meta = load_upstream_meta(
        meta_path, ExtractionMeta, flag="--extractions", upstream="fulltext-extraction"
    )
    require_corpus(
        meta.corpus_id, corpus_meta.corpus_id, meta_path, rerun="fulltext-extraction"
    )
    latest = {rec.work_id: rec for rec in read_jsonl(extractions, ExtractedDocument, stats)}
    pdf_sha = {p.work_id: p.content_sha256 for p in pdfs if p.content_sha256 is not None}
    current = {
        work_id: rec
        for work_id, rec in latest.items()
        if pdf_sha.get(work_id) == rec.pdf_sha256
    }
    stale = sum(1 for work_id in latest if work_id in pdf_sha) - len(current)
    if stale:
        print(
            f"Ignoring {stale} extraction(s) in {extractions} made from another "
            f"PDF than the corpus copy; re-run fulltext-extraction --skip-existing.",
            file=sys.stderr,
        )
    return current
