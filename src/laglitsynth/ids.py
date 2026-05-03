"""Shared OpenAlex work-ID ↔ filesystem-stem and run-ID helpers."""

from __future__ import annotations

import argparse
import re
import uuid
from datetime import UTC, datetime

_WORK_ID_PREFIX = "https://openalex.org/"
_STEM_RE = re.compile(r"^W\d+$")
RUN_ID_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}_[0-9a-f]{12}$")


def work_id_to_filename(work_id: str) -> str:
    """Return the filesystem stem for an OpenAlex work_id URL."""
    return work_id.rsplit("/", 1)[-1]


def filename_to_work_id(stem: str) -> str | None:
    """Return the OpenAlex work_id URL for a stem, or None if not a W-ID."""
    if not _STEM_RE.match(stem):
        return None
    return f"{_WORK_ID_PREFIX}{stem}"


def generate_run_id(now: datetime | None = None) -> str:
    """Return a fresh run identifier of the form ``<iso-1s>_<12hex>``.

    The timestamp is rendered in UTC at 1-second resolution with ``:``
    replaced by ``-`` so the value is filesystem-safe. Naive or
    non-UTC ``now`` arguments are coerced to UTC. The 12-hex suffix
    from ``uuid.uuid4()`` removes any concurrent-job collision concern.
    """
    moment = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    stamp = (
        moment.replace(tzinfo=None).isoformat(timespec="seconds").replace(":", "-")
    )
    return f"{stamp}_{uuid.uuid4().hex[:12]}"


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "generate-run-id",
        help="Print a fresh run identifier to stdout (UTC ISO + 12-hex uuid).",
    )
    parser.set_defaults(run=_run_cli)
    return parser


def _run_cli(_args: argparse.Namespace) -> None:
    print(generate_run_id())
