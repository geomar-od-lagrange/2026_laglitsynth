"""Shared test helpers for the laglitsynth test suite.

Plain top-level functions — not pytest fixtures — so they can be imported
directly with ``from conftest import _make_work``.  Pytest discovers
``conftest.py`` automatically and puts it on the import path for all sibling
test files.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from laglitsynth.catalogue_fetch.models import Author, Authorship, Work
from laglitsynth.fulltext_extraction.models import ExtractedDocument

if TYPE_CHECKING:
    from laglitsynth.corpus.models import CorpusMeta

TEI_NS = "http://www.tei-c.org/ns/1.0"


def _make_authorship(display_name: str) -> Authorship:
    """Return a minimal ``Authorship`` for the given author display name."""
    return Authorship(
        author_position="first",
        author=Author(display_name=display_name),
        institutions=[],
        countries=[],
        raw_affiliation_strings=[],
    )


def _make_work(
    work_id: str = "https://openalex.org/W1",
    title: str = "Test Paper",
    abstract: str | None = "An abstract.",
    doi: str | None = None,
    publication_year: int | None = None,
    pdf_url: str | None = None,
    oa_url: str | None = None,
    authorships: list[Authorship] | None = None,
) -> Work:
    """Return a minimal valid ``Work`` for testing.

    All fields beyond ``work_id`` are optional so callers can vary only the
    dimensions relevant to their test.
    """
    primary_location = None
    if pdf_url is not None:
        primary_location = {"pdf_url": pdf_url}
    open_access = None
    if oa_url is not None:
        open_access = {"oa_url": oa_url}
    return Work(
        id=work_id,
        title=title,
        abstract=abstract,
        doi=doi,
        publication_year=publication_year,
        authorships=authorships if authorships is not None else [],
        biblio={},
        cited_by_count=0,
        referenced_works=[],
        keywords=[],
        topics=[],
        primary_location=primary_location,
        open_access=open_access,
    )


def _write_works_jsonl(path: Path, works: list[Work]) -> None:
    with open(path, "w") as f:
        for w in works:
            f.write(w.model_dump_json() + "\n")


def _write_extractions_jsonl(path: Path, records: list[ExtractedDocument]) -> None:
    with open(path, "w") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")


def _mock_openai_response(content: str | None, finish_reason: str = "stop") -> MagicMock:
    """Build a mock OpenAI chat completion response returning ``content``."""
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    choice.finish_reason = finish_reason
    response = MagicMock()
    response.choices = [choice]
    return response


def _write_tei(path: Path, body_content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<TEI xmlns="{TEI_NS}"><text><body>{body_content}</body></text></TEI>'
        ).encode()
    )


TEST_RUN_ID = "2026-01-01T00-00-00_abcdef123456"


def _write_manifest(data_dir: Path, run_id: str = TEST_RUN_ID) -> None:
    """Write an initialised ``manifest.json`` under ``data_dir``."""
    from datetime import UTC, datetime

    from laglitsynth.io import write_meta
    from laglitsynth.manifest import RunManifest
    from laglitsynth.models import RunMeta

    data_dir.mkdir(parents=True, exist_ok=True)
    write_meta(
        data_dir / "manifest.json",
        RunManifest(
            run=RunMeta(
                tool="laglitsynth.manifest",
                run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
                validation_skipped=0,
            ),
            run_id=run_id,
            queries=["lagrangian oceanography"],
        ),
    )


TEST_ANALYSIS_RUN_ID = "2026-02-02T00-00-00_analysis00001"


def _corpus_pdf_bytes(work_id: str) -> bytes:
    """Distinct, magic-prefixed PDF bytes for one corpus work."""
    return b"%PDF-1.4 " + work_id.encode()


def _write_corpus(
    corpus_dir: Path,
    works: list[Work],
    *,
    pdfs: dict[str, bytes] | None = None,
    analysis_run_id: str = TEST_ANALYSIS_RUN_ID,
) -> CorpusMeta:
    """Write a ``corpus-assemble``-shaped corpus under ``corpus_dir``.

    ``pdfs`` maps work id to PDF bytes; omitted, every work gets
    ``_corpus_pdf_bytes``. Works missing from a given ``pdfs`` have no PDF.
    ``corpus_id`` is computed as ``corpus-assemble`` computes it.
    """
    import hashlib
    from datetime import UTC, datetime

    from laglitsynth.corpus.assemble import compute_corpus_id
    from laglitsynth.corpus.models import CorpusMeta, CorpusPdf
    from laglitsynth.io import write_jsonl, write_meta
    from laglitsynth.models import LlmMeta, RunMeta

    if pdfs is None:
        pdfs = {w.id: _corpus_pdf_bytes(w.id) for w in works}
    (corpus_dir / "pdfs").mkdir(parents=True, exist_ok=True)
    rows: list[CorpusPdf] = []
    for work in works:
        data = pdfs.get(work.id)
        if data is None:
            rows.append(
                CorpusPdf(work_id=work.id, pdf_path=None, content_sha256=None, store_record=None)
            )
            continue
        pdf_path = f"pdfs/{work.id.rsplit('/', 1)[-1]}.pdf"
        (corpus_dir / pdf_path).write_bytes(data)
        rows.append(
            CorpusPdf(
                work_id=work.id,
                pdf_path=pdf_path,
                content_sha256=hashlib.sha256(data).hexdigest(),
                store_record=None,
            )
        )
    write_jsonl(works, corpus_dir / "works.jsonl")
    write_jsonl([], corpus_dir / "verdicts.jsonl")
    write_jsonl(rows, corpus_dir / "provenance.jsonl")
    meta = CorpusMeta(
        run=RunMeta(
            tool="laglitsynth.corpus.assemble",
            run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
            validation_skipped=0,
        ),
        corpus_id=compute_corpus_id(rows),
        analysis_run_id=analysis_run_id,
        catalogue="catalogue.jsonl",
        screening_verdicts="verdicts.jsonl",
        screening_run_id=None,
        screening_llm=LlmMeta(
            model="m",
            base_url="https://llm.example/v1",
            decisions_url="https://llm.example/v1/decisions",
            reasoning_effort=None,
            prompt_sha256="0" * 64,
        ),
        screening_threshold=50.0,
        catalogue_count=len(works),
        selected_count=len(works),
        unscored_count=0,
        below_threshold_count=0,
        no_verdict_count=0,
        with_pdf_count=sum(1 for r in rows if r.pdf_path is not None),
    )
    write_meta(corpus_dir / "corpus-meta.json", meta)
    return meta


def _write_full_text_for(
    extractions_path: Path,
    work_ids: list[str],
    corpus_dir: Path,
    *,
    pdf_sha256: dict[str, str] | None = None,
    corpus_id: str | None = None,
) -> None:
    """Write stage 6 output for ``work_ids`` made from the corpus PDFs.

    Each work gets a renderable TEI and an ``ExtractedDocument`` whose
    ``pdf_sha256`` matches its corpus copy (override per work with
    ``pdf_sha256``), plus an ``extraction-meta.json`` naming the corpus
    (override with ``corpus_id``).
    """
    from datetime import UTC, datetime

    from laglitsynth.corpus.load import load_corpus_meta, read_corpus_pdfs
    from laglitsynth.fulltext_extraction.models import ExtractionMeta
    from laglitsynth.io import write_meta
    from laglitsynth.models import RunMeta

    corpus_sha = {
        p.work_id: p.content_sha256 for p in read_corpus_pdfs(corpus_dir) if p.content_sha256
    }
    overrides = pdf_sha256 or {}
    output_dir = extractions_path.parent
    records: list[ExtractedDocument] = []
    for wid in work_ids:
        tei_path = f"tei/{wid.rsplit('/', 1)[-1]}.tei.xml"
        _write_tei(
            output_dir / tei_path,
            f'<div xmlns="{TEI_NS}"><head>Methods</head><p>Body text.</p></div>',
        )
        records.append(
            ExtractedDocument(
                work_id=wid,
                tei_path=tei_path,
                content_sha256="0" * 64,
                pdf_sha256=overrides.get(wid, corpus_sha.get(wid, "f" * 64)),
                extracted_at="2026-04-17T00:00:00.000000+00:00",
            )
        )
    _write_extractions_jsonl(extractions_path, records)
    write_meta(
        output_dir / "extraction-meta.json",
        ExtractionMeta(
            run=RunMeta(
                tool="laglitsynth.fulltext_extraction.extract",
                run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
                validation_skipped=0,
            ),
            corpus_id=corpus_id if corpus_id is not None else load_corpus_meta(corpus_dir).corpus_id,
            grobid_version="0.8.0",
            total_pdfs=len(corpus_sha),
            extracted_count=len(records),
            failed_count=0,
        ),
    )
