"""Unified CLI dispatcher for laglitsynth."""

from __future__ import annotations

import argparse
import sys

from laglitsynth.catalogue_dedup.dedup import build_subparser as build_dedup_subparser
from laglitsynth.catalogue_fetch.fetch import build_subparser as build_fetch_subparser
from laglitsynth.extraction_codebook.extract import (
    build_subparser as build_extraction_codebook_subparser,
)
from laglitsynth.fulltext_eligibility.eligibility import (
    build_subparser as build_eligibility_subparser,
)
from laglitsynth.fulltext_extraction.extract import (
    build_subparser as build_extraction_subparser,
)
from laglitsynth.fulltext_retrieval.retrieve import (
    build_subparser as build_retrieval_subparser,
)
from laglitsynth.screening_abstracts.screen import (
    build_subparser as build_screening_subparser,
)
from laglitsynth.screening_adjudication.adjudicate import (
    build_subparser as build_adjudication_subparser,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="laglitsynth",
        description="Literature review pipeline tools.",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Registered in pipeline stage order (1-8)
    build_fetch_subparser(subparsers)
    build_dedup_subparser(subparsers)
    build_screening_subparser(subparsers)
    build_adjudication_subparser(subparsers)
    build_retrieval_subparser(subparsers)
    build_extraction_subparser(subparsers)
    build_eligibility_subparser(subparsers)
    build_extraction_codebook_subparser(subparsers)

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.run(args)
