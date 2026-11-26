"""Tests for ``corpus-assemble`` and the corpus readers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.cli import main
from laglitsynth.corpus.load import (
    load_corpus_meta,
    read_corpus_pdfs,
    read_corpus_works,
    require_corpus,
)
from laglitsynth.fulltext_retrieval.models import PdfProvenanceRecord, PdfSource
from laglitsynth.fulltext_retrieval.store import (
    sha256_of,
    store_pdf_path,
    write_provenance,
)
from laglitsynth.io import write_jsonl, write_meta
from laglitsynth.layout import corpus_dir, deduplicated_path, screening_verdicts_path
from laglitsynth.models import LlmMeta, RunMeta
from laglitsynth.screening_abstracts.models import ScreeningMeta, ScreeningVerdict

from conftest import TEST_RUN_ID, _make_work, _write_manifest

THRESHOLD = "50"


def _wid(n: int) -> str:
    return f"https://openalex.org/W{n}"


def _screening_meta() -> ScreeningMeta:
    return ScreeningMeta(
        run=RunMeta(tool="laglitsynth.screening_abstracts.screen", run_at="2026-01-01T00:00:00+00:00", validation_skipped=0),
        llm=LlmMeta(
            model="m",
            base_url="https://llm.example/v1",
            decisions_url="https://llm.example/v1/decisions",
            prompt_sha256="0" * 64,
        ),
        input_path="catalogue.jsonl",
        input_count=0,
        scored_count=0,
        skipped_count=0,
    )


def _setup(
    data_dir: Path,
    scores: dict[int, int | None],
    *,
    extra_works: tuple[int, ...] = (),
    pdfs: dict[int, bytes] | None = None,
    missing: tuple[int, ...] = (),
    catalogue_order: list[int] | None = None,
) -> None:
    """Write manifest, catalogue, screening run and store under ``data_dir``.

    ``scores`` maps work number to its verdict score; ``extra_works`` are
    catalogue works without a verdict row. ``pdfs`` are store PDFs (with
    an ``oa`` record), ``missing`` works get a ``missing`` record.
    """
    _write_manifest(data_dir)
    numbers = catalogue_order or [*scores, *extra_works]
    deduplicated_path(data_dir).parent.mkdir(parents=True, exist_ok=True)
    write_jsonl([_make_work(_wid(n)) for n in numbers], deduplicated_path(data_dir))

    verdicts = screening_verdicts_path(data_dir, TEST_RUN_ID)
    write_jsonl(
        [
            ScreeningVerdict(
                work_id=_wid(n),
                relevance_score=s,
                reason=None if s is not None else "no-abstract",
            )
            for n, s in scores.items()
        ],
        verdicts,
    )
    write_meta(verdicts.parent / "screening-meta.json", _screening_meta())

    records: dict[str, PdfProvenanceRecord] = {}
    for n, content in (pdfs or {}).items():
        path = store_pdf_path(data_dir, f"W{n}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        records[_wid(n)] = PdfProvenanceRecord(
            work_id=_wid(n),
            stem=f"W{n}",
            doi=None,
            source=PdfSource.oa,
            source_url="https://example.org/p.pdf",
            pdf_path=f"pdfs/W{n}.pdf",
            content_sha256=sha256_of(path),
            obtained_at="2026-01-01T00:00:00+00:00",
        )
    for n in missing:
        records[_wid(n)] = PdfProvenanceRecord(
            work_id=_wid(n),
            stem=f"W{n}",
            doi=None,
            source=PdfSource.missing,
            source_url=None,
            pdf_path=None,
            content_sha256=None,
            obtained_at="2026-01-01T00:00:00+00:00",
        )
    if records:
        write_provenance(data_dir, records)


def _assemble(data_dir: Path, *extra: str) -> None:
    main(
        [
            "corpus-assemble",
            "--data-dir",
            str(data_dir),
            "--screening-threshold",
            THRESHOLD,
            *extra,
        ]
    )


def _ids(works: list[Work]) -> list[str]:
    return [w.id for w in works]


def test_selection(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80, 2: 20, 3: None, 4: 50})
    _assemble(data)
    out = corpus_dir(data)
    assert _ids(read_corpus_works(out)) == [_wid(1), _wid(3), _wid(4)]
    verdict_ids = [
        json.loads(line)["work_id"]
        for line in (out / "verdicts.jsonl").read_text().splitlines()
    ]
    assert verdict_ids == [_wid(1), _wid(3), _wid(4)]
    meta = load_corpus_meta(out)
    assert meta.catalogue_count == 4
    assert meta.selected_count == 3
    assert meta.unscored_count == 1
    assert meta.below_threshold_count == 1
    assert meta.no_verdict_count == 0
    assert meta.screening_threshold == 50.0
    assert meta.screening_run_id == TEST_RUN_ID
    assert meta.screening_llm.model == "m"


def test_screening_threshold_required(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80})
    with pytest.raises(SystemExit, match="--screening-threshold"):
        main(["corpus-assemble", "--data-dir", str(data)])


def test_missing_verdict_exits(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80}, extra_works=(2,))
    with pytest.raises(SystemExit, match="--allow-unscreened"):
        _assemble(data)
    assert not corpus_dir(data).exists()
    assert not (data / "corpus.tmp").exists()


def test_allow_unscreened_includes(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80, 3: 10}, extra_works=(2,))
    _assemble(data, "--allow-unscreened")
    out = corpus_dir(data)
    assert _ids(read_corpus_works(out)) == [_wid(1), _wid(2)]
    meta = load_corpus_meta(out)
    assert meta.no_verdict_count == 1
    assert meta.selected_count == 2
    assert (out / "verdicts.jsonl").read_text().count("\n") == 1


def test_invalid_verdict_row_exits(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80})
    verdicts = screening_verdicts_path(data, TEST_RUN_ID)
    with open(verdicts, "a") as f:
        f.write(json.dumps({"work_id": _wid(2), "relevance_score": 70, "seed": 1}) + "\n")
    with pytest.raises(SystemExit, match="fail validation"):
        _assemble(data)
    assert not corpus_dir(data).exists()


def test_invalid_screening_meta_exits(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80})
    meta_path = screening_verdicts_path(data, TEST_RUN_ID).parent / "screening-meta.json"
    raw = json.loads(meta_path.read_text())
    raw["threshold"] = 50.0
    meta_path.write_text(json.dumps(raw))
    with pytest.raises(SystemExit, match="ScreeningMeta"):
        _assemble(data)
    assert not corpus_dir(data).exists()

    meta_path.unlink()
    with pytest.raises(SystemExit, match="screening-meta.json not found"):
        _assemble(data)
    assert not corpus_dir(data).exists()


def test_pdf_copies_match_store(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80, 2: 90}, pdfs={1: b"%PDF-1.4 one", 2: b"%PDF-1.4 two"})
    _assemble(data)
    out = corpus_dir(data)
    pdfs = read_corpus_pdfs(out)
    assert [p.work_id for p in pdfs] == [_wid(1), _wid(2)]
    for p in pdfs:
        assert p.pdf_path is not None
        copy = out / p.pdf_path
        store = store_pdf_path(data, p.work_id.rsplit("/", 1)[-1])
        assert copy.read_bytes() == store.read_bytes()
        assert p.content_sha256 == sha256_of(copy)
        assert p.store_record is not None
        assert p.store_record.content_sha256 == p.content_sha256
    assert load_corpus_meta(out).with_pdf_count == 2


def test_work_without_pdf_has_null_path(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80, 2: 80, 3: 80}, pdfs={1: b"%PDF-1.4 one"}, missing=(2,))
    _assemble(data)
    out = corpus_dir(data)
    by_id = {p.work_id: p for p in read_corpus_pdfs(out)}
    assert by_id[_wid(1)].pdf_path == "pdfs/W1.pdf"
    for n in (2, 3):
        assert by_id[_wid(n)].pdf_path is None
        assert by_id[_wid(n)].content_sha256 is None
    record = by_id[_wid(2)].store_record
    assert record is not None and record.source == PdfSource.missing
    assert by_id[_wid(3)].store_record is None
    assert sorted(p.name for p in (out / "pdfs").iterdir()) == ["W1.pdf"]
    assert load_corpus_meta(out).with_pdf_count == 1


def test_absent_store_file_exits(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80}, pdfs={1: b"%PDF-1.4 one"})
    store_pdf_path(data, "W1").unlink()
    with pytest.raises(SystemExit, match="W1.pdf is absent"):
        _assemble(data)
    assert not corpus_dir(data).exists()
    assert not (data / "corpus.tmp").exists()


def test_lfs_pointer_exits(tmp_path: Path) -> None:
    data = tmp_path / "data"
    pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 1\n"
    _setup(data, {1: 80}, pdfs={1: pointer})
    with pytest.raises(SystemExit, match="git lfs pull"):
        _assemble(data)
    assert not corpus_dir(data).exists()


def test_reassembly_removes_stale_files(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80, 2: 80}, pdfs={1: b"%PDF-1.4 one", 2: b"%PDF-1.4 two"})
    _assemble(data)
    out = corpus_dir(data)
    (out / "stray.txt").write_text("left over")
    assert (out / "pdfs" / "W2.pdf").exists()

    _setup(data, {1: 80, 2: 10}, pdfs={1: b"%PDF-1.4 one", 2: b"%PDF-1.4 two"})
    _assemble(data)
    assert not (out / "stray.txt").exists()
    assert not (out / "pdfs" / "W2.pdf").exists()
    assert _ids(read_corpus_works(out)) == [_wid(1)]


def test_leftover_tmp_removed(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80})
    leftover = data / "corpus.tmp"
    (leftover / "pdfs").mkdir(parents=True)
    (leftover / "pdfs" / "W9.pdf").write_bytes(b"%PDF-1.4 stale")
    _assemble(data)
    assert not leftover.exists()
    assert not (corpus_dir(data) / "pdfs" / "W9.pdf").exists()


def test_corpus_id_stable_across_catalogue_order(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    pdfs = {1: b"%PDF-1.4 one", 2: b"%PDF-1.4 two"}
    _setup(a, {1: 80, 2: 80, 3: None}, pdfs=pdfs, catalogue_order=[1, 2, 3])
    _setup(b, {1: 80, 2: 80, 3: None}, pdfs=pdfs, catalogue_order=[3, 2, 1])
    _assemble(a)
    main(["corpus-assemble", "--data-dir", str(b), "--screening-threshold", "60"])
    assert load_corpus_meta(corpus_dir(a)).corpus_id == load_corpus_meta(corpus_dir(b)).corpus_id


def test_corpus_id_changes_with_pdf_bytes(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80}, pdfs={1: b"%PDF-1.4 one"})
    _assemble(data)
    first = load_corpus_meta(corpus_dir(data)).corpus_id
    _setup(data, {1: 80}, pdfs={1: b"%PDF-1.4 one, re-fetched"})
    _assemble(data)
    assert load_corpus_meta(corpus_dir(data)).corpus_id != first


def test_analysis_run_id_kept_when_corpus_unchanged(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80, 2: 20}, pdfs={1: b"%PDF-1.4 one"})
    _assemble(data)
    first = load_corpus_meta(corpus_dir(data))
    # A different threshold that selects the same works leaves the id alone.
    main(["corpus-assemble", "--data-dir", str(data), "--screening-threshold", "70"])
    second = load_corpus_meta(corpus_dir(data))
    assert second.corpus_id == first.corpus_id
    assert second.analysis_run_id == first.analysis_run_id


def test_analysis_run_id_fresh_when_corpus_changes(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80, 2: 20}, pdfs={1: b"%PDF-1.4 one"})
    _assemble(data)
    first = load_corpus_meta(corpus_dir(data))
    main(["corpus-assemble", "--data-dir", str(data), "--screening-threshold", "10"])
    second = load_corpus_meta(corpus_dir(data))
    assert second.corpus_id != first.corpus_id
    assert second.analysis_run_id != first.analysis_run_id


def test_explicit_verdicts_record_no_run_id(tmp_path: Path) -> None:
    data = tmp_path / "data"
    _setup(data, {1: 80})
    verdicts = screening_verdicts_path(data, TEST_RUN_ID)
    _assemble(data, "--screening-verdicts", str(verdicts))
    meta = load_corpus_meta(corpus_dir(data))
    assert meta.screening_run_id is None
    assert meta.screening_verdicts == str(verdicts)


def test_require_corpus_exits_naming_file_and_stage(tmp_path: Path) -> None:
    source = tmp_path / "extraction-meta.json"
    require_corpus("abc", "abc", source, rerun="fulltext-extraction")
    with pytest.raises(SystemExit, match=r"extraction-meta\.json.*re-run fulltext-extraction"):
        require_corpus("abc", "def", source, rerun="fulltext-extraction")


def test_load_corpus_meta_missing_names_corpus_assemble(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="corpus-assemble"):
        load_corpus_meta(tmp_path / "corpus")
