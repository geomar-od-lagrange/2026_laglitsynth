"""Bake an Ollama model with a fixed num_ctx via Modelfile."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "bake-model",
        help="Bake an Ollama model with a fixed num_ctx via Modelfile.",
    )
    parser.add_argument("--tag", required=True, help="Tag for the new baked model")
    parser.add_argument("--base", required=True, help="Base model to derive from")
    parser.add_argument(
        "--num-ctx", type=int, required=True, help="Context window size"
    )
    parser.set_defaults(run=run)
    return parser


def _modelfile_content(base: str, num_ctx: int) -> str:
    """Return the two-line Modelfile content for the given base and num_ctx."""
    return f"FROM {base}\nPARAMETER num_ctx {num_ctx}\n"


def run(args: argparse.Namespace) -> None:
    tag: str = args.tag
    base: str = args.base
    num_ctx: int = args.num_ctx

    content = _modelfile_content(base, num_ctx)

    with tempfile.TemporaryDirectory() as tmpdir:
        modelfile_path = Path(tmpdir) / "Modelfile"
        modelfile_path.write_text(content)
        subprocess.run(
            ["ollama", "create", tag, "-f", str(modelfile_path)],
            check=True,
        )

    print(tag)
