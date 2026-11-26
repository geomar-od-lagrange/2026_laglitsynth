"""Integration tests for the fulltext_extraction stage (``run`` entry point)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from laglitsynth.fulltext_extraction.extract import run
from laglitsynth.fulltext_extraction.models import ExtractedDocument

from conftest import _corpus_pdf_bytes, _make_work, _write_corpus


def _fake_tei_bytes() -> bytes:
    # Minimal valid TEI that GROBID would emit for a processed PDF.
    return (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<TEI xmlns="http://www.tei-c.org/ns/1.0">'
        b"<text><body>"
        b'<div><head>Intro</head><p>Content.</p></div>'
        b"</body></text></TEI>"
    )


def _patch_clients(fake_tei: bytes) -> tuple[MagicMock, MagicMock]:
    preflight_ok = httpx.Response(
        200,
        text="alive",
        request=httpx.Request("GET", "http://localhost:8070/api/isalive"),
    )
    version_ok = httpx.Response(
        200,
        text="0.8.0",
        request=httpx.Request("GET", "http://localhost:8070/api/version"),
    )

    preflight_client_mock = MagicMock(spec=httpx.Client)
    preflight_client_mock.get.side_effect = [preflight_ok, version_ok]

    paper_client_mock = MagicMock(spec=httpx.Client)
    paper_resp = httpx.Response(
        200,
        content=fake_tei,
        request=httpx.Request(
            "POST", "http://localhost:8070/api/processFulltextDocument"
        ),
    )
    paper_client_mock.post.return_value = paper_resp
    return preflight_client_mock, paper_client_mock


def _args(
    tmp_path: Path,
    *,
    corpus_dir: Path | None,
    skip_existing: bool = False,
    consolidate_citations: str = "0",
) -> MagicMock:
    args = MagicMock()
    args.corpus_dir = corpus_dir
    args.output_dir = None if corpus_dir is None else tmp_path / "out"
    args.data_dir = tmp_path / "data"
    args.grobid_url = "http://localhost:8070"
    args.timeout = 120.0
    args.skip_existing = skip_existing
    args.consolidate_citations = consolidate_citations
    return args


def _run(args: MagicMock, fake_tei: bytes | None = None) -> MagicMock:
    """Run stage 6 against mocked GROBID; return the per-paper client mock."""
    preflight_client_mock, paper_client_mock = _patch_clients(
        fake_tei if fake_tei is not None else _fake_tei_bytes()
    )

    def _make_client(timeout: float) -> MagicMock:
        if timeout == 5.0:
            return preflight_client_mock
        return paper_client_mock

    with patch(
        "laglitsynth.fulltext_extraction.extract.httpx.Client",
        side_effect=_make_client,
    ):
        run(args)
    return paper_client_mock


def _records(output_dir: Path) -> list[ExtractedDocument]:
    return [
        ExtractedDocument.model_validate_json(line)
        for line in (output_dir / "extraction.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _sent_names(paper_client_mock: MagicMock) -> list[str]:
    return [c.kwargs["files"]["input"][0] for c in paper_client_mock.post.call_args_list]


_W = "https://openalex.org/W1234"


class TestRunWritesTrimmedRecord:
    def test_record_shape(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus, [_make_work(_W)])
        fake_tei = _fake_tei_bytes()

        _run(_args(tmp_path, corpus_dir=corpus), fake_tei)

        output_dir = tmp_path / "out"
        lines = [l for l in (output_dir / "extraction.jsonl").read_text().splitlines() if l.strip()]
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["work_id"] == _W
        assert record["tei_path"] == "tei/W1234.tei.xml"
        assert record["content_sha256"] == hashlib.sha256(fake_tei).hexdigest()
        assert record["pdf_sha256"] == hashlib.sha256(_corpus_pdf_bytes(_W)).hexdigest()
        assert "extracted_at" in record
        # Removed fields must be gone — extra="forbid" on the model.
        assert "sections" not in record
        assert "raw_text" not in record

        # TEI artefact exists at the stored relative path.
        assert (output_dir / record["tei_path"]).read_bytes() == fake_tei


class TestConsolidateCitationsFlag:
    def test_flag_value_reaches_grobid_form_field(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus, [_make_work(_W)])

        paper_client_mock = _run(
            _args(tmp_path, corpus_dir=corpus, consolidate_citations="1")
        )

        # The GROBID processing POST carried the flag's value in its form data.
        assert paper_client_mock.post.call_count == 1
        _, kwargs = paper_client_mock.post.call_args
        assert kwargs["data"] == {"consolidateCitations": "1"}


class TestCorpusInput:
    def test_only_corpus_pdfs_are_sent(self, tmp_path: Path) -> None:
        """Works without a corpus PDF and PDFs outside the corpus are not sent."""
        corpus = tmp_path / "corpus"
        with_pdf = "https://openalex.org/W1"
        without_pdf = "https://openalex.org/W2"
        _write_corpus(
            corpus,
            [_make_work(with_pdf), _make_work(without_pdf)],
            pdfs={with_pdf: _corpus_pdf_bytes(with_pdf)},
        )
        # Store PDFs outside the corpus are never read.
        store = tmp_path / "data" / "pdfs"
        store.mkdir(parents=True)
        (store / "W3.pdf").write_bytes(b"%PDF-1.4 store only")
        (corpus / "pdfs" / "W4.pdf").write_bytes(b"%PDF-1.4 stray, no provenance row")

        paper_client_mock = _run(_args(tmp_path, corpus_dir=corpus))

        assert _sent_names(paper_client_mock) == ["W1.pdf"]
        assert [r.work_id for r in _records(tmp_path / "out")] == [with_pdf]
        meta = json.loads((tmp_path / "out" / "extraction-meta.json").read_text())
        assert meta["total_pdfs"] == 1
        assert "invalid_stem_count" not in meta

    def test_meta_carries_corpus_id(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        corpus_meta = _write_corpus(corpus, [_make_work(_W)])

        _run(_args(tmp_path, corpus_dir=corpus))

        meta = json.loads((tmp_path / "out" / "extraction-meta.json").read_text())
        assert meta["corpus_id"] == corpus_meta.corpus_id
        assert meta["extracted_count"] == 1

    def test_skip_existing_reextracts_changed_corpus_pdf(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        changed = "https://openalex.org/W1"
        same = "https://openalex.org/W2"
        works = [_make_work(changed), _make_work(same)]
        _write_corpus(corpus, works)
        _run(_args(tmp_path, corpus_dir=corpus))

        # Re-assembly: W1's PDF bytes change, W2's stay.
        new_bytes = b"%PDF-1.4 a better copy"
        corpus_meta = _write_corpus(
            corpus,
            works,
            pdfs={changed: new_bytes, same: _corpus_pdf_bytes(same)},
        )
        paper_client_mock = _run(_args(tmp_path, corpus_dir=corpus, skip_existing=True))

        assert _sent_names(paper_client_mock) == ["W1.pdf"]
        latest = {r.work_id: r for r in _records(tmp_path / "out")}
        assert latest[changed].pdf_sha256 == hashlib.sha256(new_bytes).hexdigest()
        assert latest[same].pdf_sha256 == hashlib.sha256(_corpus_pdf_bytes(same)).hexdigest()
        meta = json.loads((tmp_path / "out" / "extraction-meta.json").read_text())
        assert meta["corpus_id"] == corpus_meta.corpus_id

    def test_skip_existing_skips_unchanged_pdfs(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus, [_make_work(_W)])
        _run(_args(tmp_path, corpus_dir=corpus))

        paper_client_mock = _run(_args(tmp_path, corpus_dir=corpus, skip_existing=True))

        paper_client_mock.post.assert_not_called()
        assert len(_records(tmp_path / "out")) == 1


class TestConventionalPaths:
    def test_data_dir_alone_reads_corpus_and_writes_extraction_dir(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "data"
        _write_corpus(data_dir / "corpus", [_make_work(_W)])
        args = _args(tmp_path, corpus_dir=None)

        _run(args)

        output_dir = data_dir / "fulltext-extraction"
        assert [r.work_id for r in _records(output_dir)] == [_W]
        assert (output_dir / "tei" / "W1234.tei.xml").exists()
        assert (output_dir / "extraction-meta.json").exists()

    def test_missing_corpus_names_upstream_and_flag(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit) as exc_info:
            run(_args(tmp_path, corpus_dir=None))
        msg = str(exc_info.value)
        assert "run corpus-assemble first" in msg
        assert "--corpus-dir" in msg
