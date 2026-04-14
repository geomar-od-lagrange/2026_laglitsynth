"""Shared JSONL reader for Work records."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

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
