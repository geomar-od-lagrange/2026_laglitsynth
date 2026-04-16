"""Tests for the screening_adjudication submodule."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from laglitsynth.screening_adjudication.models import AdjudicationMeta
from laglitsynth.screening_adjudication.adjudicate import run
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


def test_pass_through_copies_all_works(tmp_path: Path) -> None:
    works = [_make_work("https://openalex.org/W1"), _make_work("https://openalex.org/W2")]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.output_dir = tmp_path / "out"
    run(args)

    lines = (tmp_path / "out" / "included.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert "W1" in lines[0]
    assert "W2" in lines[1]


def test_meta_correctness(tmp_path: Path) -> None:
    works = [_make_work("https://openalex.org/W1"), _make_work("https://openalex.org/W2")]
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.output_dir = tmp_path / "out"
    run(args)

    meta_data = json.loads((tmp_path / "out" / "adjudication-meta.json").read_text())
    meta = AdjudicationMeta.model_validate(meta_data)
    assert meta.input_count == 2
    assert meta.output_count == 2
    assert meta.mode == "pass_through"
    assert meta.human_reviewed == 0
    assert meta.tool == "laglitsynth.screening_adjudication.adjudicate"


def test_empty_input(tmp_path: Path) -> None:
    _write_works_jsonl(tmp_path / "input.jsonl", [])

    args = MagicMock()
    args.input = tmp_path / "input.jsonl"
    args.output_dir = tmp_path / "out"
    run(args)

    lines = (tmp_path / "out" / "included.jsonl").read_text().strip()
    assert lines == ""

    meta_data = json.loads((tmp_path / "out" / "adjudication-meta.json").read_text())
    assert meta_data["input_count"] == 0
    assert meta_data["output_count"] == 0
