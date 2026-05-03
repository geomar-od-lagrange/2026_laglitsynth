"""Tests for the catalogue_dedup three-rule implementation."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from laglitsynth.catalogue_dedup.dedup import deduplicate, run
from laglitsynth.catalogue_dedup.models import DeduplicationMeta

from conftest import _make_authorship, _make_work, _write_works_jsonl


# ---------------------------------------------------------------------------
# Unit tests for deduplicate()
# ---------------------------------------------------------------------------


def test_dedup_by_openalex_id() -> None:
    """Two works with the same OpenAlex ID → only one survives."""
    w1 = _make_work("https://openalex.org/W1", title="Paper One")
    w2 = _make_work("https://openalex.org/W1", title="Paper One (duplicate)")
    survivors, dropped = deduplicate([w1, w2])
    assert len(survivors) == 1
    assert len(dropped) == 1
    assert dropped[0].rule == "openalex_id"
    assert survivors[0].id == "https://openalex.org/W1"


def test_dedup_by_doi_normalisation() -> None:
    """Works with DOIs that differ only in prefix/casing are duplicates."""
    w1 = _make_work("https://openalex.org/W1", doi="https://doi.org/10.1234/test")
    w2 = _make_work("https://openalex.org/W2", doi="http://doi.org/10.1234/test")
    w3 = _make_work("https://openalex.org/W3", doi="https://dx.doi.org/10.1234/TEST")
    survivors, dropped = deduplicate([w1, w2, w3])
    assert len(survivors) == 1
    assert len(dropped) == 2
    assert all(d.rule == "doi" for d in dropped)


def test_dedup_by_title_author_year() -> None:
    """Works without DOI matching on normalised title + first author + year."""
    auth = [_make_authorship("Jane Smith")]
    w1 = _make_work(
        "https://openalex.org/W1",
        title="Ocean Dynamics: A Review",
        publication_year=2020,
        authorships=auth,
    )
    w2 = _make_work(
        "https://openalex.org/W2",
        title="ocean dynamics  a review",  # different whitespace/casing, no punctuation change
        publication_year=2020,
        authorships=[_make_authorship("Jane Smith")],
    )
    survivors, dropped = deduplicate([w1, w2])
    assert len(survivors) == 1
    assert len(dropped) == 1
    assert dropped[0].rule == "title_author_year"


def test_dedup_keeps_most_complete() -> None:
    """When a duplicate is found, the record with more metadata survives."""
    # w1 has no DOI, no authors; w2 has a DOI and one author — w2 should win.
    w1 = _make_work("https://openalex.org/W1", doi=None)
    w2 = _make_work(
        "https://openalex.org/W2",
        doi="https://doi.org/10.1234/abc",
        title="Test Paper",
        publication_year=2021,
        authorships=[_make_authorship("Alice")],
    )
    # Force a rule-1 duplicate by using same OpenAlex ID.
    w2_dup = _make_work(
        "https://openalex.org/W1",  # same OA ID as w1
        doi="https://doi.org/10.1234/abc",
        authorships=[_make_authorship("Alice"), _make_authorship("Bob")],
    )
    survivors, dropped = deduplicate([w1, w2_dup])
    # w2_dup has more authorships and a DOI; it should replace w1.
    assert len(survivors) == 1
    survived = survivors[0]
    assert survived.doi == "https://doi.org/10.1234/abc"
    assert len(survived.authorships) == 2
    assert dropped[0].dropped_work_id == "https://openalex.org/W1"
    assert dropped[0].survived_work_id == "https://openalex.org/W1"


def test_dedup_keeps_most_complete_doi_beats_no_doi() -> None:
    """With same OA ID, the record with a DOI beats the one without."""
    w_no_doi = _make_work("https://openalex.org/W1", doi=None)
    w_with_doi = _make_work(
        "https://openalex.org/W1",
        doi="https://doi.org/10.9999/x",
        authorships=[],
    )
    survivors, dropped = deduplicate([w_no_doi, w_with_doi])
    assert len(survivors) == 1
    assert survivors[0].doi is not None
    assert len(dropped) == 1
    assert dropped[0].survived_work_id == "https://openalex.org/W1"


def test_dropped_jsonl_records_rule(tmp_path: Path) -> None:
    """dropped.jsonl contains survived_work_id and the matching rule."""
    w1 = _make_work("https://openalex.org/W1", doi="https://doi.org/10.0/X")
    w2 = _make_work("https://openalex.org/W2", doi="https://doi.org/10.0/x")

    _write_works_jsonl(tmp_path / "input.jsonl", [w1, w2])

    args = MagicMock()
    args.input = [str(tmp_path / "input.jsonl")]
    args.output_dir = tmp_path / "out"
    run(args)

    dropped_lines = (tmp_path / "out" / "dropped.jsonl").read_text().strip().splitlines()
    assert len(dropped_lines) == 1
    rec = json.loads(dropped_lines[0])
    assert rec["rule"] == "doi"
    assert "survived_work_id" in rec
    assert "dropped_work_id" in rec


def test_multi_input_glob(tmp_path: Path) -> None:
    """Multiple input files via glob pattern are merged before dedup."""
    works_a = [_make_work("https://openalex.org/W1"), _make_work("https://openalex.org/W2")]
    works_b = [_make_work("https://openalex.org/W3"), _make_work("https://openalex.org/W4")]

    _write_works_jsonl(tmp_path / "fetch_a.jsonl", works_a)
    _write_works_jsonl(tmp_path / "fetch_b.jsonl", works_b)

    args = MagicMock()
    args.input = [str(tmp_path / "fetch_*.jsonl")]
    args.output_dir = tmp_path / "out"
    run(args)

    dedup_lines = (tmp_path / "out" / "deduplicated.jsonl").read_text().strip().splitlines()
    assert len(dedup_lines) == 4

    meta_data = json.loads((tmp_path / "out" / "dedup-meta.json").read_text())
    meta = DeduplicationMeta.model_validate(meta_data)
    assert meta.input_count == 4
    assert meta.output_count == 4
    assert meta.duplicates_removed == 0


def test_multi_input_glob_deduplicates_across_files(tmp_path: Path) -> None:
    """A DOI duplicate spanning two input files is removed."""
    w1 = _make_work("https://openalex.org/W1", doi="https://doi.org/10.1/dup")
    w2 = _make_work("https://openalex.org/W2", doi="http://doi.org/10.1/dup")

    _write_works_jsonl(tmp_path / "file_a.jsonl", [w1])
    _write_works_jsonl(tmp_path / "file_b.jsonl", [w2])

    args = MagicMock()
    args.input = [str(tmp_path / "file_a.jsonl"), str(tmp_path / "file_b.jsonl")]
    args.output_dir = tmp_path / "out"
    run(args)

    dedup_lines = (tmp_path / "out" / "deduplicated.jsonl").read_text().strip().splitlines()
    assert len(dedup_lines) == 1


def test_works_without_doi_use_title_author_year(tmp_path: Path) -> None:
    """Works with no DOI fall through to rule 3 for dedup."""
    auth = [_make_authorship("Bob Jones")]
    w1 = _make_work(
        "https://openalex.org/W1",
        doi=None,
        title="Climate Change Effects",
        publication_year=2019,
        authorships=auth,
    )
    w2 = _make_work(
        "https://openalex.org/W2",
        doi=None,
        title="climate change effects",
        publication_year=2019,
        authorships=[_make_authorship("Bob Jones")],
    )
    _write_works_jsonl(tmp_path / "input.jsonl", [w1, w2])

    args = MagicMock()
    args.input = [str(tmp_path / "input.jsonl")]
    args.output_dir = tmp_path / "out"
    run(args)

    dedup_lines = (tmp_path / "out" / "deduplicated.jsonl").read_text().strip().splitlines()
    assert len(dedup_lines) == 1

    dropped_lines = (tmp_path / "out" / "dropped.jsonl").read_text().strip().splitlines()
    assert len(dropped_lines) == 1
    rec = json.loads(dropped_lines[0])
    assert rec["rule"] == "title_author_year"


def test_no_duplicates_passes_through_unchanged(tmp_path: Path) -> None:
    """A set of distinct works is passed through unmodified."""
    auth_a = [_make_authorship("Alice")]
    auth_b = [_make_authorship("Bob")]
    works = [
        _make_work("https://openalex.org/W1", doi="https://doi.org/10.1/a",
                   title="Paper A", publication_year=2020, authorships=auth_a),
        _make_work("https://openalex.org/W2", doi="https://doi.org/10.1/b",
                   title="Paper B", publication_year=2020, authorships=auth_b),
        _make_work("https://openalex.org/W3", doi=None,
                   title="Paper C", publication_year=2021, authorships=auth_a),
    ]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    args = MagicMock()
    args.input = [str(tmp_path / "input.jsonl")]
    args.output_dir = tmp_path / "out"
    run(args)

    dedup_lines = (tmp_path / "out" / "deduplicated.jsonl").read_text().strip().splitlines()
    assert len(dedup_lines) == 3

    dropped_text = (tmp_path / "out" / "dropped.jsonl").read_text().strip()
    assert dropped_text == ""

    meta_data = json.loads((tmp_path / "out" / "dedup-meta.json").read_text())
    meta = DeduplicationMeta.model_validate(meta_data)
    assert meta.input_count == 3
    assert meta.output_count == 3
    assert meta.duplicates_removed == 0
    assert meta.by_rule == {}


# ---------------------------------------------------------------------------
# Rule 3 edge cases
# ---------------------------------------------------------------------------


def test_rule3_skipped_for_missing_author() -> None:
    """Works without authorships do not trigger rule 3."""
    w1 = _make_work(
        "https://openalex.org/W1",
        doi=None,
        title="Lonely Paper",
        publication_year=2022,
        authorships=[],
    )
    w2 = _make_work(
        "https://openalex.org/W2",
        doi=None,
        title="Lonely Paper",
        publication_year=2022,
        authorships=[],
    )
    survivors, dropped = deduplicate([w1, w2])
    # Rule 3 skipped for both — both survive as distinct records.
    assert len(survivors) == 2
    assert len(dropped) == 0


def test_rule3_skipped_for_missing_year() -> None:
    """Works without publication_year do not trigger rule 3."""
    auth = [_make_authorship("Carol")]
    w1 = _make_work(
        "https://openalex.org/W1",
        doi=None,
        title="Undated Paper",
        publication_year=None,
        authorships=auth,
    )
    w2 = _make_work(
        "https://openalex.org/W2",
        doi=None,
        title="Undated Paper",
        publication_year=None,
        authorships=[_make_authorship("Carol")],
    )
    survivors, dropped = deduplicate([w1, w2])
    assert len(survivors) == 2
    assert len(dropped) == 0


# ---------------------------------------------------------------------------
# Meta correctness (integration via run())
# ---------------------------------------------------------------------------


def test_meta_correctness(tmp_path: Path) -> None:
    w1 = _make_work("https://openalex.org/W1", doi="https://doi.org/10.0/A")
    w2 = _make_work("https://openalex.org/W2", doi="http://doi.org/10.0/a")  # same DOI
    w3 = _make_work("https://openalex.org/W3", doi="https://doi.org/10.0/B")

    _write_works_jsonl(tmp_path / "input.jsonl", [w1, w2, w3])

    args = MagicMock()
    args.input = [str(tmp_path / "input.jsonl")]
    args.output_dir = tmp_path / "out"
    run(args)

    meta_data = json.loads((tmp_path / "out" / "dedup-meta.json").read_text())
    meta = DeduplicationMeta.model_validate(meta_data)
    assert meta.input_count == 3
    assert meta.output_count == 2
    assert meta.duplicates_removed == 1
    assert meta.by_rule == {"doi": 1}
    assert meta.run.tool == "laglitsynth.catalogue_dedup.dedup"


def test_validation_skipped_counted(tmp_path: Path) -> None:
    """One valid Work + one invalid JSONL line: meta.run.validation_skipped == 1."""
    work = _make_work("https://openalex.org/W1")
    with open(tmp_path / "input.jsonl", "w") as f:
        f.write(work.model_dump_json() + "\n")
        f.write('{"id": "not-a-work", "broken": true}\n')  # missing required fields

    args = MagicMock()
    args.input = [str(tmp_path / "input.jsonl")]
    args.output_dir = tmp_path / "out"
    run(args)

    meta_data = json.loads((tmp_path / "out" / "dedup-meta.json").read_text())
    meta = DeduplicationMeta.model_validate(meta_data)
    assert meta.run.validation_skipped == 1
    assert meta.input_count == 1


def test_empty_input(tmp_path: Path) -> None:
    _write_works_jsonl(tmp_path / "input.jsonl", [])

    args = MagicMock()
    args.input = [str(tmp_path / "input.jsonl")]
    args.output_dir = tmp_path / "out"
    run(args)

    dedup_lines = (tmp_path / "out" / "deduplicated.jsonl").read_text().strip()
    assert dedup_lines == ""

    meta_data = json.loads((tmp_path / "out" / "dedup-meta.json").read_text())
    assert meta_data["input_count"] == 0
    assert meta_data["output_count"] == 0
