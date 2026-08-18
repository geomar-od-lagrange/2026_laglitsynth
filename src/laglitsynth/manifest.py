"""Run manifest: the one file that pins a review's queries, run-id, and
the resolved input->output path chain per stage.

See plans/run-manifest.md for the design. This module holds the typed
models (``RunManifest``, ``StageEntry``) and the read/append/lookup
helpers (``load_manifest``, ``append_stage``, ``latest_output``) that
every run-id-aware stage will use to relay paths instead of a
human-carried ``--input``/``--run-id`` handoff.
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


class StageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str  # CLI subcommand name, e.g. "screening-abstracts"
    run_at: str  # ISO-8601 UTC
    inputs: dict[str, str]  # logical name -> resolved path
    output: str  # the path/dir this stage wrote
    meta_path: str | None  # the stage's own *-meta.json, when it writes one


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    run_id: str  # the shared run-id leaf for stages 3/7/8
    queries: list[str]  # the search term(s) this review ran
    data_dir: str  # repo-relative, the project root marker ("data")
    stages: list[StageEntry]  # append-only; one entry per stage invocation


def _manifest_path(data_dir: Path) -> Path:
    return data_dir / MANIFEST_FILENAME


def load_manifest(data_dir: Path) -> RunManifest | None:
    """Return the manifest at ``data_dir/manifest.json``, or ``None`` if absent."""
    path = _manifest_path(data_dir)
    if not path.exists():
        return None
    return RunManifest.model_validate_json(path.read_text())


def append_stage(data_dir: Path, entry: StageEntry) -> RunManifest:
    """Append ``entry`` to the manifest's stage log and rewrite the file.

    Reads the existing manifest at ``data_dir/manifest.json`` (which must
    already exist -- a review is initialised via ``manifest-init`` before
    any stage can append to it), appends ``entry``, rewrites via
    ``write_meta``, and returns the updated manifest.
    """
    manifest = load_manifest(data_dir)
    if manifest is None:
        raise FileNotFoundError(
            f"no manifest at {_manifest_path(data_dir)}; run `laglitsynth "
            f"manifest-init` first"
        )
    updated = manifest.model_copy(update={"stages": [*manifest.stages, entry]})
    write_meta(_manifest_path(data_dir), updated)
    return updated


def latest_output(manifest: RunManifest, stage: str) -> str | None:
    """Return the ``output`` of the last ``StageEntry`` matching ``stage``.

    ``None`` when the stage has never appended an entry.
    """
    for entry in reversed(manifest.stages):
        if entry.stage == stage:
            return entry.output
    return None


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "manifest-init",
        help="Mint a run-id and write the initial data/manifest.json for a review.",
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
    manifest_path = _manifest_path(data_dir)

    if manifest_path.exists():
        raise SystemExit(
            f"{manifest_path} already exists; refusing to clobber it "
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
        data_dir=str(data_dir),
        stages=[],
    )
    write_meta(manifest_path, manifest)
    print(f"Initialised {manifest_path} (run_id={run_id})", file=sys.stderr)
