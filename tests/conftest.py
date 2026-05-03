"""Shared test helpers for the laglitsynth test suite.

Plain top-level functions — not pytest fixtures — so they can be imported
directly with ``from conftest import _make_work``.  Pytest discovers
``conftest.py`` automatically and puts it on the import path for all sibling
test files.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from laglitsynth.catalogue_fetch.models import Author, Authorship, Work
from laglitsynth.fulltext_extraction.models import ExtractedDocument

TEI_NS = "http://www.tei-c.org/ns/1.0"


def _make_authorship(display_name: str) -> Authorship:
    """Return a minimal ``Authorship`` for the given author display name."""
    return Authorship(
        author_position="first",
        author=Author(display_name=display_name),
        institutions=[],
        countries=[],
        raw_affiliation_strings=[],
    )


def _make_work(
    work_id: str = "https://openalex.org/W1",
    title: str = "Test Paper",
    abstract: str | None = "An abstract.",
    doi: str | None = None,
    publication_year: int | None = None,
    pdf_url: str | None = None,
    oa_url: str | None = None,
    authorships: list[Authorship] | None = None,
) -> Work:
    """Return a minimal valid ``Work`` for testing.

    All fields beyond ``work_id`` are optional so callers can vary only the
    dimensions relevant to their test.
    """
    primary_location = None
    if pdf_url is not None:
        primary_location = {"pdf_url": pdf_url}
    open_access = None
    if oa_url is not None:
        open_access = {"oa_url": oa_url}
    return Work(
        id=work_id,
        title=title,
        abstract=abstract,
        doi=doi,
        publication_year=publication_year,
        authorships=authorships if authorships is not None else [],
        biblio={},
        cited_by_count=0,
        referenced_works=[],
        keywords=[],
        topics=[],
        primary_location=primary_location,
        open_access=open_access,
    )


def _write_works_jsonl(path: Path, works: list[Work]) -> None:
    with open(path, "w") as f:
        for w in works:
            f.write(w.model_dump_json() + "\n")


def _write_extractions_jsonl(path: Path, records: list[ExtractedDocument]) -> None:
    with open(path, "w") as f:
        for r in records:
            f.write(r.model_dump_json() + "\n")


def _mock_openai_response(content: str) -> MagicMock:
    """Build a mock OpenAI chat completion response returning ``content``."""
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]
    return response


def _write_tei(path: Path, body_content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        (
            f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<TEI xmlns="{TEI_NS}"><text><body>{body_content}</body></text></TEI>'
        ).encode()
    )
