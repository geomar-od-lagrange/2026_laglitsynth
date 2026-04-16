"""Tests for the fulltext retrieval submodule."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from laglitsynth.fulltext_retrieval.models import RetrievalRecord, RetrievalStatus
from laglitsynth.fulltext_retrieval.retrieve import (
    _load_existing,
    _rate_limit,
    _retrieve_one,
    _validate_pdf,
    run,
    work_id_to_filename,
)
from laglitsynth.catalogue_fetch.models import Work


def _make_work(
    work_id: str = "https://openalex.org/W1234567890",
    doi: str | None = "https://doi.org/10.1234/test",
    pdf_url: str | None = None,
    oa_url: str | None = None,
) -> Work:
    primary_location = None
    if pdf_url is not None:
        primary_location = {"pdf_url": pdf_url}
    open_access = None
    if oa_url is not None:
        open_access = {"oa_url": oa_url}
    return Work(
        id=work_id,
        doi=doi,
        title="Test Paper",
        abstract="An abstract.",
        authorships=[],
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


def _pdf_content() -> bytes:
    return b"%PDF-1.4 fake pdf content"


class TestWorkIdToFilename:
    def test_extracts_suffix(self) -> None:
        assert work_id_to_filename("https://openalex.org/W1234567890") == "W1234567890"

    def test_bare_id(self) -> None:
        assert work_id_to_filename("W999") == "W999"


class TestValidatePdf:
    def test_pdf_header(self) -> None:
        assert _validate_pdf(b"%PDF-1.4 content", None) is True

    def test_content_type(self) -> None:
        assert _validate_pdf(b"not pdf header", "application/pdf") is True

    def test_invalid(self) -> None:
        assert _validate_pdf(b"<html>", "text/html") is False


class TestOaRetrieval:
    def test_retrieve_from_oa_url(self, tmp_path: Path) -> None:
        work = _make_work(pdf_url="https://example.com/paper.pdf")
        mock_response = httpx.Response(
            200,
            content=_pdf_content(),
            headers={"content-type": "application/pdf"},
            request=httpx.Request("GET", "https://example.com/paper.pdf"),
        )
        client = MagicMock(spec=httpx.Client)
        client.get.return_value = mock_response

        with patch("laglitsynth.fulltext_retrieval.retrieve._rate_limit"):
            record = _retrieve_one(
                work,
                tmp_path,
                client=client,
                email="test@example.com",
                manual_dir=None,
                dry_run=False,
            )

        assert record.retrieval_status == RetrievalStatus.retrieved_oa
        assert record.source_url == "https://example.com/paper.pdf"
        assert record.pdf_path is not None
        assert (tmp_path / record.pdf_path).exists()


class TestUnpaywallFallback:
    def test_unpaywall_retrieval(self, tmp_path: Path) -> None:
        work = _make_work(doi="https://doi.org/10.1234/test")

        unpaywall_response = httpx.Response(
            200,
            json={
                "best_oa_location": {
                    "url_for_pdf": "https://unpaywall.example.com/paper.pdf"
                }
            },
            request=httpx.Request(
                "GET", "https://api.unpaywall.org/v2/10.1234/test?email=test@example.com"
            ),
        )
        pdf_response = httpx.Response(
            200,
            content=_pdf_content(),
            headers={"content-type": "application/pdf"},
            request=httpx.Request("GET", "https://unpaywall.example.com/paper.pdf"),
        )

        client = MagicMock(spec=httpx.Client)
        client.get.side_effect = [unpaywall_response, pdf_response]

        with patch("laglitsynth.fulltext_retrieval.retrieve._rate_limit"):
            record = _retrieve_one(
                work,
                tmp_path,
                client=client,
                email="test@example.com",
                manual_dir=None,
                dry_run=False,
            )

        assert record.retrieval_status == RetrievalStatus.retrieved_unpaywall
        assert record.source_url == "https://unpaywall.example.com/paper.pdf"


class TestManualPickup:
    def test_manual_pickup(self, tmp_path: Path) -> None:
        work = _make_work()
        manual_dir = tmp_path / "manual"
        manual_dir.mkdir()
        fname = work_id_to_filename(work.id)
        (manual_dir / f"{fname}.pdf").write_bytes(_pdf_content())

        client = MagicMock(spec=httpx.Client)

        record = _retrieve_one(
            work,
            tmp_path,
            client=client,
            email="test@example.com",
            manual_dir=manual_dir,
            dry_run=False,
        )

        assert record.retrieval_status == RetrievalStatus.retrieved_manual
        assert record.pdf_path is not None
        assert (tmp_path / record.pdf_path).exists()


class TestAbstractOnly:
    def test_no_sources(self, tmp_path: Path) -> None:
        work = _make_work(doi=None)
        client = MagicMock(spec=httpx.Client)

        record = _retrieve_one(
            work,
            tmp_path,
            client=client,
            email="test@example.com",
            manual_dir=None,
            dry_run=False,
        )

        assert record.retrieval_status == RetrievalStatus.abstract_only
        assert record.pdf_path is None


class TestFailedOnHttpError:
    def test_http_403(self, tmp_path: Path) -> None:
        work = _make_work(pdf_url="https://example.com/paper.pdf")

        response = httpx.Response(
            403,
            request=httpx.Request("GET", "https://example.com/paper.pdf"),
        )
        client = MagicMock(spec=httpx.Client)
        client.get.return_value = response
        # Make raise_for_status actually raise
        response.raise_for_status = MagicMock(  # type: ignore[method-assign]
            side_effect=httpx.HTTPStatusError(
                "403 Forbidden", request=response.request, response=response
            )
        )
        client.get.return_value = response

        with patch("laglitsynth.fulltext_retrieval.retrieve._rate_limit"):
            record = _retrieve_one(
                work,
                tmp_path,
                client=client,
                email="test@example.com",
                manual_dir=None,
                dry_run=False,
            )

        # OA 403 falls through the cascade to abstract_only (no Unpaywall
        # without a DOI on this test work).
        assert record.retrieval_status == RetrievalStatus.abstract_only


class TestSkipExisting:
    def test_skip_retrieved_retry_failed(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        retrieval_path = output_dir / "retrieval.jsonl"

        # Write existing records
        records = [
            RetrievalRecord(
                work_id="https://openalex.org/W1",
                retrieval_status=RetrievalStatus.retrieved_oa,
                retrieved_at="2026-01-01T00:00:00",
            ),
            RetrievalRecord(
                work_id="https://openalex.org/W2",
                retrieval_status=RetrievalStatus.failed,
                error="timeout",
                retrieved_at="2026-01-01T00:00:00",
            ),
            RetrievalRecord(
                work_id="https://openalex.org/W3",
                retrieval_status=RetrievalStatus.abstract_only,
                retrieved_at="2026-01-01T00:00:00",
            ),
        ]
        with open(retrieval_path, "w") as f:
            for r in records:
                f.write(r.model_dump_json() + "\n")

        skip_ids = _load_existing(output_dir)
        # retrieved_oa should be skipped; failed and abstract_only should not
        assert "https://openalex.org/W1" in skip_ids
        assert "https://openalex.org/W2" not in skip_ids
        assert "https://openalex.org/W3" not in skip_ids


class TestUnretrievedTxt:
    def test_unretrieved_output(self, tmp_path: Path) -> None:
        works = [
            _make_work("https://openalex.org/W1", doi="https://doi.org/10.1/a"),
            _make_work("https://openalex.org/W2", doi=None),
        ]
        _write_works_jsonl(tmp_path / "input.jsonl", works)

        args = MagicMock()
        args.input = tmp_path / "input.jsonl"
        args.output_dir = tmp_path / "out"
        args.email = "test@example.com"
        args.manual_dir = None
        args.skip_existing = False
        args.dry_run = False

        client_mock = MagicMock(spec=httpx.Client)
        # All downloads fail
        client_mock.get.side_effect = httpx.ConnectError("connection refused")

        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._rate_limit"),
        ):
            run(args)

        unretrieved = (tmp_path / "out" / "unretrieved.txt").read_text().strip().splitlines()
        assert len(unretrieved) == 2
        assert "10.1/a" in unretrieved[0]
        assert "W1.pdf" in unretrieved[0]
        assert "no-doi" in unretrieved[1]
        assert "W2.pdf" in unretrieved[1]


class TestRateLimiting:
    def test_rate_limit_sleeps(self) -> None:
        import laglitsynth.fulltext_retrieval.retrieve as mod

        # Clear state
        mod._last_request.clear()

        with patch("laglitsynth.fulltext_retrieval.retrieve.time") as mock_time:
            mock_time.monotonic.side_effect = [
                10.0,  # first call: now (no domain entry yet, skip sleep)
                10.0,  # first call: update _last_request
                10.3,  # second call: now (only 0.3s later)
                11.0,  # second call: update _last_request after sleep
            ]
            mock_time.sleep = MagicMock()

            _rate_limit("example.com", min_interval=1.0)
            assert mock_time.sleep.call_count == 0

            _rate_limit("example.com", min_interval=1.0)
            mock_time.sleep.assert_called_once_with(pytest.approx(0.7, abs=0.01))
