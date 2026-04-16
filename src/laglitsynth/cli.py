"""Unified CLI dispatcher for laglitsynth."""

from __future__ import annotations

import argparse
import sys

from laglitsynth.catalogue_fetch.fetch import build_subparser as build_fetch_subparser
from laglitsynth.catalogue_dedup.dedup import build_subparser as build_dedup_subparser
from laglitsynth.screening_abstracts.screen import (
    build_subparser as build_filter_subparser,
)
from laglitsynth.screening_adjudication.adjudicate import (
    build_subparser as build_adjudication_subparser,
)
from laglitsynth.fulltext_retrieval.retrieve import (
    build_subparser as build_retrieve_subparser,
)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="laglitsynth",
        description="Literature review pipeline tools.",
    )
    subparsers = parser.add_subparsers(dest="command")

    build_fetch_subparser(subparsers)
    build_dedup_subparser(subparsers)
    build_filter_subparser(subparsers)
    build_adjudication_subparser(subparsers)
    build_retrieve_subparser(subparsers)

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.run(args)
