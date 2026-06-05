"""Shared, work-keyed PDF store under ``data/pdfs/``.

The store is a single persistent directory holding one PDF per work
(``data/pdfs/<stem>.pdf``) plus a work-keyed provenance sidecar
(``data/pdfs/provenance.jsonl``). It is single-writer by design: one
operator drives the pipeline and is the only process that writes here, so
provenance is a plain whole-file last-write-wins record — read all rows
into a ``dict`` keyed by ``work_id`` on startup, rewrite the whole file
atomically. There is no concurrent writer to race, hence no journal or
locking.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from laglitsynth.fulltext_retrieval.models import PdfProvenanceRecord
from laglitsynth.io import read_jsonl

PDFS_SUBDIR = "pdfs"
PROVENANCE_FILENAME = "provenance.jsonl"


def pdfs_dir(data_dir: Path) -> Path:
    """Return the ``<data-dir>/pdfs`` directory path."""
    return data_dir / PDFS_SUBDIR


def provenance_path(data_dir: Path) -> Path:
    """Return the ``<data-dir>/pdfs/provenance.jsonl`` path."""
    return pdfs_dir(data_dir) / PROVENANCE_FILENAME


def store_pdf_path(data_dir: Path, stem: str) -> Path:
    """Return the on-disk PDF path for a work stem in the shared store."""
    return pdfs_dir(data_dir) / f"{stem}.pdf"


def sha256_of(path: Path) -> str:
    """Return the hex SHA-256 of a file's bytes, streamed in chunks."""
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_provenance(data_dir: Path) -> dict[str, PdfProvenanceRecord]:
    """Read ``provenance.jsonl`` into a ``dict`` keyed by ``work_id``.

    Returns an empty mapping when the file does not exist yet. The file is
    last-write-wins per ``work_id``: later rows for the same work overwrite
    earlier ones.
    """
    path = provenance_path(data_dir)
    records: dict[str, PdfProvenanceRecord] = {}
    if not path.exists():
        return records
    for rec in read_jsonl(path, PdfProvenanceRecord):
        records[rec.work_id] = rec
    return records


def write_provenance(
    data_dir: Path, records: dict[str, PdfProvenanceRecord]
) -> None:
    """Rewrite ``provenance.jsonl`` atomically from a ``work_id`` mapping."""
    path = provenance_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd = tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, delete=False, suffix=".tmp", encoding="utf-8"
    )
    try:
        for rec in records.values():
            tmp_fd.write(rec.model_dump_json() + "\n")
        tmp_fd.flush()
        os.fsync(tmp_fd.fileno())
        tmp_fd.close()
        Path(tmp_fd.name).rename(path)
    except BaseException:
        tmp_fd.close()
        Path(tmp_fd.name).unlink(missing_ok=True)
        raise


def upsert_provenance(
    data_dir: Path,
    records: dict[str, PdfProvenanceRecord],
    record: PdfProvenanceRecord,
) -> None:
    """Insert/replace ``record`` for its ``work_id`` and rewrite the file.

    Mutates ``records`` in place (last-write-wins) and persists the whole
    mapping atomically.
    """
    records[record.work_id] = record
    write_provenance(data_dir, records)
