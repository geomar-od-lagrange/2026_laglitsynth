"""Tests for the catalogue_dedup submodule."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from laglitsynth.catalogue_dedup.dedup import run
from laglitsynth.catalogue_dedup.models import DeduplicationMeta
from laglitsynth.catalogue_fetch.models import Work


def _make_work(work_id: str = "https://openalex.org/W1") -> Work:
    return Work(
        id=work_id,
        title="Test Paper",
        abstract="An abstract.",
        authorships=[],
        biblio={},
        cited_by_count=0,
        referenced_works=[],
        keywords=[],
        topics=[],
    )


def _write_works_jsonl(path: Path, works: list[Work]) -> None:
    with open(path, "w") as f:
        for w in works:
            f.write(w.model_dump_json() + "\n")


def test_pass_all_writes_all_works(tmp_path: Path) -> None:
    works = [_make_work("https://openalex.org/W1"), _make_work("https://openalex.org/W2")]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.output_dir = tmp_path / "out"
    run(args)

    dedup_lines = (tmp_path / "out" / "deduplicated.jsonl").read_text().strip().splitlines()
    assert len(dedup_lines) == 2
    assert "W1" in dedup_lines[0]
    assert "W2" in dedup_lines[1]


def test_dropped_is_empty(tmp_path: Path) -> None:
    works = [_make_work()]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.output_dir = tmp_path / "out"
    run(args)

    dropped = (tmp_path / "out" / "dropped.jsonl").read_text().strip()
    assert dropped == ""


def test_meta_correctness(tmp_path: Path) -> None:
    works = [_make_work("https://openalex.org/W1"), _make_work("https://openalex.org/W2")]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.output_dir = tmp_path / "out"
    run(args)

    meta_data = json.loads((tmp_path / "out" / "dedup-meta.json").read_text())
    meta = DeduplicationMeta.model_validate(meta_data)
    assert meta.input_count == 2
    assert meta.output_count == 2
    assert meta.duplicates_removed == 0
    assert meta.by_rule == {}
    assert meta.run.tool == "laglitsynth.catalogue_dedup.dedup"
    assert meta.run.validation_skipped == 0


def test_validation_skipped_counted(tmp_path: Path) -> None:
    """One valid Work + one invalid JSONL line: meta.run.validation_skipped == 1."""
    work = _make_work("https://openalex.org/W1")
    with open(tmp_path / "input.jsonl", "w") as f:
        f.write(work.model_dump_json() + "\n")
        f.write('{"id": "not-a-work", "broken": true}\n')  # missing required fields

    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.output_dir = tmp_path / "out"
    run(args)

    meta_data = json.loads((tmp_path / "out" / "dedup-meta.json").read_text())
    meta = DeduplicationMeta.model_validate(meta_data)
    assert meta.run.validation_skipped == 1
    assert meta.input_count == 1  # only valid work counted


def test_empty_input(tmp_path: Path) -> None:
    _write_works_jsonl(tmp_path / "input.jsonl", [])

    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.output_dir = tmp_path / "out"
    run(args)

    dedup_lines = (tmp_path / "out" / "deduplicated.jsonl").read_text().strip()
    assert dedup_lines == ""

    meta_data = json.loads((tmp_path / "out" / "dedup-meta.json").read_text())
    assert meta_data["input_count"] == 0
    assert meta_data["output_count"] == 0
