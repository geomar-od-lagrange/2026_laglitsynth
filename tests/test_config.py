"""Tests for ``laglitsynth.config``."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
import yaml

from laglitsynth.config import (
    load_config,
    register_config_arg,
    resolve_yaml_arg,
    save_resolved_config,
)


def _apply_config(parser: argparse.ArgumentParser, argv: list[str]) -> None:
    """Mimic the cli.main dispatch: sniff --config and set_defaults."""
    sniffer = argparse.ArgumentParser(add_help=False)
    sniffer.add_argument("--config", default=None)
    pre, _ = sniffer.parse_known_args(argv)
    if pre.config:
        parser.set_defaults(**load_config(Path(pre.config)))


def _write_yaml(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def test_load_config_round_trip(tmp_path: Path) -> None:
    src = _write_yaml(tmp_path / "cfg.yaml", {"model": "gemma3:4b", "concurrency": 4})
    assert load_config(src) == {"model": "gemma3:4b", "concurrency": 4}


def test_load_config_rejects_non_mapping(tmp_path: Path) -> None:
    src = tmp_path / "bad.yaml"
    src.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected a top-level mapping"):
        load_config(src)


def test_load_config_empty_file(tmp_path: Path) -> None:
    src = tmp_path / "empty.yaml"
    src.write_text("", encoding="utf-8")
    assert load_config(src) == {}


def _build_parser_with_args() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    register_config_arg(parser)
    parser.add_argument("--model", default="default-model")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--threshold", type=int, default=50)
    return parser


def test_config_seeds_defaults_explicit_flag_wins(tmp_path: Path) -> None:
    cfg = _write_yaml(
        tmp_path / "cfg.yaml",
        {"model": "from-config", "concurrency": 8, "threshold": 75},
    )
    parser = _build_parser_with_args()
    argv = ["--config", str(cfg), "--model", "from-cli"]
    _apply_config(parser, argv)

    args = parser.parse_args(argv)

    # CLI flag overrides config.
    assert args.model == "from-cli"
    # Config seeds the unset flags.
    assert args.concurrency == 8
    assert args.threshold == 75


def test_no_config_falls_through_to_argparse_defaults() -> None:
    parser = _build_parser_with_args()
    _apply_config(parser, [])

    args = parser.parse_args([])
    assert args.model == "default-model"
    assert args.concurrency == 1
    assert args.threshold == 50


def test_save_resolved_config_inlines_named_paths(tmp_path: Path) -> None:
    codebook = _write_yaml(
        tmp_path / "codebook.yaml",
        {"id": "test", "system_prompt": "hello", "fields": []},
    )
    args = argparse.Namespace(
        codebook=codebook,
        model="gemma3:4b",
        threshold=50,
        config=None,
        run=lambda a: None,
        command="screening-abstracts",
    )
    run_dir = tmp_path / "run-1"
    out = save_resolved_config(args, run_dir, inlines=["codebook"])

    written = load_config(out)
    assert written["model"] == "gemma3:4b"
    assert written["threshold"] == 50
    assert written["codebook"] == {
        "id": "test",
        "system_prompt": "hello",
        "fields": [],
    }
    # Drop keys gone.
    assert "config" not in written
    assert "run" not in written
    assert "command" not in written


def test_run_snapshot_round_trips_via_config(tmp_path: Path) -> None:
    """A saved snapshot should reload cleanly when fed back as --config."""
    codebook = _write_yaml(
        tmp_path / "codebook.yaml",
        {"id": "test", "system_prompt": "hello", "fields": []},
    )
    first = argparse.Namespace(
        codebook=codebook, model="m", threshold=75, config=None
    )
    run_dir = tmp_path / "run-1"
    written = save_resolved_config(first, run_dir, inlines=["codebook"])

    parser = argparse.ArgumentParser()
    register_config_arg(parser)
    parser.add_argument("--codebook", default=None)
    parser.add_argument("--model", default="default")
    parser.add_argument("--threshold", type=int, default=50)
    argv = ["--config", str(written)]
    _apply_config(parser, argv)
    reloaded = parser.parse_args(argv)

    # Inlined codebook arrives as a dict.
    assert reloaded.codebook == {
        "id": "test",
        "system_prompt": "hello",
        "fields": [],
    }
    assert reloaded.model == "m"
    assert reloaded.threshold == 75


def test_load_config_resolves_yaml_paths_relative_to_config_file(tmp_path: Path) -> None:
    """An input config can sit alongside the YAMLs it references."""
    sub_dir = tmp_path / "configs"
    sub_dir.mkdir()
    codebook = _write_yaml(
        sub_dir / "codebook.yaml", {"id": "x", "system_prompt": "p", "fields": []}
    )
    cfg = _write_yaml(sub_dir / "input.yaml", {"codebook": "codebook.yaml"})

    loaded = load_config(cfg)
    # The relative path got resolved against the config file's directory,
    # not the test's CWD.
    assert loaded["codebook"] == str(codebook.resolve())


def test_cli_config_dispatch_does_not_bleed_across_subparsers(tmp_path: Path) -> None:
    """A missing --config path on one subcommand must not poison another.

    Regression for the build-time-sniff bug: every subparser builder
    used to call ``add_config_arg(parser)``, which read sys.argv
    globally — so a typo on the active subcommand could surface as a
    FileNotFoundError originating in an unrelated subparser.
    """
    from unittest.mock import patch

    from laglitsynth.cli import main

    missing = tmp_path / "does-not-exist.yaml"
    captured: dict[str, object] = {}

    def fake_run(args: object) -> None:
        captured["called"] = True

    # FileNotFoundError must come from the active subcommand only —
    # i.e. the screening-abstracts run, not extraction-codebook's
    # subparser build.
    with patch(
        "laglitsynth.screening_abstracts.screen.run", fake_run
    ), pytest.raises(FileNotFoundError):
        main(
            [
                "screening-abstracts",
                str(tmp_path / "in.jsonl"),
                "the-prompt",
                "--config",
                str(missing),
            ]
        )

    # Sanity: invoking *without* --config does not read any file at all.
    captured.clear()
    with patch(
        "laglitsynth.screening_abstracts.screen.run", fake_run
    ), patch("laglitsynth.screening_abstracts.screen.preflight"):
        main(
            [
                "screening-abstracts",
                str(tmp_path / "in.jsonl"),
                "the-prompt",
            ]
        )
    assert captured.get("called") is True


def test_load_config_leaves_absolute_yaml_paths_alone(tmp_path: Path) -> None:
    codebook = _write_yaml(
        tmp_path / "codebook.yaml", {"id": "x", "system_prompt": "p", "fields": []}
    )
    cfg = _write_yaml(
        tmp_path / "input.yaml", {"codebook": str(codebook.resolve())}
    )
    loaded = load_config(cfg)
    assert loaded["codebook"] == str(codebook.resolve())


def test_resolve_yaml_arg_passthrough_dict() -> None:
    spec = {"id": "x", "fields": []}
    assert resolve_yaml_arg(spec) is spec


def test_resolve_yaml_arg_loads_path(tmp_path: Path) -> None:
    src = _write_yaml(tmp_path / "x.yaml", {"id": "x", "fields": []})
    assert resolve_yaml_arg(src) == {"id": "x", "fields": []}
    assert resolve_yaml_arg(str(src)) == {"id": "x", "fields": []}
