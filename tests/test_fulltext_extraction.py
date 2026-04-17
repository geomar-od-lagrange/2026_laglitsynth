"""Integration tests for the fulltext_extraction stage (``run`` entry point)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

from laglitsynth.fulltext_extraction.extract import run


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


class TestRunWritesTrimmedRecord:
    def test_record_shape(self, tmp_path: Path) -> None:
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        output_dir = tmp_path / "out"
        (pdf_dir / "W1234.pdf").write_bytes(b"%PDF-1.4 fake")

        args = MagicMock()
        args.pdf_dir = pdf_dir
        args.output_dir = output_dir
        args.grobid_url = "http://localhost:8070"
        args.timeout = 120.0
        args.skip_existing = False

        fake_tei = _fake_tei_bytes()
        preflight_client_mock, paper_client_mock = _patch_clients(fake_tei)

        def _make_client(timeout: float) -> MagicMock:
            if timeout == 5.0:
                return preflight_client_mock
            return paper_client_mock

        with patch(
            "laglitsynth.fulltext_extraction.extract.httpx.Client",
            side_effect=_make_client,
        ):
            run(args)

        extraction_path = output_dir / "extraction.jsonl"
        lines = [l for l in extraction_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["work_id"] == "https://openalex.org/W1234"
        assert record["tei_path"] == "tei/W1234.tei.xml"
        assert record["content_sha256"] == hashlib.sha256(fake_tei).hexdigest()
        assert "extracted_at" in record
        # Removed fields must be gone — extra="forbid" on the model.
        assert "sections" not in record
        assert "raw_text" not in record

        # TEI artefact exists at the stored relative path.
        assert (output_dir / record["tei_path"]).read_bytes() == fake_tei


class TestInvalidStemSkipped:
    def test_invalid_stem_skipped(self, tmp_path: Path) -> None:
        pdf_dir = tmp_path / "pdfs"
        pdf_dir.mkdir()
        output_dir = tmp_path / "out"

        # Place a stray file with an invalid stem and a valid one.
        (pdf_dir / "sample.pdf").write_bytes(b"%PDF-1.4 fake")
        (pdf_dir / "W1234.pdf").write_bytes(b"%PDF-1.4 fake")

        args = MagicMock()
        args.pdf_dir = pdf_dir
        args.output_dir = output_dir
        args.grobid_url = "http://localhost:8070"
        args.timeout = 120.0
        args.skip_existing = False

        fake_tei = _fake_tei_bytes()
        preflight_client_mock, paper_client_mock = _patch_clients(fake_tei)

        def _make_client(timeout: float) -> MagicMock:
            if timeout == 5.0:
                return preflight_client_mock
            return paper_client_mock

        with patch(
            "laglitsynth.fulltext_extraction.extract.httpx.Client",
            side_effect=_make_client,
        ):
            run(args)

        extraction_path = output_dir / "extraction.jsonl"
        lines = [l for l in extraction_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["work_id"] == "https://openalex.org/W1234"

        meta_path = output_dir / "extraction-meta.json"
        meta = json.loads(meta_path.read_text())
        assert meta["invalid_stem_count"] == 1
        assert meta["extracted_count"] == 1
