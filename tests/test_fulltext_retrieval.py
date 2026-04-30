"""Tests for the fulltext retrieval submodule."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from laglitsynth.fulltext_retrieval.models import RetrievalRecord, RetrievalStatus
from laglitsynth.fulltext_retrieval.retrieve import (
    _DOI_PREFIX_RE,
    _RateLimiter,
    _retrieve_one,
    _validate_pdf,
    run,
)
from laglitsynth.ids import work_id_to_filename
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


def _make_rate_limiter() -> _RateLimiter:
    rl = _RateLimiter()
    return rl


class TestWorkIdToFilename:
    def test_extracts_suffix(self) -> None:
        assert work_id_to_filename("https://openalex.org/W1234567890") == "W1234567890"

    def test_bare_id(self) -> None:
        assert work_id_to_filename("W999") == "W999"


class TestValidatePdf:
    def test_pdf_header(self) -> None:
        assert _validate_pdf(b"%PDF-1.4 content") is True

    def test_rejects_html_with_pdf_content_type(self) -> None:
        # Content-type alone is not enough; magic bytes must be %PDF.
        assert _validate_pdf(b"<html>") is False

    def test_rejects_html_body(self) -> None:
        # HTML body, would-be application/pdf content-type, must be rejected.
        assert _validate_pdf(b"<html><body>error page</body></html>") is False

    def test_invalid(self) -> None:
        assert _validate_pdf(b"not pdf at all") is False


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
        rate_limiter = _RateLimiter()

        with patch.object(rate_limiter, "wait"):
            record = _retrieve_one(
                work,
                tmp_path,
                client=client,
                email="test@example.com",
                manual_dir=None,
                dry_run=False,
                rate_limiter=rate_limiter,
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
        rate_limiter = _RateLimiter()

        with patch.object(rate_limiter, "wait"):
            record = _retrieve_one(
                work,
                tmp_path,
                client=client,
                email="test@example.com",
                manual_dir=None,
                dry_run=False,
                rate_limiter=rate_limiter,
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
        rate_limiter = _RateLimiter()

        record = _retrieve_one(
            work,
            tmp_path,
            client=client,
            email="test@example.com",
            manual_dir=manual_dir,
            dry_run=False,
            rate_limiter=rate_limiter,
        )

        assert record.retrieval_status == RetrievalStatus.retrieved_manual
        assert record.pdf_path is not None
        assert (tmp_path / record.pdf_path).exists()


class TestAbstractOnly:
    def test_no_sources(self, tmp_path: Path) -> None:
        work = _make_work(doi=None)
        client = MagicMock(spec=httpx.Client)
        rate_limiter = _RateLimiter()

        record = _retrieve_one(
            work,
            tmp_path,
            client=client,
            email="test@example.com",
            manual_dir=None,
            dry_run=False,
            rate_limiter=rate_limiter,
        )

        assert record.retrieval_status == RetrievalStatus.abstract_only
        assert record.pdf_path is None


class TestFailedOnHttpError:
    def test_http_403(self, tmp_path: Path) -> None:
        # OA URL attempted and received a 403 — must yield failed, not abstract_only.
        work = _make_work(doi=None, pdf_url="https://example.com/paper.pdf")

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
        rate_limiter = _RateLimiter()

        with patch.object(rate_limiter, "wait"):
            record = _retrieve_one(
                work,
                tmp_path,
                client=client,
                email="test@example.com",
                manual_dir=None,
                dry_run=False,
                rate_limiter=rate_limiter,
            )

        # OA URL was attempted and failed — should be failed, not abstract_only.
        assert record.retrieval_status == RetrievalStatus.failed
        assert record.error is not None
        assert "403" in record.error

    def test_unpaywall_download_fails_marks_failed(self, tmp_path: Path) -> None:
        # Unpaywall API succeeds but the PDF download fails — must yield failed.
        work = _make_work(doi="https://doi.org/10.1234/test")

        unpaywall_response = httpx.Response(
            200,
            json={
                "best_oa_location": {
                    "url_for_pdf": "https://unpaywall.example.com/paper.pdf"
                }
            },
            request=httpx.Request(
                "GET", "https://api.unpaywall.org/v2/10.1234%2Ftest?email=test@example.com"
            ),
        )
        failed_pdf_response = httpx.Response(
            503,
            request=httpx.Request("GET", "https://unpaywall.example.com/paper.pdf"),
        )
        failed_pdf_response.raise_for_status = MagicMock(  # type: ignore[method-assign]
            side_effect=httpx.HTTPStatusError(
                "503 Service Unavailable",
                request=failed_pdf_response.request,
                response=failed_pdf_response,
            )
        )

        client = MagicMock(spec=httpx.Client)
        client.get.side_effect = [unpaywall_response, failed_pdf_response]
        rate_limiter = _RateLimiter()

        with patch.object(rate_limiter, "wait"):
            record = _retrieve_one(
                work,
                tmp_path,
                client=client,
                email="test@example.com",
                manual_dir=None,
                dry_run=False,
                rate_limiter=rate_limiter,
            )

        assert record.retrieval_status == RetrievalStatus.failed
        assert record.error is not None


class TestSkipExisting:
    def test_skip_retrieved_retry_failed(self, tmp_path: Path) -> None:
        # Verify skip-existing logic: retrieved_oa is preserved without network
        # calls; failed and abstract_only are retried.
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        retrieval_path = output_dir / "retrieval.jsonl"

        seeded = [
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
            for r in seeded:
                f.write(r.model_dump_json() + "\n")

        works = [
            _make_work("https://openalex.org/W1", doi=None),
            _make_work("https://openalex.org/W2", doi=None),
            _make_work("https://openalex.org/W3", doi=None),
        ]
        _write_works_jsonl(tmp_path / "input.jsonl", works)

        args = MagicMock()
        args.input = tmp_path / "input.jsonl"
        args.output_dir = output_dir
        args.email = "test@example.com"
        args.manual_dir = None
        args.skip_existing = True
        args.dry_run = False

        client_mock = MagicMock(spec=httpx.Client)
        client_mock.get.side_effect = httpx.ConnectError("connection refused")

        rl = _RateLimiter()
        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._RateLimiter", return_value=rl),
        ):
            run(args)

        # W1 (retrieved_oa) must be preserved; no network call attempted for it.
        # W2 (failed) and W3 (abstract_only) must be re-processed — both will
        # again be abstract_only (no OA URLs, no DOI, no manual).
        lines = [l for l in retrieval_path.read_text().splitlines() if l.strip()]
        final = {json.loads(l)["work_id"]: json.loads(l)["retrieval_status"] for l in lines}
        assert final["https://openalex.org/W1"] == "retrieved_oa"
        assert final["https://openalex.org/W2"] == "abstract_only"
        assert final["https://openalex.org/W3"] == "abstract_only"


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

        rl = _RateLimiter()
        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._RateLimiter", return_value=rl),
            patch.object(rl, "wait"),
        ):
            run(args)

        unretrieved = (tmp_path / "out" / "unretrieved.txt").read_text().strip().splitlines()
        assert len(unretrieved) == 2
        assert "10.1/a" in unretrieved[0]
        assert "W1.pdf" in unretrieved[0]
        assert "no-doi" in unretrieved[1]
        assert "W2.pdf" in unretrieved[1]

    def test_unretrieved_output_dry_run(self, tmp_path: Path) -> None:
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
        args.dry_run = True

        # No network calls expected under dry_run.
        client_mock = MagicMock(spec=httpx.Client)

        rl = _RateLimiter()
        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._RateLimiter", return_value=rl),
        ):
            run(args)

        unretrieved_path = tmp_path / "out" / "unretrieved.txt"
        assert unretrieved_path.exists()
        lines = unretrieved_path.read_text().strip().splitlines()
        # Both W1 and W2 have no OA URLs and no manual files → abstract_only
        assert len(lines) == 2

    def test_unretrieved_output_skip_existing(self, tmp_path: Path) -> None:
        # Pre-seed retrieval.jsonl with a failed record for W1 and retrieved_oa for W2.
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        retrieval_path = output_dir / "retrieval.jsonl"

        seeded = [
            RetrievalRecord(
                work_id="https://openalex.org/W1",
                retrieval_status=RetrievalStatus.failed,
                error="timeout",
                retrieved_at="2026-01-01T00:00:00",
            ),
            RetrievalRecord(
                work_id="https://openalex.org/W2",
                retrieval_status=RetrievalStatus.retrieved_oa,
                retrieved_at="2026-01-01T00:00:00",
            ),
        ]
        with open(retrieval_path, "w") as f:
            for r in seeded:
                f.write(r.model_dump_json() + "\n")

        works = [
            _make_work("https://openalex.org/W1", doi="https://doi.org/10.1/a"),
            _make_work("https://openalex.org/W2", doi=None),
        ]
        _write_works_jsonl(tmp_path / "input.jsonl", works)

        args = MagicMock()
        args.input = tmp_path / "input.jsonl"
        args.output_dir = output_dir
        args.email = "test@example.com"
        args.manual_dir = None
        args.skip_existing = True
        args.dry_run = False

        # W1 will be retried; all downloads fail.
        client_mock = MagicMock(spec=httpx.Client)
        client_mock.get.side_effect = httpx.ConnectError("connection refused")

        rl = _RateLimiter()
        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._RateLimiter", return_value=rl),
            patch.object(rl, "wait"),
        ):
            run(args)

        unretrieved_path = output_dir / "unretrieved.txt"
        lines = unretrieved_path.read_text().strip().splitlines()
        # W1 still failed → in unretrieved; W2 is retrieved_oa → not in unretrieved
        assert any("W1.pdf" in line for line in lines)
        assert not any("W2.pdf" in line for line in lines)


class TestRetrievalJsonl:
    def test_no_duplicates_on_rerun(self, tmp_path: Path) -> None:
        works = [
            _make_work("https://openalex.org/W1", doi=None),
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
        client_mock.get.side_effect = httpx.ConnectError("connection refused")

        rl = _RateLimiter()
        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._RateLimiter", return_value=rl),
        ):
            run(args)
            run(args)

        retrieval_path = tmp_path / "out" / "retrieval.jsonl"
        lines = [l for l in retrieval_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        work_ids = [json.loads(l)["work_id"] for l in lines]
        assert len(set(work_ids)) == 2

    def test_preserves_existing_rows_under_skip_existing(self, tmp_path: Path) -> None:
        output_dir = tmp_path / "out"
        output_dir.mkdir()
        retrieval_path = output_dir / "retrieval.jsonl"

        existing_rec = RetrievalRecord(
            work_id="https://openalex.org/W1",
            retrieval_status=RetrievalStatus.retrieved_oa,
            retrieved_at="2026-01-01T00:00:00",
        )
        with open(retrieval_path, "w") as f:
            f.write(existing_rec.model_dump_json() + "\n")

        # Input has the pre-seeded work plus a new work.
        works = [
            _make_work("https://openalex.org/W1", doi=None),
            _make_work("https://openalex.org/W2", doi=None),
        ]
        _write_works_jsonl(tmp_path / "input.jsonl", works)

        args = MagicMock()
        args.input = tmp_path / "input.jsonl"
        args.output_dir = output_dir
        args.email = "test@example.com"
        args.manual_dir = None
        args.skip_existing = True
        args.dry_run = False

        client_mock = MagicMock(spec=httpx.Client)
        client_mock.get.side_effect = httpx.ConnectError("connection refused")

        rl = _RateLimiter()
        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._RateLimiter", return_value=rl),
        ):
            run(args)

        lines = [l for l in retrieval_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        work_ids = [json.loads(l)["work_id"] for l in lines]
        assert "https://openalex.org/W1" in work_ids
        assert "https://openalex.org/W2" in work_ids


class TestRateLimiting:
    def test_rate_limit_sleeps(self) -> None:
        rl = _RateLimiter()

        with patch("laglitsynth.fulltext_retrieval.retrieve.time") as mock_time:
            mock_time.monotonic.side_effect = [
                10.0,  # first wait(): now (no domain entry yet, skip sleep)
                10.0,  # first wait(): record _last_request
                10.3,  # second wait(): now (only 0.3s later)
                11.0,  # second wait(): record _last_request after sleep
            ]
            mock_time.sleep = MagicMock()

            rl.wait("example.com", min_interval=1.0)
            assert mock_time.sleep.call_count == 0

            rl.wait("example.com", min_interval=1.0)
            mock_time.sleep.assert_called_once_with(pytest.approx(0.7, abs=0.01))


class TestDoiNormalisation:
    """Verify that DOI prefix stripping works for all realistic input forms."""

    def test_https_prefix(self) -> None:
        doi = "https://doi.org/10.1234/test"
        assert _DOI_PREFIX_RE.sub("", doi) == "10.1234/test"

    def test_http_prefix(self) -> None:
        doi = "http://doi.org/10.1234/test"
        assert _DOI_PREFIX_RE.sub("", doi) == "10.1234/test"

    def test_dx_doi_org(self) -> None:
        doi = "https://dx.doi.org/10.1234/test"
        assert _DOI_PREFIX_RE.sub("", doi) == "10.1234/test"

    def test_uppercase_https(self) -> None:
        doi = "HTTPS://DOI.ORG/10.1234/test"
        assert _DOI_PREFIX_RE.sub("", doi) == "10.1234/test"

    def test_bare_doi(self) -> None:
        doi = "10.1234/test"
        assert _DOI_PREFIX_RE.sub("", doi) == "10.1234/test"


class TestDryRunStatusHonesty:
    def test_doi_only_work_yields_abstract_only(self, tmp_path: Path) -> None:
        # A work with only a DOI (no OA URLs, no manual file) under --dry-run
        # must produce abstract_only, not retrieved_unpaywall.
        work = _make_work(doi="https://doi.org/10.1234/test")
        client = MagicMock(spec=httpx.Client)
        rate_limiter = _RateLimiter()

        record = _retrieve_one(
            work,
            tmp_path,
            client=client,
            email="test@example.com",
            manual_dir=None,
            dry_run=True,
            rate_limiter=rate_limiter,
        )

        assert record.retrieval_status == RetrievalStatus.abstract_only


class TestUnpaywallEmail:
    """Email passed via --email is plumbed into the User-Agent and Unpaywall URL."""

    def _base_args(self, tmp_path: Path, email: str = "test@example.com") -> MagicMock:
        _write_works_jsonl(tmp_path / "input.jsonl", [])
        args = MagicMock()
        args.input = tmp_path / "input.jsonl"
        args.output_dir = tmp_path / "out"
        args.email = email
        args.manual_dir = None
        args.skip_existing = False
        args.dry_run = True
        return args

    def test_email_flag_used_in_user_agent(self, tmp_path: Path) -> None:
        args = self._base_args(tmp_path, email="flag@example.com")

        with patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client") as client_cls:
            run(args)

        ua = client_cls.call_args.kwargs["headers"]["User-Agent"]
        assert "flag@example.com" in ua

    def test_email_flag_plumbed_into_unpaywall_url(self, tmp_path: Path) -> None:
        # Run a real (non-dry-run) retrieval for a work with a DOI and no OA
        # URLs so the Unpaywall path is exercised.  Assert the URL the mock
        # client received contains email=<value>.
        work = _make_work(doi="https://doi.org/10.1234/test")
        _write_works_jsonl(tmp_path / "input.jsonl", [work])

        args = MagicMock()
        args.input = tmp_path / "input.jsonl"
        args.output_dir = tmp_path / "out"
        args.email = "addr@example.com"
        args.manual_dir = None
        args.skip_existing = False
        args.dry_run = False

        unpaywall_response = httpx.Response(
            200,
            json={"best_oa_location": None},
            request=httpx.Request("GET", "https://api.unpaywall.org/v2/10.1234%2Ftest"),
        )
        client_mock = MagicMock(spec=httpx.Client)
        client_mock.get.return_value = unpaywall_response

        rl = _RateLimiter()
        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._RateLimiter", return_value=rl),
            patch.object(rl, "wait"),
        ):
            run(args)

        called_url = client_mock.get.call_args[0][0]
        assert "email=addr%40example.com" in called_url or "email=addr@example.com" in called_url
