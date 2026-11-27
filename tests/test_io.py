from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from laglitsynth.io import JsonlReadStats, keep_jsonl_rows


class _Row(BaseModel):
    work_id: str
    reason: str | None = None


def _write(path: Path, rows: list[_Row]) -> str:
    text = "".join(r.model_dump_json() + "\n" for r in rows)
    path.write_text(text)
    return text


def test_keep_jsonl_rows_rewrites_atomically(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    _write(path, [_Row(work_id="A"), _Row(work_id="B", reason="llm-timeout")])
    with open(path, "a") as f:
        f.write('{"not_a_row": 1}\n')
    stats = JsonlReadStats()

    kept, dropped = keep_jsonl_rows(
        path, _Row, lambda r: r.reason is None, stats=stats
    )

    assert [r.work_id for r in kept] == ["A"]
    assert dropped == 1
    assert stats.skipped == 1
    assert path.read_text() == _Row(work_id="A").model_dump_json() + "\n"
    assert list(tmp_path.iterdir()) == [path]


def test_keep_jsonl_rows_without_rewrite_leaves_file(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    before = _write(path, [_Row(work_id="A"), _Row(work_id="B", reason="llm-rejected")])

    kept, dropped = keep_jsonl_rows(
        path, _Row, lambda r: r.reason is None, rewrite=False
    )

    assert [r.work_id for r in kept] == ["A"]
    assert dropped == 1
    assert path.read_text() == before
