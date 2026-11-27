"""Shared I/O utilities for JSONL and metadata files."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

_T = TypeVar("_T", bound=BaseModel)


@dataclass
class JsonlReadStats:
    skipped: int = field(default=0)


def write_jsonl(records: Iterable[BaseModel], path: Path) -> int:
    """Write records to a JSONL file, overwriting any existing file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w") as f:
        for record in records:
            f.write(record.model_dump_json() + "\n")
            count += 1
    return count


def append_jsonl(record: BaseModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(record.model_dump_json() + "\n")


def read_jsonl(
    path: Path, model: type[_T], stats: JsonlReadStats | None = None
) -> Iterator[_T]:
    with open(path) as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield model.model_validate_json(line)
            except ValidationError as exc:
                logger.warning("Skipping invalid record on line %d: %s", line_no, exc)
                if stats is not None:
                    stats.skipped += 1


def drop_jsonl_rows(path: Path, model: type[_T], drop: Callable[[_T], bool]) -> int:
    """Remove the rows of ``path`` for which ``drop`` is true; return how many.

    Every other line, including one that fails validation, is copied
    through unchanged. The new content goes to a temporary file beside
    ``path``, is fsynced and moved over it with ``os.replace``, so a crash
    leaves either the old or the new file. Nothing is written when no row
    is dropped.
    """
    kept_lines: list[str] = []
    dropped = 0
    with open(path) as f:
        for line in f:
            if line.strip():
                try:
                    row = model.model_validate_json(line)
                except ValidationError:
                    pass
                else:
                    if drop(row):
                        dropped += 1
                        continue
            kept_lines.append(line if line.endswith("\n") else line + "\n")
    if not dropped:
        return 0
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "w") as f:
            f.writelines(kept_lines)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return dropped


def write_meta(path: Path, meta: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(meta.model_dump_json(indent=2) + "\n")
