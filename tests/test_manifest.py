"""Tests for the laglitsynth.manifest module."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from laglitsynth.ids import RUN_ID_RE
from laglitsynth.io import write_meta
from laglitsynth.manifest import (
    RunManifest,
    load_manifest,
    resolve_run_id,
    run as run_manifest_init,
)
from laglitsynth.models import RunMeta


def _make_manifest(
    run_id: str = "2026-01-01T00-00-00_abcdef123456",
    queries: list[str] | None = None,
) -> RunManifest:
    return RunManifest(
        run=RunMeta(
            tool="laglitsynth.manifest",
            run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
            validation_skipped=0,
        ),
        run_id=run_id,
        queries=queries if queries is not None else ["lagrangian oceanography"],
    )


class TestLoadManifest:
    def test_round_trip_through_write_meta(self, tmp_path: Path) -> None:
        manifest = _make_manifest()
        write_meta(tmp_path / "manifest.json", manifest)

        loaded = load_manifest(tmp_path)

        assert loaded == manifest

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert load_manifest(tmp_path) is None


class TestManifestInitCli:
    def test_writes_valid_manifest(self, tmp_path: Path) -> None:
        from argparse import Namespace

        args = Namespace(queries=["lagrangian oceanography"], data_dir=tmp_path)
        run_manifest_init(args)

        manifest_path = tmp_path / "manifest.json"
        assert manifest_path.exists()

        data = json.loads(manifest_path.read_text())
        assert data["queries"] == ["lagrangian oceanography"]
        assert set(data) == {"run", "run_id", "queries"}
        assert RUN_ID_RE.match(data["run_id"])

        # Also round-trips through the typed loader.
        loaded = load_manifest(tmp_path)
        assert loaded is not None
        assert loaded.queries == ["lagrangian oceanography"]

    def test_multiple_queries(self, tmp_path: Path) -> None:
        from argparse import Namespace

        args = Namespace(
            queries=["lagrangian oceanography", "particle tracking"],
            data_dir=tmp_path,
        )
        run_manifest_init(args)

        loaded = load_manifest(tmp_path)
        assert loaded is not None
        assert loaded.queries == ["lagrangian oceanography", "particle tracking"]

    def test_refuses_to_clobber_existing_manifest(self, tmp_path: Path) -> None:
        from argparse import Namespace

        args = Namespace(queries=["first query"], data_dir=tmp_path)
        run_manifest_init(args)

        first_contents = (tmp_path / "manifest.json").read_text()

        args_again = Namespace(queries=["second query"], data_dir=tmp_path)
        try:
            run_manifest_init(args_again)
        except SystemExit:
            pass
        else:
            raise AssertionError("expected SystemExit when manifest already exists")

        # File is untouched by the refused re-run.
        assert (tmp_path / "manifest.json").read_text() == first_contents


class TestManifestInitCliViaMain:
    def test_subcommand_end_to_end(self, tmp_path: Path) -> None:
        from laglitsynth.cli import main

        main(
            [
                "manifest-init",
                "lagrangian oceanography",
                "--data-dir",
                str(tmp_path),
            ]
        )

        manifest_path = tmp_path / "manifest.json"
        assert manifest_path.exists()
        data = json.loads(manifest_path.read_text())
        assert data["queries"] == ["lagrangian oceanography"]
        assert RUN_ID_RE.match(data["run_id"])


class TestResolveRunId:
    def test_flag_wins_over_the_manifest(self, tmp_path: Path) -> None:
        write_meta(tmp_path / "manifest.json", _make_manifest(run_id="from-manifest"))

        assert resolve_run_id(tmp_path, "from-flag") == "from-flag"

    def test_manifest_supplies_it_when_the_flag_is_omitted(
        self, tmp_path: Path
    ) -> None:
        write_meta(tmp_path / "manifest.json", _make_manifest(run_id="from-manifest"))

        assert resolve_run_id(tmp_path, None) == "from-manifest"

    def test_flag_works_without_a_manifest(self, tmp_path: Path) -> None:
        assert resolve_run_id(tmp_path, "from-flag") == "from-flag"

    def test_no_flag_and_no_manifest_exits_naming_manifest_init(
        self, tmp_path: Path
    ) -> None:
        with pytest.raises(SystemExit, match="manifest-init"):
            resolve_run_id(tmp_path, None)
