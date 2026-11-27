from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from laglitsynth.io import drop_jsonl_rows


class _Row(BaseModel):
    work_id: str
    reason: str | None = None


def _line(row: _Row) -> str:
    return row.model_dump_json() + "\n"


def _is_sentinel(row: _Row) -> bool:
    return row.reason is not None


def test_drop_jsonl_rows_rewrites_and_keeps_invalid_lines(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text(
        _line(_Row(work_id="A"))
        + '{"not_a_row": 1}\n'
        + _line(_Row(work_id="B", reason="llm-timeout"))
        + "not json\n"
    )

    assert drop_jsonl_rows(path, _Row, _is_sentinel) == 1

    assert path.read_text() == _line(_Row(work_id="A")) + '{"not_a_row": 1}\nnot json\n'
    assert list(tmp_path.iterdir()) == [path]


def test_drop_jsonl_rows_leaves_file_when_nothing_dropped(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text(_line(_Row(work_id="A")))
    mtime = path.stat().st_mtime_ns

    assert drop_jsonl_rows(path, _Row, _is_sentinel) == 0

    assert path.stat().st_mtime_ns == mtime


def test_drop_jsonl_rows_removes_temp_file_on_failure(tmp_path: Path) -> None:
    path = tmp_path / "rows.jsonl"
    before = _line(_Row(work_id="A", reason="llm-rejected"))
    path.write_text(before)

    with patch("laglitsynth.io.os.replace", side_effect=OSError("disk")):
        with pytest.raises(OSError):
            drop_jsonl_rows(path, _Row, _is_sentinel)

    assert path.read_text() == before
    assert list(tmp_path.iterdir()) == [path]
