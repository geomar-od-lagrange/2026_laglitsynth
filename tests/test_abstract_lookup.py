"""Tests for the abstract-lookup stage.

Source clients are exercised with ``httpx.MockTransport`` so no network is
touched and no ``respx`` dependency is needed.  End-to-end runs use
``monkeypatch`` to inject a client backed by a mock transport.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

import laglitsynth.abstract_lookup.lookup as lookup_mod
from laglitsynth.abstract_lookup.lookup import lookup_abstract, run
from laglitsynth.abstract_lookup.models import AbstractLookupMeta, AbstractRecord
from laglitsynth.abstract_lookup.sources import (
    fetch_crossref,
    fetch_openalex,
    fetch_semantic_scholar,
    reconstruct_inverted_index,
    strip_jats,
)

from conftest import _make_work, _write_works_jsonl


def _client(handler: object) -> httpx.Client:
    """Build an httpx.Client whose requests are served by ``handler``."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return httpx.Client(transport=transport)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_reconstruct_inverted_index_orders_by_position() -> None:
    index = {"world": [1], "hello": [0]}
    assert reconstruct_inverted_index(index) == "hello world"


def test_reconstruct_inverted_index_none() -> None:
    assert reconstruct_inverted_index(None) is None
    assert reconstruct_inverted_index({}) is None


def test_strip_jats_removes_tags() -> None:
    jats = "<jats:p>Hello <jats:italic>world</jats:italic>.</jats:p>"
    assert strip_jats(jats) == "Hello world ."


def test_strip_jats_none_and_markup_only() -> None:
    assert strip_jats(None) is None
    assert strip_jats("<jats:p></jats:p>") is None


# ---------------------------------------------------------------------------
# Per-source clients
# ---------------------------------------------------------------------------


def test_semantic_scholar_returns_abstract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "semanticscholar.org" in request.url.host
        return httpx.Response(200, json={"abstract": "An S2 abstract."})

    with _client(handler) as client:
        result = fetch_semantic_scholar(
            "10.1/x", client=client, email="a@b.c"
        )
    assert result == "An S2 abstract."


def test_semantic_scholar_404_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with _client(handler) as client:
        assert fetch_semantic_scholar("10.1/x", client=client, email="a@b.c") is None


def test_semantic_scholar_null_abstract_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"abstract": None})

    with _client(handler) as client:
        assert fetch_semantic_scholar("10.1/x", client=client, email="a@b.c") is None


def test_semantic_scholar_api_key_sent_as_header() -> None:
    seen: dict[str, str | None] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-api-key")
        return httpx.Response(200, json={"abstract": "x"})

    with _client(handler) as client:
        fetch_semantic_scholar(
            "10.1/x", client=client, email="a@b.c", api_key="secret"
        )
    assert seen["key"] == "secret"


def test_openalex_reconstructs_inverted_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "openalex.org" in request.url.host
        return httpx.Response(
            200,
            json={"abstract_inverted_index": {"hello": [0], "world": [1]}},
        )

    with _client(handler) as client:
        result = fetch_openalex("10.1/x", client=client, email="a@b.c")
    assert result == "hello world"


def test_openalex_missing_index_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"abstract_inverted_index": None})

    with _client(handler) as client:
        assert fetch_openalex("10.1/x", client=client, email="a@b.c") is None


def test_crossref_strips_jats() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "crossref.org" in request.url.host
        return httpx.Response(
            200,
            json={"message": {"abstract": "<jats:p>Cross abstract.</jats:p>"}},
        )

    with _client(handler) as client:
        result = fetch_crossref("10.1/x", client=client, email="a@b.c")
    assert result == "Cross abstract."


def test_crossref_missing_abstract_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {}})

    with _client(handler) as client:
        assert fetch_crossref("10.1/x", client=client, email="a@b.c") is None


# ---------------------------------------------------------------------------
# Cascade
# ---------------------------------------------------------------------------


def test_cascade_stops_at_first_source() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        calls.append(host)
        if "semanticscholar.org" in host:
            return httpx.Response(200, json={"abstract": "S2 wins."})
        raise AssertionError(f"unexpected fall-through to {host}")

    with _client(handler) as client:
        result = lookup_abstract("10.1/x", client=client, email="a@b.c")
    assert result == ("S2 wins.", "semantic_scholar")
    assert all("semanticscholar.org" in h for h in calls)


def test_cascade_falls_through_to_crossref() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if "semanticscholar.org" in host:
            return httpx.Response(404)
        if "openalex.org" in host:
            return httpx.Response(200, json={"abstract_inverted_index": None})
        if "crossref.org" in host:
            return httpx.Response(
                200,
                json={"message": {"abstract": "<jats:p>Crossref text.</jats:p>"}},
            )
        raise AssertionError(host)

    with _client(handler) as client:
        result = lookup_abstract("10.1/x", client=client, email="a@b.c")
    assert result == ("Crossref text.", "crossref")


def test_cascade_none_when_no_source_has_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with _client(handler) as client:
        assert lookup_abstract("10.1/x", client=client, email="a@b.c") is None


# ---------------------------------------------------------------------------
# End-to-end run()
# ---------------------------------------------------------------------------


def _run_args(tmp_path: Path, **overrides: object) -> MagicMock:
    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.output_dir = tmp_path / "out"
    args.email = "a@b.c"
    args.api_key = None
    args.skip_existing = False
    for key, value in overrides.items():
        setattr(args, key, value)
    return args


def _patch_client(
    monkeypatch: pytest.MonkeyPatch, handler: object
) -> None:
    """Make lookup.run build an httpx.Client backed by ``handler``."""
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    real_client = httpx.Client  # capture before patching to avoid recursion

    def _factory(*_args: object, **_kwargs: object) -> httpx.Client:
        return real_client(transport=transport)

    monkeypatch.setattr(lookup_mod.httpx, "Client", _factory)


def test_end_to_end_fills_missing_abstract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    works = [
        _make_work(
            "https://openalex.org/W1",
            abstract=None,
            doi="https://doi.org/10.1/a",
        ),
        _make_work(
            "https://openalex.org/W2",
            abstract="Already here.",
            doi="https://doi.org/10.1/b",
        ),
        _make_work("https://openalex.org/W3", abstract=None, doi=None),
    ]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    def handler(request: httpx.Request) -> httpx.Response:
        if "semanticscholar.org" in request.url.host:
            return httpx.Response(200, json={"abstract": "Looked up."})
        raise AssertionError(request.url.host)

    _patch_client(monkeypatch, handler)
    run(_run_args(tmp_path))

    records = {
        r.work_id: r
        for r in (
            AbstractRecord.model_validate_json(line)
            for line in (tmp_path / "out" / "abstracts.jsonl")
            .read_text()
            .strip()
            .splitlines()
        )
    }
    assert records["https://openalex.org/W1"].abstract == "Looked up."
    assert records["https://openalex.org/W1"].source == "semantic_scholar"
    assert records["https://openalex.org/W2"].abstract == "Already here."
    assert records["https://openalex.org/W2"].source is None
    assert records["https://openalex.org/W3"].abstract is None
    assert records["https://openalex.org/W3"].source is None

    meta = AbstractLookupMeta.model_validate(
        json.loads((tmp_path / "out" / "abstract-lookup-meta.json").read_text())
    )
    assert meta.input_count == 3
    assert meta.filled_count == 1
    assert meta.no_doi_count == 1
    assert meta.still_missing_count == 1
    assert meta.by_source == {"semantic_scholar": 1}
    assert meta.run.tool == "laglitsynth.abstract_lookup.lookup"


def test_end_to_end_none_preserved_when_no_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    works = [
        _make_work(
            "https://openalex.org/W1",
            abstract=None,
            doi="https://doi.org/10.1/a",
        )
    ]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    _patch_client(monkeypatch, handler)
    run(_run_args(tmp_path))

    record = AbstractRecord.model_validate_json(
        (tmp_path / "out" / "abstracts.jsonl").read_text().strip()
    )
    assert record.abstract is None
    assert record.source is None

    meta = AbstractLookupMeta.model_validate(
        json.loads((tmp_path / "out" / "abstract-lookup-meta.json").read_text())
    )
    assert meta.filled_count == 0
    assert meta.still_missing_count == 1
    assert meta.no_doi_count == 0


def test_skip_existing_processes_only_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    works = [
        _make_work(
            "https://openalex.org/W1",
            abstract=None,
            doi="https://doi.org/10.1/a",
        ),
        _make_work(
            "https://openalex.org/W2",
            abstract=None,
            doi="https://doi.org/10.1/b",
        ),
    ]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    # W1 already resolved in a prior run; W2 was still missing.
    with open(out_dir / "abstracts.jsonl", "w") as f:
        f.write(
            AbstractRecord(
                work_id="https://openalex.org/W1",
                abstract="Prior abstract.",
                source="openalex",
            ).model_dump_json()
            + "\n"
        )
        f.write(
            AbstractRecord(work_id="https://openalex.org/W2").model_dump_json()
            + "\n"
        )

    looked_up: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        looked_up.append(str(request.url))
        if "semanticscholar.org" in request.url.host:
            return httpx.Response(200, json={"abstract": "W2 abstract."})
        raise AssertionError(request.url.host)

    _patch_client(monkeypatch, handler)
    run(_run_args(tmp_path, skip_existing=True))

    records = {
        r.work_id: r
        for r in (
            AbstractRecord.model_validate_json(line)
            for line in (out_dir / "abstracts.jsonl")
            .read_text()
            .strip()
            .splitlines()
        )
    }
    # W1 carried forward untouched; only W2 was looked up.
    assert records["https://openalex.org/W1"].abstract == "Prior abstract."
    assert records["https://openalex.org/W1"].source == "openalex"
    assert records["https://openalex.org/W2"].abstract == "W2 abstract."
    assert all("10.1/a" not in url for url in looked_up)

    meta = AbstractLookupMeta.model_validate(
        json.loads((out_dir / "abstract-lookup-meta.json").read_text())
    )
    assert meta.filled_count == 2  # carried-forward W1 + freshly filled W2
    assert meta.still_missing_count == 0


def test_email_dotenv_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ABSTRACT_LOOKUP_EMAIL=env@b.c\n")
    _write_works_jsonl(
        tmp_path / "input.jsonl",
        [_make_work("https://openalex.org/W1", abstract=None, doi=None)],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no DOI -> no request expected")

    _patch_client(monkeypatch, handler)
    args = _run_args(tmp_path, email=None)
    args.input = tmp_path / "input.jsonl"
    args.output_dir = tmp_path / "out"
    run(args)

    meta = AbstractLookupMeta.model_validate(
        json.loads((tmp_path / "out" / "abstract-lookup-meta.json").read_text())
    )
    assert meta.no_doi_count == 1


def test_missing_email_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    _write_works_jsonl(
        tmp_path / "input.jsonl",
        [_make_work("https://openalex.org/W1", abstract=None, doi=None)],
    )
    args = _run_args(tmp_path, email=None)
    args.input = tmp_path / "input.jsonl"
    args.output_dir = tmp_path / "out"
    with pytest.raises(SystemExit) as exc_info:
        run(args)
    assert "ABSTRACT_LOOKUP_EMAIL" in str(exc_info.value)
