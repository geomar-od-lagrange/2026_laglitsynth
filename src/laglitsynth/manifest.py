"""Run manifest: the one file that pins a review's queries and run-id.

``data/manifest.json`` is written once by ``manifest-init`` and never
rewritten. Stages 3, 7, and 8 write under a ``<run-id>/`` leaf, and every
stage that reads one of those leaves resolves the run-id from here when
``--run-id`` is omitted. Everything else a stage needs is at a fixed path
under ``--data-dir`` (``laglitsynth.layout``).
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from laglitsynth.ids import generate_run_id
from laglitsynth.io import write_meta
from laglitsynth.models import RunMeta

TOOL_NAME = "laglitsynth.manifest"

MANIFEST_FILENAME = "manifest.json"


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    run_id: str  # the shared run-id leaf for stages 3/7/8
    queries: list[str]  # the search term(s) this review ran


def manifest_path(data_dir: Path) -> Path:
    return data_dir / MANIFEST_FILENAME


def load_manifest(data_dir: Path) -> RunManifest | None:
    """Return the manifest at ``data_dir/manifest.json``, or ``None`` if absent."""
    path = manifest_path(data_dir)
    if not path.exists():
        return None
    return RunManifest.model_validate_json(path.read_text())


def resolve_run_id(data_dir: Path, flag_value: str | None) -> str:
    """Return the run-id: the flag, else the manifest's, else exit.

    Nothing is minted here. A stage that silently invented a run-id would
    write a leaf no other stage can find, which is the divergence the
    manifest exists to prevent.
    """
    if flag_value is not None:
        return flag_value
    manifest = load_manifest(data_dir)
    if manifest is None:
        raise SystemExit(
            f"no run-id: pass --run-id or run `laglitsynth manifest-init` "
            f"to create {manifest_path(data_dir)}"
        )
    return manifest.run_id


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "manifest-init",
        help="Mint a run-id and write data/manifest.json for a review.",
    )
    parser.add_argument(
        "queries",
        nargs="+",
        help="Search term(s) this review runs (recorded, not executed).",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Bucket root for stage outputs (default: data/)",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    data_dir: Path = args.data_dir
    path = manifest_path(data_dir)

    if path.exists():
        raise SystemExit(
            f"{path} already exists; refusing to clobber it "
            f"(a review is initialised once)"
        )

    run_id = generate_run_id()
    manifest = RunManifest(
        run=RunMeta(
            tool=TOOL_NAME,
            run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
            validation_skipped=0,
        ),
        run_id=run_id,
        queries=list(args.queries),
    )
    write_meta(path, manifest)
    print(f"Initialised {path} (run_id={run_id})", file=sys.stderr)
