"""Shared I/O utilities for JSONL and metadata files."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from pathlib import Path

from pydantic import BaseModel, ValidationError

from laglitsynth.openalex.models import Work

logger = logging.getLogger(__name__)


def read_works_jsonl(path: Path) -> Iterator[Work]:
    """Yield validated Work records from a JSONL file."""
    with open(path) as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield Work.model_validate_json(line)
            except ValidationError as exc:
                logger.warning("Skipping invalid record on line %d: %s", line_no, exc)


def write_jsonl(records: Iterable[BaseModel], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "x") as f:
        for record in records:
            f.write(record.model_dump_json() + "\n")
            count += 1
    return count


def write_meta(path: Path, meta: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        f.write(meta.model_dump_json(indent=2) + "\n")
