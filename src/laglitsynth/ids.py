"""Shared OpenAlex work-ID ↔ filesystem-stem helpers."""

from __future__ import annotations

import re

_WORK_ID_PREFIX = "https://openalex.org/"
_STEM_RE = re.compile(r"^W\d+$")


def work_id_to_filename(work_id: str) -> str:
    """Return the filesystem stem for an OpenAlex work_id URL."""
    return work_id.rsplit("/", 1)[-1]


def filename_to_work_id(stem: str) -> str | None:
    """Return the OpenAlex work_id URL for a stem, or None if not a W-ID."""
    if not _STEM_RE.match(stem):
        return None
    return f"{_WORK_ID_PREFIX}{stem}"
