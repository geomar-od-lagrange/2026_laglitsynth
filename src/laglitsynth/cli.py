"""Unified CLI dispatcher for laglitsynth."""

from __future__ import annotations

import argparse
import sys

from laglitsynth.catalogue_dedup.dedup import build_subparser as build_dedup_subparser
from laglitsynth.catalogue_fetch.fetch import build_subparser as build_fetch_subparser
from laglitsynth.extraction_codebook.extract import build_subparser as build_extraction_codebook_subparser
from laglitsynth.fulltext_eligibility.eligibility import build_subparser as build_eligibility_subparser
from laglitsynth.fulltext_extraction.extract import build_subparser as build_extraction_subparser
from laglitsynth.fulltext_retrieval.retrieve import build_subparser as build_retrieval_subparser
from laglitsynth.screening_abstracts.export import build_subparser as build_screening_export_subparser
from laglitsynth.screening_abstracts.screen import build_subparser as build_screening_subparser
from laglitsynth.screening_adjudication.adjudicate import build_subparser as build_adjudication_subparser

_BUILDERS = (
    build_fetch_subparser,
    build_dedup_subparser,
    build_screening_subparser,
    build_screening_export_subparser,
    build_adjudication_subparser,
    build_retrieval_subparser,
    build_extraction_subparser,
    build_eligibility_subparser,
    build_extraction_codebook_subparser,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="laglitsynth",
        description="Literature review pipeline tools.",
    )
    subparsers = parser.add_subparsers(dest="command")

    for builder in _BUILDERS:
        builder(subparsers)

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.run(args)
