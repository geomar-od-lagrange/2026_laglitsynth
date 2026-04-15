"""Unified CLI dispatcher for laglitsynth."""

from __future__ import annotations

import argparse
import sys

from laglitsynth.llmfilter.filter import build_subparser as build_filter_subparser
from laglitsynth.openalex.fetch import build_subparser as build_fetch_subparser


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="laglitsynth",
        description="Literature review pipeline tools.",
    )
    subparsers = parser.add_subparsers(dest="command")

    build_fetch_subparser(subparsers)
    build_filter_subparser(subparsers)

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.run(args)
