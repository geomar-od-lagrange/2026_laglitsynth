"""Tests for the laglitsynth.manifest module."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from laglitsynth.ids import RUN_ID_RE
from laglitsynth.io import write_meta
from laglitsynth.manifest import (
    RunManifest,
    StageEntry,
    append_stage,
    latest_output,
    load_manifest,
    run as run_manifest_init,
)
from laglitsynth.models import RunMeta


def _make_manifest(
    run_id: str = "2026-01-01T00-00-00_abcdef123456",
    queries: list[str] | None = None,
    stages: list[StageEntry] | None = None,
) -> RunManifest:
    return RunManifest(
        run=RunMeta(
            tool="laglitsynth.manifest",
            run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
            validation_skipped=0,
        ),
        run_id=run_id,
        queries=queries if queries is not None else ["lagrangian oceanography"],
        data_dir="data",
        stages=stages if stages is not None else [],
    )


def _make_stage_entry(
    stage: str,
    output: str,
    inputs: dict[str, str] | None = None,
    meta_path: str | None = None,
) -> StageEntry:
    return StageEntry(
        stage=stage,
        run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        inputs=inputs if inputs is not None else {},
        output=output,
        meta_path=meta_path,
    )


class TestLoadManifest:
    def test_round_trip_through_write_meta(self, tmp_path: Path) -> None:
        manifest = _make_manifest()
        write_meta(tmp_path / "manifest.json", manifest)

        loaded = load_manifest(tmp_path)

        assert loaded == manifest

    def test_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert load_manifest(tmp_path) is None


class TestAppendStage:
    def test_appends_without_dropping_prior_entries(self, tmp_path: Path) -> None:
        first_entry = _make_stage_entry(
            "catalogue-dedup", "data/catalogue-dedup/deduplicated.jsonl"
        )
        manifest = _make_manifest(stages=[first_entry])
        write_meta(tmp_path / "manifest.json", manifest)

        second_entry = _make_stage_entry(
            "screening-abstracts",
            "data/screening-abstracts/run1/verdicts.jsonl",
            inputs={"catalogue": "data/catalogue-dedup/deduplicated.jsonl"},
            meta_path="data/screening-abstracts/run1/screening-meta.json",
        )
        updated = append_stage(tmp_path, second_entry)

        assert updated.stages == [first_entry, second_entry]
        # Rewritten file also reflects the append.
        reloaded = load_manifest(tmp_path)
        assert reloaded is not None
        assert reloaded.stages == [first_entry, second_entry]

    def test_raises_when_manifest_absent(self, tmp_path: Path) -> None:
        entry = _make_stage_entry("catalogue-dedup", "data/catalogue-dedup/deduplicated.jsonl")
        try:
            append_stage(tmp_path, entry)
        except FileNotFoundError:
            pass
        else:
            raise AssertionError("expected FileNotFoundError when manifest is absent")


class TestLatestOutput:
    def test_returns_last_matching_stage_output(self, tmp_path: Path) -> None:
        stages = [
            _make_stage_entry("catalogue-dedup", "data/catalogue-dedup/deduplicated.jsonl"),
            _make_stage_entry("screening-abstracts", "data/screening-abstracts/run1"),
            _make_stage_entry("screening-abstracts", "data/screening-abstracts/run2"),
        ]
        manifest = _make_manifest(stages=stages)

        assert latest_output(manifest, "screening-abstracts") == "data/screening-abstracts/run2"
        assert (
            latest_output(manifest, "catalogue-dedup")
            == "data/catalogue-dedup/deduplicated.jsonl"
        )

    def test_returns_none_for_stage_never_run(self, tmp_path: Path) -> None:
        manifest = _make_manifest(stages=[])
        assert latest_output(manifest, "screening-abstracts") is None


class TestManifestInitCli:
    def test_writes_valid_manifest(self, tmp_path: Path) -> None:
        from argparse import Namespace

        args = Namespace(queries=["lagrangian oceanography"], data_dir=tmp_path)
        run_manifest_init(args)

        manifest_path = tmp_path / "manifest.json"
        assert manifest_path.exists()

        data = json.loads(manifest_path.read_text())
        assert data["queries"] == ["lagrangian oceanography"]
        assert data["stages"] == []
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
