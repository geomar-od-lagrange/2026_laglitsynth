"""Tests for screening_abstracts.export."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
from pydantic import BaseModel

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.screening_abstracts.export import (
    COLUMNS,
    build_row,
    export_review_csv,
)
from laglitsynth.screening_abstracts.models import ScreeningVerdict


def _make_work(
    work_id: str = "https://openalex.org/W1",
    title: str | None = "Test Paper",
    abstract: str | None = "An abstract.",
    doi: str | None = "10.1234/foo",
    publication_year: int | None = 2024,
) -> Work:
    return Work(
        id=work_id,
        title=title,
        abstract=abstract,
        doi=doi,
        publication_year=publication_year,
        authorships=[],
        biblio={},  # type: ignore[arg-type]
        cited_by_count=0,
        referenced_works=[],
        keywords=[],
        topics=[],
    )


def _write_jsonl(path: Path, records: list[BaseModel]) -> None:
    with open(path, "w") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")


def _write_inputs(
    tmp_path: Path, works: list[Work], verdicts: list[ScreeningVerdict]
) -> tuple[Path, Path, Path]:
    catalogue_path = tmp_path / "dedup.jsonl"
    verdicts_path = tmp_path / "verdicts.jsonl"
    output_path = tmp_path / "review.csv"
    _write_jsonl(catalogue_path, list(works))
    _write_jsonl(verdicts_path, list(verdicts))
    return verdicts_path, catalogue_path, output_path


def test_export_round_trip(tmp_path: Path) -> None:
    works = [_make_work("W1"), _make_work("W2", abstract=None)]
    verdicts = [
        ScreeningVerdict(
            work_id="W1",
            relevance_score=80,
            reason="relevant",
            seed=42,
            raw_response='{"relevance_score": 80, "reason": "relevant"}',
        ),
        ScreeningVerdict(
            work_id="W2",
            relevance_score=None,
            reason="no-abstract",
            seed=None,
            raw_response=None,
        ),
    ]
    verdicts_path, catalogue_path, output_path = _write_inputs(tmp_path, works, verdicts)

    count = export_review_csv(verdicts_path, catalogue_path, output_path)

    assert count == 2
    with open(output_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["work_id"] for r in rows] == ["W1", "W2"]
    assert rows[0]["relevance_score"] == "80"
    assert rows[0]["llm_reason"] == "relevant"
    assert rows[0]["reviewer_decision"] == ""
    assert rows[0]["reviewer_reason"] == ""
    assert rows[0]["abstract"] == "An abstract."
    # sentinel — empty relevance_score cell, sentinel reason preserved
    assert rows[1]["relevance_score"] == ""
    assert rows[1]["llm_reason"] == "no-abstract"
    assert rows[1]["abstract"] == ""


def test_export_special_characters_survive(tmp_path: Path) -> None:
    tricky_abstract = 'Line 1, with comma\nLine 2 with "quotes" and, more commas'
    works = [_make_work("W1", abstract=tricky_abstract)]
    verdicts = [
        ScreeningVerdict(
            work_id="W1",
            relevance_score=50,
            reason='Uses "X, Y"',
            seed=1,
        )
    ]
    verdicts_path, catalogue_path, output_path = _write_inputs(tmp_path, works, verdicts)

    export_review_csv(verdicts_path, catalogue_path, output_path)

    with open(output_path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["abstract"] == tricky_abstract
    assert rows[0]["llm_reason"] == 'Uses "X, Y"'


def test_export_raises_on_missing_work(tmp_path: Path) -> None:
    works = [_make_work("W1")]
    verdicts = [
        ScreeningVerdict(work_id="W_missing", relevance_score=None, reason="?"),
    ]
    verdicts_path, catalogue_path, output_path = _write_inputs(tmp_path, works, verdicts)

    with pytest.raises(ValueError, match="W_missing"):
        export_review_csv(verdicts_path, catalogue_path, output_path)


def test_export_column_order(tmp_path: Path) -> None:
    works = [_make_work("W1")]
    verdicts = [ScreeningVerdict(work_id="W1", relevance_score=80, reason="ok", seed=1)]
    verdicts_path, catalogue_path, output_path = _write_inputs(tmp_path, works, verdicts)

    export_review_csv(verdicts_path, catalogue_path, output_path)

    with open(output_path, encoding="utf-8-sig", newline="") as f:
        header = next(csv.reader(f))
    assert tuple(header) == COLUMNS


def test_export_writes_utf8_bom(tmp_path: Path) -> None:
    works = [_make_work("W1")]
    verdicts = [ScreeningVerdict(work_id="W1", relevance_score=80, reason="ok", seed=1)]
    verdicts_path, catalogue_path, output_path = _write_inputs(tmp_path, works, verdicts)

    export_review_csv(verdicts_path, catalogue_path, output_path)

    with open(output_path, "rb") as f:
        head = f.read(3)
    assert head == b"\xef\xbb\xbf"


def test_build_row_empty_fields() -> None:
    work = _make_work(
        "W1", title=None, doi=None, publication_year=None, abstract=None
    )
    verdict = ScreeningVerdict(work_id="W1", relevance_score=None, reason=None)
    row = build_row(verdict, work)
    assert row["title"] == ""
    assert row["doi"] == ""
    assert row["publication_year"] == ""
    assert row["abstract"] == ""
    assert row["relevance_score"] == ""
    assert row["llm_reason"] == ""
    assert row["raw_response"] == ""


# --- CLI smoke ---


def test_cli_smoke_default_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from laglitsynth.cli import main

    works = [_make_work("W1")]
    verdicts = [ScreeningVerdict(work_id="W1", relevance_score=80, reason="ok", seed=1)]
    verdicts_path, catalogue_path, _ = _write_inputs(tmp_path, works, verdicts)

    main(
        [
            "screening-abstracts-export",
            "--verdicts",
            str(verdicts_path),
            "--catalogue",
            str(catalogue_path),
        ]
    )

    # Default resolves to <verdicts parent>/review.csv
    assert (tmp_path / "review.csv").exists()


def test_cli_smoke_custom_output(tmp_path: Path) -> None:
    from laglitsynth.cli import main

    works = [_make_work("W1")]
    verdicts = [ScreeningVerdict(work_id="W1", relevance_score=80, reason="ok", seed=1)]
    verdicts_path, catalogue_path, _ = _write_inputs(tmp_path, works, verdicts)
    custom = tmp_path / "custom" / "my-review.csv"

    main(
        [
            "screening-abstracts-export",
            "--verdicts",
            str(verdicts_path),
            "--catalogue",
            str(catalogue_path),
            "--output",
            str(custom),
        ]
    )

    assert custom.exists()
