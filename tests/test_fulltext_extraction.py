"""Integration tests for the fulltext_extraction stage (``run`` entry point)."""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from laglitsynth.fulltext_extraction.extract import _grobid_version, build_subparser, run
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
    # GROBID 0.9.0's /api/version shape.
    version_ok = httpx.Response(
        200,
        text='{"version":"0.9.0","revision":"0.9.0"}',
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
    concurrency: int = 1,
) -> MagicMock:
    args = MagicMock()
    args.corpus_dir = corpus_dir
    args.output_dir = None if corpus_dir is None else tmp_path / "out"
    args.data_dir = tmp_path / "data"
    args.grobid_url = "http://localhost:8070"
    args.timeout = 120.0
    args.skip_existing = skip_existing
    args.consolidate_citations = consolidate_citations
    args.concurrency = concurrency
    return args


def _run(
    args: MagicMock,
    fake_tei: bytes | None = None,
    *,
    post: Callable[..., httpx.Response] | None = None,
    client_kwargs: list[dict[str, Any]] | None = None,
) -> MagicMock:
    """Run stage 6 against mocked GROBID; return the per-paper client mock.

    ``post`` replaces the per-paper client's ``post``; ``client_kwargs``
    collects the keyword arguments each ``httpx.Client`` was built with.
    """
    preflight_client_mock, paper_client_mock = _patch_clients(
        fake_tei if fake_tei is not None else _fake_tei_bytes()
    )
    if post is not None:
        paper_client_mock.post.side_effect = post

    def _make_client(**kwargs: Any) -> MagicMock:
        if client_kwargs is not None:
            client_kwargs.append(kwargs)
        if kwargs["timeout"] == 5.0:
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
        assert meta["grobid_version"] == "0.9.0"

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


# --- --concurrency ---


def _tei_for(name: str) -> bytes:
    """Distinct TEI per PDF so each file on disk can be matched to its work."""
    return _fake_tei_bytes().replace(b"Content.", name.encode())


class _FakeGrobid:
    """Thread-safe stand-in for ``POST /api/processFulltextDocument``.

    Records every PDF sent and the peak number of requests in flight;
    answers HTTP 500 for the stems in ``fail``.
    """

    def __init__(self, *, fail: frozenset[str] = frozenset(), delay: float = 0.0):
        self.fail = fail
        self.delay = delay
        self.sent: list[str] = []
        self.peak = 0
        self._in_flight = 0
        self._lock = threading.Lock()

    def __call__(self, url: str, **kwargs: Any) -> httpx.Response:
        name: str = kwargs["files"]["input"][0]
        with self._lock:
            self.sent.append(name)
            self._in_flight += 1
            self.peak = max(self.peak, self._in_flight)
        try:
            time.sleep(self.delay)
        finally:
            with self._lock:
                self._in_flight -= 1
        request = httpx.Request("POST", url)
        if Path(name).stem in self.fail:
            return httpx.Response(500, text="boom", request=request)
        return httpx.Response(200, content=_tei_for(name), request=request)


_WORK_IDS = [f"https://openalex.org/W{n}" for n in range(1, 7)]


def _meta(output_dir: Path) -> dict[str, Any]:
    meta: dict[str, Any] = json.loads((output_dir / "extraction-meta.json").read_text())
    return meta


class TestConcurrency:
    def test_default_is_one(self) -> None:
        parser = argparse.ArgumentParser()
        build_subparser(parser.add_subparsers())
        args = parser.parse_args(["fulltext-extraction"])
        assert args.concurrency == 1

    def test_every_pdf_extracted_once(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus, [_make_work(w) for w in _WORK_IDS])
        grobid = _FakeGrobid(delay=0.05)

        _run(_args(tmp_path, corpus_dir=corpus, concurrency=3), post=grobid)

        assert sorted(grobid.sent) == sorted(f"W{n}.pdf" for n in range(1, 7))
        assert 2 <= grobid.peak <= 3
        output_dir = tmp_path / "out"
        records = _records(output_dir)
        assert sorted(r.work_id for r in records) == sorted(_WORK_IDS)
        for record in records:
            stem = record.work_id.rsplit("/", 1)[1]
            tei = (output_dir / record.tei_path).read_bytes()
            assert record.tei_path == f"tei/{stem}.tei.xml"
            assert tei == _tei_for(f"{stem}.pdf")
            assert record.content_sha256 == hashlib.sha256(tei).hexdigest()
            assert record.pdf_sha256 == hashlib.sha256(
                _corpus_pdf_bytes(record.work_id)
            ).hexdigest()
        meta = _meta(output_dir)
        assert (meta["total_pdfs"], meta["extracted_count"], meta["failed_count"]) == (
            6,
            6,
            0,
        )

    def test_failed_pdf_does_not_stop_the_others(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus, [_make_work(w) for w in _WORK_IDS])
        grobid = _FakeGrobid(fail=frozenset({"W3"}), delay=0.01)

        _run(_args(tmp_path, corpus_dir=corpus, concurrency=3), post=grobid)

        assert len(grobid.sent) == 6
        output_dir = tmp_path / "out"
        extracted = {r.work_id for r in _records(output_dir)}
        assert extracted == set(_WORK_IDS) - {"https://openalex.org/W3"}
        assert not (output_dir / "tei" / "W3.tei.xml").exists()
        meta = _meta(output_dir)
        assert (meta["extracted_count"], meta["failed_count"]) == (5, 1)

    def test_skip_existing_resumes_only_the_missing(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus, [_make_work(w) for w in _WORK_IDS])
        _run(
            _args(tmp_path, corpus_dir=corpus, concurrency=3),
            post=_FakeGrobid(fail=frozenset({"W3"})),
        )

        grobid = _FakeGrobid()
        _run(
            _args(tmp_path, corpus_dir=corpus, skip_existing=True, concurrency=3),
            post=grobid,
        )

        assert grobid.sent == ["W3.pdf"]
        output_dir = tmp_path / "out"
        work_ids = [r.work_id for r in _records(output_dir)]
        assert sorted(work_ids) == sorted(_WORK_IDS)  # each work exactly once
        assert (output_dir / "tei" / "W3.tei.xml").read_bytes() == _tei_for("W3.pdf")
        meta = _meta(output_dir)
        assert (meta["total_pdfs"], meta["extracted_count"], meta["failed_count"]) == (
            6,
            1,
            0,
        )

    def test_connection_pool_fits_concurrency(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        _write_corpus(corpus, [_make_work(_W)])
        client_kwargs: list[dict[str, Any]] = []

        _run(
            _args(tmp_path, corpus_dir=corpus, concurrency=4),
            client_kwargs=client_kwargs,
        )

        paper = [kw for kw in client_kwargs if kw["timeout"] == 120.0]
        assert len(paper) == 1
        limits: httpx.Limits = paper[0]["limits"]
        assert limits.max_connections is not None and limits.max_connections >= 4
        assert (
            limits.max_keepalive_connections is not None
            and limits.max_keepalive_connections >= 4
        )

    def test_one_runs_sequentially_in_corpus_order(self, tmp_path: Path) -> None:
        from concurrent.futures import ThreadPoolExecutor as TPE

        corpus = tmp_path / "corpus"
        _write_corpus(corpus, [_make_work(w) for w in _WORK_IDS])
        grobid = _FakeGrobid()

        with patch("laglitsynth.concurrency.ThreadPoolExecutor", wraps=TPE) as mock_tpe:
            _run(_args(tmp_path, corpus_dir=corpus, concurrency=1), post=grobid)

        mock_tpe.assert_not_called()
        assert grobid.sent == [f"W{n}.pdf" for n in range(1, 7)]
        assert [r.work_id for r in _records(tmp_path / "out")] == _WORK_IDS


class TestGrobidVersion:
    @staticmethod
    def _client(response: httpx.Response | Exception) -> MagicMock:
        client = MagicMock(spec=httpx.Client)
        if isinstance(response, Exception):
            client.get.side_effect = response
        else:
            client.get.return_value = response
        return client

    @staticmethod
    def _response(status: int, text: str) -> httpx.Response:
        return httpx.Response(
            status,
            text=text,
            request=httpx.Request("GET", "http://localhost:8070/api/version"),
        )

    def test_json_body_of_grobid_0_9(self) -> None:
        client = self._client(
            self._response(200, '{"version":"0.9.0","revision":"0.9.0"}')
        )
        assert _grobid_version("http://localhost:8070", client) == "0.9.0"

    def test_plain_text_body_of_grobid_0_8(self) -> None:
        client = self._client(self._response(200, "0.8.0\n"))
        assert _grobid_version("http://localhost:8070", client) == "0.8.0"

    def test_error_status_is_unknown(self) -> None:
        client = self._client(self._response(500, "boom"))
        assert _grobid_version("http://localhost:8070", client) == "unknown"

    def test_connection_error_is_unknown(self) -> None:
        client = self._client(httpx.ConnectError("refused"))
        assert _grobid_version("http://localhost:8070", client) == "unknown"
