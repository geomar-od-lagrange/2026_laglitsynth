"""Unified CLI dispatcher for laglitsynth."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from laglitsynth.bake_model import build_subparser as build_bake_model_subparser
from laglitsynth.catalogue_dedup.dedup import build_subparser as build_dedup_subparser
from laglitsynth.catalogue_fetch.fetch import build_subparser as build_fetch_subparser
from laglitsynth.config import load_config
from laglitsynth.extraction_codebook.extract import build_subparser as build_extraction_codebook_subparser
from laglitsynth.fulltext_eligibility.eligibility import build_subparser as build_eligibility_subparser
from laglitsynth.fulltext_extraction.extract import build_subparser as build_extraction_subparser
from laglitsynth.fulltext_retrieval.retrieve import build_subparser as build_retrieval_subparser
from laglitsynth.ids import build_subparser as build_run_id_subparser
from laglitsynth.screening_abstracts.export import build_subparser as build_screening_export_subparser
from laglitsynth.screening_abstracts.screen import build_subparser as build_screening_subparser

_BUILDERS = (
    build_bake_model_subparser,
    build_fetch_subparser,
    build_dedup_subparser,
    build_screening_subparser,
    build_screening_export_subparser,
    build_retrieval_subparser,
    build_extraction_subparser,
    build_eligibility_subparser,
    build_extraction_codebook_subparser,
    build_run_id_subparser,
)


def _find_subcommand(argv: list[str], choices: dict[str, argparse.ArgumentParser]) -> str | None:
    for token in argv:
        if token in choices:
            return token
    return None


def _sniff_config(argv: list[str]) -> str | None:
    """Pull ``--config <path>`` (or ``--config=<path>``) out of argv.

    A standalone parser is used so subparser-required-args validation
    does not fire — only the active subcommand should engage that, and
    only after defaults have been seeded.
    """
    sniffer = argparse.ArgumentParser(add_help=False)
    sniffer.add_argument("--config", default=None)
    pre, _rest = sniffer.parse_known_args(argv)
    config: str | None = pre.config
    return config


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        prog="laglitsynth",
        description="Literature review pipeline tools.",
    )
    subparsers = parser.add_subparsers(dest="command")

    for builder in _BUILDERS:
        builder(subparsers)

    # Apply --config defaults to the active subparser before final parse,
    # so argparse precedence (explicit CLI flag > set_defaults default >
    # add_argument default) leaves explicit flags winning.
    command = _find_subcommand(argv, subparsers.choices)
    config_path = _sniff_config(argv)
    if command is not None and config_path is not None:
        subparsers.choices[command].set_defaults(**load_config(Path(config_path)))

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    args.run(args)
