"""Tests for the catalogue-sample subcommand."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import pytest

from laglitsynth.catalogue_dedup.models import SAMPLE_TOOL_NAME, SampleMeta
from laglitsynth.catalogue_dedup.sample import run, sample_path, sample_works
from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.io import read_jsonl
from laglitsynth.layout import deduplicated_path

from conftest import _make_work, _write_works_jsonl


def _catalogue(count: int) -> list[Work]:
    return [_make_work(f"https://openalex.org/W{i}") for i in range(count)]


def _args(data_dir: Path, **overrides: Any) -> argparse.Namespace:
    base: dict[str, Any] = {
        "input": None,
        "n": 10,
        "seed": 1,
        "output": None,
        "data_dir": data_dir,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


class TestSampleWorks:
    def test_same_seed_gives_same_sample(self) -> None:
        works = _catalogue(50)
        first = [w.id for w in sample_works(works, 10, seed=7)]
        second = [w.id for w in sample_works(works, 10, seed=7)]
        assert first == second
        assert len(first) == 10

    def test_different_seed_differs(self) -> None:
        works = _catalogue(50)
        a = [w.id for w in sample_works(works, 10, seed=1)]
        b = [w.id for w in sample_works(works, 10, seed=2)]
        assert a != b

    def test_output_keeps_catalogue_order(self) -> None:
        works = _catalogue(50)
        position = {w.id: i for i, w in enumerate(works)}
        chosen = sample_works(works, 10, seed=3)
        indices = [position[w.id] for w in chosen]
        assert indices == sorted(indices)
        assert len(set(indices)) == 10

    @pytest.mark.parametrize("n", [5, 6])
    def test_n_at_least_len_returns_everything(self, n: int) -> None:
        works = _catalogue(5)
        assert [w.id for w in sample_works(works, n, seed=1)] == [w.id for w in works]


class TestRun:
    def test_writes_sample_and_meta_at_default_path(self, tmp_path: Path) -> None:
        data_dir = tmp_path / "data"
        works = _catalogue(50)
        catalogue = deduplicated_path(data_dir)
        catalogue.parent.mkdir(parents=True)
        _write_works_jsonl(catalogue, works)

        run(_args(data_dir, n=10, seed=42))

        output = data_dir / "catalogue-dedup" / "sample-10-seed42.jsonl"
        assert sample_path(data_dir, 10, 42) == output
        written = [w.id for w in read_jsonl(output, Work)]
        assert written == [w.id for w in sample_works(works, 10, seed=42)]

        meta_path = data_dir / "catalogue-dedup" / "sample-10-seed42.meta.json"
        meta = SampleMeta.model_validate_json(meta_path.read_text())
        assert meta.run.tool == SAMPLE_TOOL_NAME
        assert meta.input_path == str(catalogue)
        assert meta.input_count == 50
        assert meta.n == 10
        assert meta.seed == 42
        assert meta.output_count == 10

    def test_explicit_input_and_output(self, tmp_path: Path) -> None:
        works = _catalogue(3)
        input_path = tmp_path / "in.jsonl"
        _write_works_jsonl(input_path, works)
        output = tmp_path / "out" / "s.jsonl"

        run(_args(tmp_path / "data", input=input_path, output=output, n=10, seed=0))

        assert [w.id for w in read_jsonl(output, Work)] == [w.id for w in works]
        meta = SampleMeta.model_validate_json(
            (tmp_path / "out" / "s.meta.json").read_text()
        )
        assert meta.input_count == 3
        assert meta.n == 10
        assert meta.output_count == 3

    def test_missing_input_names_catalogue_dedup(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="catalogue-dedup"):
            run(_args(tmp_path / "data"))
