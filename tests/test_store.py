"""Tests for the shared, work-keyed PDF store."""

from __future__ import annotations

from pathlib import Path

from laglitsynth.fulltext_retrieval.models import PdfProvenanceRecord, PdfSource
from laglitsynth.fulltext_retrieval.store import (
    load_provenance,
    sha256_of,
    store_pdf_path,
    upsert_provenance,
    write_provenance,
)


def _record(
    work_id: str = "https://openalex.org/W1",
    stem: str = "W1",
    *,
    source: PdfSource = PdfSource.oa,
    doi: str | None = "10.1/a",
    pdf_path: str | None = "pdfs/W1.pdf",
    content_sha256: str | None = "abc",
) -> PdfProvenanceRecord:
    return PdfProvenanceRecord(
        work_id=work_id,
        stem=stem,
        doi=doi,
        source=source,
        source_url="https://example.com/p.pdf" if source != PdfSource.missing else None,
        pdf_path=pdf_path,
        content_sha256=content_sha256,
        obtained_at="2026-01-01T00:00:00+00:00",
    )


class TestStorePdfPath:
    def test_path_layout(self, tmp_path: Path) -> None:
        assert store_pdf_path(tmp_path, "W42") == tmp_path / "pdfs" / "W42.pdf"


class TestSha256:
    def test_matches_known_value(self, tmp_path: Path) -> None:
        p = tmp_path / "f.bin"
        p.write_bytes(b"hello")
        # sha256("hello")
        assert sha256_of(p) == (
            "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
        )


class TestProvenanceRoundTrip:
    def test_write_then_load(self, tmp_path: Path) -> None:
        records = {r.work_id: r for r in [_record()]}
        write_provenance(tmp_path, records)
        loaded = load_provenance(tmp_path)
        assert loaded == records

    def test_load_missing_file_is_empty(self, tmp_path: Path) -> None:
        assert load_provenance(tmp_path) == {}


class TestUpsertLastWriteWins:
    def test_upsert_overwrites_per_work(self, tmp_path: Path) -> None:
        records: dict[str, PdfProvenanceRecord] = {}
        upsert_provenance(tmp_path, records, _record(source=PdfSource.oa))
        upsert_provenance(
            tmp_path,
            records,
            _record(source=PdfSource.zotero_import),
        )
        loaded = load_provenance(tmp_path)
        assert len(loaded) == 1
        assert loaded["https://openalex.org/W1"].source == PdfSource.zotero_import


class TestMissingRecordRoundTrips:
    def test_missing_record_has_none_path_and_sha(self, tmp_path: Path) -> None:
        rec = _record(
            source=PdfSource.missing,
            pdf_path=None,
            content_sha256=None,
        )
        records = {rec.work_id: rec}
        write_provenance(tmp_path, records)
        loaded = load_provenance(tmp_path)[rec.work_id]
        assert loaded.source == PdfSource.missing
        assert loaded.pdf_path is None
        assert loaded.content_sha256 is None
