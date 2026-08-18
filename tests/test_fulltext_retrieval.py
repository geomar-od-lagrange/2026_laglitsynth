"""Tests for the fulltext retrieval submodule (shared store)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.fulltext_retrieval.models import PdfSource
from laglitsynth.fulltext_retrieval.retrieve import (
    _DOI_PREFIX_RE,
    _RateLimiter,
    _active_works,
    _retrieve_one,
    _validate_pdf,
    run,
)
from laglitsynth.fulltext_retrieval.store import load_provenance, store_pdf_path
from laglitsynth.ids import work_id_to_filename
from laglitsynth.io import write_meta
from laglitsynth.manifest import RunManifest, StageEntry, load_manifest
from laglitsynth.models import RunMeta
from laglitsynth.screening_abstracts.models import ScreeningVerdict

from conftest import _make_work, _write_works_jsonl


def _seed_manifest(data_dir: Path, stages: list[StageEntry]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    write_meta(
        data_dir / "manifest.json",
        RunManifest(
            run=RunMeta(
                tool="laglitsynth.manifest",
                run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
                validation_skipped=0,
            ),
            run_id="2026-01-01T00-00-00_abcdef123456",
            queries=["test query"],
            data_dir=str(data_dir),
            stages=stages,
        ),
    )


def _stage_entry(stage: str, output: str) -> StageEntry:
    return StageEntry(
        stage=stage,
        run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        inputs={},
        output=output,
        meta_path=None,
    )


def _write_verdicts_jsonl(path: Path, verdicts: list[ScreeningVerdict]) -> None:
    with open(path, "w") as f:
        for v in verdicts:
            f.write(v.model_dump_json() + "\n")


def _make_passthrough_args(
    tmp_path: Path,
    works: list[Work],
    *,
    threshold: float = 50.0,
    score: int = 80,
    data_subdir: str = "data",
    email: str = "test@example.com",
) -> MagicMock:
    """Build a MagicMock args suitable for calling ``run()``.

    Writes ``catalogue.jsonl`` and ``verdicts.jsonl`` under ``tmp_path``.
    Every work in ``works`` gets a verdict with ``relevance_score=score``
    (above ``threshold`` by default), so the inline join passes all works
    through unchanged.
    """
    catalogue_path = tmp_path / "catalogue.jsonl"
    verdicts_path = tmp_path / "verdicts.jsonl"
    _write_works_jsonl(catalogue_path, works)
    verdicts = [
        ScreeningVerdict(work_id=w.id, relevance_score=score)
        for w in works
    ]
    _write_verdicts_jsonl(verdicts_path, verdicts)

    args = MagicMock()
    args.catalogue = catalogue_path
    args.screening_verdicts = verdicts_path
    args.screening_threshold = threshold
    args.data_dir = tmp_path / data_subdir
    args.email = email
    args.refetch = False
    args.dry_run = False
    return args


def _pdf_content() -> bytes:
    return b"%PDF-1.4 fake pdf content"


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
                dry_run=False,
                rate_limiter=rate_limiter,
            )

        assert record.source == PdfSource.oa
        assert record.source_url == "https://example.com/paper.pdf"
        assert record.pdf_path == "pdfs/W1.pdf"
        assert record.content_sha256 is not None
        assert store_pdf_path(tmp_path, record.stem).exists()


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
                dry_run=False,
                rate_limiter=rate_limiter,
            )

        assert record.source == PdfSource.unpaywall
        assert record.source_url == "https://unpaywall.example.com/paper.pdf"


class TestMissing:
    def test_no_sources(self, tmp_path: Path) -> None:
        work = _make_work(doi=None)
        client = MagicMock(spec=httpx.Client)
        rate_limiter = _RateLimiter()

        record = _retrieve_one(
            work,
            tmp_path,
            client=client,
            email="test@example.com",
            dry_run=False,
            rate_limiter=rate_limiter,
        )

        assert record.source == PdfSource.missing
        assert record.pdf_path is None
        assert record.content_sha256 is None
        assert not store_pdf_path(tmp_path, record.stem).exists()

    def test_http_403_is_missing(self, tmp_path: Path) -> None:
        # OA URL attempted and received a 403 — for the store, the only fact
        # that matters is that no PDF exists: record `missing`.
        work = _make_work(doi=None, pdf_url="https://example.com/paper.pdf")

        response = httpx.Response(
            403,
            request=httpx.Request("GET", "https://example.com/paper.pdf"),
        )
        response.raise_for_status = MagicMock(  # type: ignore[method-assign]
            side_effect=httpx.HTTPStatusError(
                "403 Forbidden", request=response.request, response=response
            )
        )
        client = MagicMock(spec=httpx.Client)
        client.get.return_value = response
        rate_limiter = _RateLimiter()

        with patch.object(rate_limiter, "wait"):
            record = _retrieve_one(
                work,
                tmp_path,
                client=client,
                email="test@example.com",
                dry_run=False,
                rate_limiter=rate_limiter,
            )

        assert record.source == PdfSource.missing
        assert record.pdf_path is None


class TestStickyByDefault:
    def test_plain_run_skips_held_attempts_missing(self, tmp_path: Path) -> None:
        # Sticky default: a work with a non-missing record is skipped entirely
        # (no download attempted, PDF untouched); a `missing` record is retried.
        data_dir = tmp_path / "data"
        works = [
            _make_work("https://openalex.org/W1", doi=None),
            _make_work("https://openalex.org/W2", doi=None),
        ]
        args = _make_passthrough_args(tmp_path, works)
        args.data_dir = data_dir
        args.refetch = False

        # Seed W1 as already retrieved (oa) and W2 as missing.
        from laglitsynth.fulltext_retrieval.models import PdfProvenanceRecord
        from laglitsynth.fulltext_retrieval.store import write_provenance

        pdf_path = store_pdf_path(data_dir, "W1")
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(_pdf_content())
        seeded = {
            "https://openalex.org/W1": PdfProvenanceRecord(
                work_id="https://openalex.org/W1",
                stem="W1",
                doi=None,
                source=PdfSource.oa,
                source_url="https://example.com/p.pdf",
                pdf_path="pdfs/W1.pdf",
                content_sha256="seedsha",
                obtained_at="2026-01-01T00:00:00+00:00",
            ),
            "https://openalex.org/W2": PdfProvenanceRecord(
                work_id="https://openalex.org/W2",
                stem="W2",
                doi=None,
                source=PdfSource.missing,
                source_url=None,
                pdf_path=None,
                content_sha256=None,
                obtained_at="2026-01-01T00:00:00+00:00",
            ),
        }
        write_provenance(data_dir, seeded)

        client_mock = MagicMock(spec=httpx.Client)
        client_mock.get.side_effect = httpx.ConnectError("connection refused")

        rl = _RateLimiter()
        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._RateLimiter", return_value=rl),
        ):
            run(args)

        final = load_provenance(data_dir)
        # (a) W1 untouched (still oa, original sha preserved, PDF still on disk).
        assert final["https://openalex.org/W1"].source == PdfSource.oa
        assert final["https://openalex.org/W1"].content_sha256 == "seedsha"
        assert pdf_path.read_bytes() == _pdf_content()
        # No download was attempted for W1 — the only network calls are for the
        # retried `missing` work W2 (a no-DOI work has no OA URL, so even W2
        # makes no call here; the held work is skipped before any attempt).
        assert client_mock.get.call_count == 0
        # (b) W2 retried, still no sources → missing.
        assert final["https://openalex.org/W2"].source == PdfSource.missing

    def test_refetch_failure_keeps_prior_record(self, tmp_path: Path) -> None:
        # (c) --refetch re-attempts a held work; when every download fails the
        # no-downgrade guard keeps the prior `oa` record with its PDF present
        # rather than clobbering provenance to `missing`.
        data_dir = tmp_path / "data"
        works = [
            _make_work(
                "https://openalex.org/W1",
                doi=None,
                pdf_url="https://example.com/p.pdf",
            ),
        ]
        args = _make_passthrough_args(tmp_path, works)
        args.data_dir = data_dir
        args.refetch = True

        from laglitsynth.fulltext_retrieval.models import PdfProvenanceRecord
        from laglitsynth.fulltext_retrieval.store import write_provenance

        pdf_path = store_pdf_path(data_dir, "W1")
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.write_bytes(_pdf_content())
        write_provenance(
            data_dir,
            {
                "https://openalex.org/W1": PdfProvenanceRecord(
                    work_id="https://openalex.org/W1",
                    stem="W1",
                    doi=None,
                    source=PdfSource.oa,
                    source_url="https://example.com/p.pdf",
                    pdf_path="pdfs/W1.pdf",
                    content_sha256="seedsha",
                    obtained_at="2026-01-01T00:00:00+00:00",
                ),
            },
        )

        # Every download attempt fails.
        client_mock = MagicMock(spec=httpx.Client)
        client_mock.get.side_effect = httpx.ConnectError("connection refused")

        rl = _RateLimiter()
        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._RateLimiter", return_value=rl),
        ):
            run(args)

        # The held work was re-attempted (a download call was made)...
        assert client_mock.get.call_count >= 1
        # ...but the prior good record is preserved and the PDF is untouched.
        final = load_provenance(data_dir)
        assert final["https://openalex.org/W1"].source == PdfSource.oa
        assert final["https://openalex.org/W1"].content_sha256 == "seedsha"
        assert pdf_path.read_bytes() == _pdf_content()


class TestProvenanceJsonl:
    def test_no_duplicates_on_rerun(self, tmp_path: Path) -> None:
        works = [
            _make_work("https://openalex.org/W1", doi=None),
            _make_work("https://openalex.org/W2", doi=None),
        ]
        args = _make_passthrough_args(tmp_path, works)

        client_mock = MagicMock(spec=httpx.Client)
        client_mock.get.side_effect = httpx.ConnectError("connection refused")

        rl = _RateLimiter()
        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._RateLimiter", return_value=rl),
        ):
            run(args)
            run(args)

        prov_path = args.data_dir / "pdfs" / "provenance.jsonl"
        lines = [l for l in prov_path.read_text().splitlines() if l.strip()]
        assert len(lines) == 2
        work_ids = [json.loads(l)["work_id"] for l in lines]
        assert len(set(work_ids)) == 2

    def test_preserves_other_works_rows(self, tmp_path: Path) -> None:
        # A work outside this run's input set keeps its record.
        from laglitsynth.fulltext_retrieval.models import PdfProvenanceRecord
        from laglitsynth.fulltext_retrieval.store import write_provenance

        data_dir = tmp_path / "data"
        write_provenance(
            data_dir,
            {
                "https://openalex.org/W9": PdfProvenanceRecord(
                    work_id="https://openalex.org/W9",
                    stem="W9",
                    doi=None,
                    source=PdfSource.manual,
                    source_url=None,
                    pdf_path="pdfs/W9.pdf",
                    content_sha256="x",
                    obtained_at="2026-01-01T00:00:00+00:00",
                )
            },
        )

        works = [_make_work("https://openalex.org/W1", doi=None)]
        args = _make_passthrough_args(tmp_path, works)
        args.data_dir = data_dir

        client_mock = MagicMock(spec=httpx.Client)
        client_mock.get.side_effect = httpx.ConnectError("connection refused")

        rl = _RateLimiter()
        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._RateLimiter", return_value=rl),
        ):
            run(args)

        final = load_provenance(data_dir)
        assert final["https://openalex.org/W9"].source == PdfSource.manual
        assert final["https://openalex.org/W1"].source == PdfSource.missing


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
    def test_doi_only_work_yields_missing(self, tmp_path: Path) -> None:
        # A work with only a DOI (no OA URLs) under --dry-run must produce
        # `missing`, not unpaywall.
        work = _make_work(doi="https://doi.org/10.1234/test")
        client = MagicMock(spec=httpx.Client)
        rate_limiter = _RateLimiter()

        record = _retrieve_one(
            work,
            tmp_path,
            client=client,
            email="test@example.com",
            dry_run=True,
            rate_limiter=rate_limiter,
        )

        assert record.source == PdfSource.missing


class TestUnpaywallEmail:
    """Email passed via --email is plumbed into the User-Agent and Unpaywall URL."""

    def _base_args(self, tmp_path: Path, email: str = "test@example.com") -> MagicMock:
        args = _make_passthrough_args(tmp_path, [], email=email)
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
        args = _make_passthrough_args(tmp_path, [work], email="addr@example.com")

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


class TestValidationSkipped:
    def test_validation_skipped_counts_invalid_catalogue_lines(
        self, tmp_path: Path
    ) -> None:
        """meta.run.validation_skipped reflects malformed lines in catalogue and verdicts."""
        work = _make_work("https://openalex.org/W1", doi=None)
        catalogue_path = tmp_path / "catalogue.jsonl"
        verdicts_path = tmp_path / "verdicts.jsonl"

        with open(catalogue_path, "w") as f:
            f.write(work.model_dump_json() + "\n")
            f.write('{"not_a_real_field": "x"}\n')

        verdict = ScreeningVerdict(work_id="https://openalex.org/W1", relevance_score=80)
        with open(verdicts_path, "w") as f:
            f.write(verdict.model_dump_json() + "\n")
            f.write('{"not_a_real_field": "y"}\n')

        args = MagicMock()
        args.catalogue = catalogue_path
        args.screening_verdicts = verdicts_path
        args.screening_threshold = 50.0
        args.data_dir = tmp_path / "data"
        args.email = "test@example.com"
        args.refetch = False
        args.dry_run = False

        client_mock = MagicMock(spec=httpx.Client)
        client_mock.get.side_effect = httpx.ConnectError("connection refused")

        rl = _RateLimiter()
        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._RateLimiter", return_value=rl),
        ):
            run(args)

        meta_path = tmp_path / "data" / "fulltext-retrieval" / "retrieval-meta.json"
        meta = json.loads(meta_path.read_text())
        assert meta["run"]["validation_skipped"] == 2


class TestRetrievalMeta:
    def test_missing_count_reflects_unretrieved_tally(self, tmp_path: Path) -> None:
        # Two no-DOI works with no OA URLs both fall through to `missing`, so
        # the meta's missing_count is 2 and retrieved_count is 0.
        works = [
            _make_work("https://openalex.org/W1", doi=None),
            _make_work("https://openalex.org/W2", doi=None),
        ]
        args = _make_passthrough_args(tmp_path, works)

        client_mock = MagicMock(spec=httpx.Client)
        client_mock.get.side_effect = httpx.ConnectError("connection refused")

        rl = _RateLimiter()
        with (
            patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client", return_value=client_mock),
            patch("laglitsynth.fulltext_retrieval.retrieve._RateLimiter", return_value=rl),
        ):
            run(args)

        meta_path = args.data_dir / "fulltext-retrieval" / "retrieval-meta.json"
        meta = json.loads(meta_path.read_text())
        assert meta["total_works"] == 2
        assert meta["missing_count"] == 2
        assert meta["retrieved_count"] == 0
        assert "abstract_only_count" not in meta
        assert "failed_count" not in meta


class TestActiveWorksJoin:
    """Unit tests for the _active_works inline-join helper."""

    def test_active_works_threshold(self, tmp_path: Path) -> None:
        """Works with a score at or above the threshold pass; those below are filtered."""
        works = [
            _make_work("https://openalex.org/W1"),
            _make_work("https://openalex.org/W2"),
            _make_work("https://openalex.org/W3"),
        ]
        verdicts = [
            ScreeningVerdict(work_id="https://openalex.org/W1", relevance_score=80),
            ScreeningVerdict(work_id="https://openalex.org/W2", relevance_score=49),
            ScreeningVerdict(work_id="https://openalex.org/W3", relevance_score=50),
        ]
        catalogue_path = tmp_path / "catalogue.jsonl"
        verdicts_path = tmp_path / "verdicts.jsonl"
        _write_works_jsonl(catalogue_path, works)
        _write_verdicts_jsonl(verdicts_path, verdicts)

        result = list(_active_works(catalogue_path, verdicts_path, screening_threshold=50.0))
        ids = {w.id for w in result}
        assert ids == {
            "https://openalex.org/W1",
            "https://openalex.org/W3",
        }
        assert "https://openalex.org/W2" not in ids

    def test_null_score_sentinels_ride_through(self, tmp_path: Path) -> None:
        """Works with relevance_score=None (sentinel reasons) always pass, regardless of threshold."""
        works = [
            _make_work("https://openalex.org/W1"),
            _make_work("https://openalex.org/W2"),
            _make_work("https://openalex.org/W3"),
            _make_work("https://openalex.org/W4"),
            _make_work("https://openalex.org/W5"),
        ]
        verdicts = [
            ScreeningVerdict(
                work_id="https://openalex.org/W1",
                relevance_score=None,
                reason="no-abstract",
            ),
            ScreeningVerdict(
                work_id="https://openalex.org/W2",
                relevance_score=None,
                reason="llm-parse-failure",
            ),
            ScreeningVerdict(
                work_id="https://openalex.org/W3",
                relevance_score=None,
                reason="llm-timeout",
            ),
            ScreeningVerdict(
                work_id="https://openalex.org/W4",
                relevance_score=80,
            ),
            ScreeningVerdict(
                work_id="https://openalex.org/W5",
                relevance_score=10,
            ),
        ]
        catalogue_path = tmp_path / "catalogue.jsonl"
        verdicts_path = tmp_path / "verdicts.jsonl"
        _write_works_jsonl(catalogue_path, works)
        _write_verdicts_jsonl(verdicts_path, verdicts)

        result = list(_active_works(catalogue_path, verdicts_path, screening_threshold=50.0))
        ids = {w.id for w in result}
        assert ids == {
            "https://openalex.org/W1",
            "https://openalex.org/W2",
            "https://openalex.org/W3",
            "https://openalex.org/W4",
        }
        assert "https://openalex.org/W5" not in ids

    def test_works_without_verdicts_are_excluded(self, tmp_path: Path) -> None:
        """Works present in the catalogue but absent from verdicts are excluded."""
        works = [
            _make_work("https://openalex.org/W1"),
            _make_work("https://openalex.org/W2"),
        ]
        verdicts = [
            ScreeningVerdict(work_id="https://openalex.org/W1", relevance_score=90),
        ]
        catalogue_path = tmp_path / "catalogue.jsonl"
        verdicts_path = tmp_path / "verdicts.jsonl"
        _write_works_jsonl(catalogue_path, works)
        _write_verdicts_jsonl(verdicts_path, verdicts)

        result = list(_active_works(catalogue_path, verdicts_path, screening_threshold=50.0))
        assert len(result) == 1
        assert result[0].id == "https://openalex.org/W1"


class TestEmailDotenvFallback:
    """Tests for the .env fallback behaviour of --email."""

    def _make_args(self, tmp_path: Path, email: str | None) -> MagicMock:
        args = _make_passthrough_args(tmp_path, [], email="placeholder@example.com")
        args.email = email
        args.dry_run = True
        return args

    def test_explicit_flag_wins_over_env(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--email from CLI wins; .env value is not used."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("UNPAYWALL_EMAIL=from-env@example.com\n")

        args = _make_passthrough_args(tmp_path, [], email="from-flag@example.com")
        args.dry_run = True

        with patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client") as client_cls:
            run(args)

        ua = client_cls.call_args.kwargs["headers"]["User-Agent"]
        assert "from-flag@example.com" in ua
        assert "from-env" not in ua

    def test_missing_flag_loads_from_env(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """No --email flag: value is read from .env with a stderr notice."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env").write_text("UNPAYWALL_EMAIL=env-user@example.com\n")

        args = self._make_args(tmp_path, email=None)

        with patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client") as client_cls:
            run(args)

        ua = client_cls.call_args.kwargs["headers"]["User-Agent"]
        assert "env-user@example.com" in ua
        captured = capsys.readouterr()
        assert "Loaded UNPAYWALL_EMAIL from .env" in captured.err

    def test_missing_both_fails_with_clear_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No flag and no .env: SystemExit with a clear message."""
        monkeypatch.chdir(tmp_path)
        args = self._make_args(tmp_path, email=None)

        with pytest.raises(SystemExit) as exc_info:
            run(args)

        assert "UNPAYWALL_EMAIL" in str(exc_info.value)
        assert ".env" in str(exc_info.value)


class TestManifestWiring:
    def _write_verdicts(self, path: Path, work_id: str, score: float) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            f.write(
                ScreeningVerdict(work_id=work_id, relevance_score=score).model_dump_json()
                + "\n"
            )

    def test_omitted_inputs_resolve_from_manifest_and_append_entry(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "data"
        work = _make_work("https://openalex.org/W1", doi=None)

        catalogue_path = data_dir / "catalogue-dedup" / "deduplicated.jsonl"
        catalogue_path.parent.mkdir(parents=True, exist_ok=True)
        _write_works_jsonl(catalogue_path, [work])

        verdicts_path = data_dir / "screening-abstracts" / "run1" / "verdicts.jsonl"
        self._write_verdicts(verdicts_path, work.id, 80)

        _seed_manifest(
            data_dir,
            [
                _stage_entry("catalogue-dedup", str(catalogue_path)),
                _stage_entry("screening-abstracts", str(verdicts_path)),
            ],
        )

        args = MagicMock()
        args.catalogue = None
        args.screening_verdicts = None
        args.screening_threshold = 50.0
        args.data_dir = data_dir
        args.email = "test@example.com"
        args.refetch = False
        args.dry_run = True

        with patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client"):
            run(args)

        manifest = load_manifest(data_dir)
        assert manifest is not None
        entry = manifest.stages[-1]
        assert entry.stage == "fulltext-retrieval"
        assert entry.inputs == {
            "catalogue": str(catalogue_path),
            "screening_verdicts": str(verdicts_path),
        }
        assert entry.output == str(data_dir / "pdfs")
        assert entry.meta_path == str(
            data_dir / "fulltext-retrieval" / "retrieval-meta.json"
        )

    def test_explicit_flag_wins_over_manifest(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        work = _make_work("https://openalex.org/W1", doi=None)

        recorded_catalogue = data_dir / "catalogue-dedup" / "deduplicated.jsonl"
        recorded_catalogue.parent.mkdir(parents=True, exist_ok=True)
        _write_works_jsonl(recorded_catalogue, [work])
        recorded_verdicts = data_dir / "screening-abstracts" / "run1" / "verdicts.jsonl"
        self._write_verdicts(recorded_verdicts, work.id, 80)
        _seed_manifest(
            data_dir,
            [
                _stage_entry("catalogue-dedup", str(recorded_catalogue)),
                _stage_entry("screening-abstracts", str(recorded_verdicts)),
            ],
        )

        explicit_catalogue = tmp_path / "explicit-catalogue.jsonl"
        _write_works_jsonl(explicit_catalogue, [work])
        explicit_verdicts = tmp_path / "explicit-verdicts.jsonl"
        self._write_verdicts(explicit_verdicts, work.id, 80)

        args = MagicMock()
        args.catalogue = explicit_catalogue
        args.screening_verdicts = explicit_verdicts
        args.screening_threshold = 50.0
        args.data_dir = data_dir
        args.email = "test@example.com"
        args.refetch = False
        args.dry_run = True

        with patch("laglitsynth.fulltext_retrieval.retrieve.httpx.Client"):
            run(args)

        manifest = load_manifest(data_dir)
        assert manifest is not None
        entry = manifest.stages[-1]
        assert entry.inputs == {
            "catalogue": str(explicit_catalogue),
            "screening_verdicts": str(explicit_verdicts),
        }

    def test_no_manifest_and_omitted_catalogue_names_the_flag(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "data"
        args = MagicMock()
        args.catalogue = None
        args.screening_verdicts = None
        args.screening_threshold = 50.0
        args.data_dir = data_dir
        args.email = "test@example.com"
        args.refetch = False
        args.dry_run = True

        with pytest.raises(SystemExit) as exc_info:
            run(args)
        assert "--catalogue" in str(exc_info.value)

    def test_manifest_naming_a_missing_file_raises_operator_visible_error(
        self, tmp_path: Path
    ) -> None:
        data_dir = tmp_path / "data"
        # The manifest claims a catalogue-dedup output that never made it to
        # this disk (e.g. rsync brought data/manifest.json but not the file).
        _seed_manifest(
            data_dir,
            [_stage_entry("catalogue-dedup", str(data_dir / "nowhere.jsonl"))],
        )

        args = MagicMock()
        args.catalogue = None
        args.screening_verdicts = None
        args.screening_threshold = 50.0
        args.data_dir = data_dir
        args.email = "test@example.com"
        args.refetch = False
        args.dry_run = True

        with pytest.raises(SystemExit) as exc_info:
            run(args)
        assert "does not exist here" in str(exc_info.value)
