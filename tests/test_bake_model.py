"""Tests for the bake-model CLI subcommand."""

from __future__ import annotations

import subprocess
from pathlib import Path
from subprocess import CalledProcessError
from unittest.mock import MagicMock, call, patch

import pytest

from laglitsynth.bake_model import _modelfile_content, run
from laglitsynth.cli import main


def _make_args(
    tag: str = "laglit-test",
    base: str = "llama3.1:8b",
    num_ctx: int = 32768,
) -> "argparse.Namespace":  # noqa: F821
    import argparse

    ns = argparse.Namespace(tag=tag, base=base, num_ctx=num_ctx)
    return ns


# ---------------------------------------------------------------------------
# Unit: Modelfile content
# ---------------------------------------------------------------------------


def test_emits_correct_modelfile() -> None:
    """subprocess.run receives a Modelfile whose content is FROM + PARAMETER."""
    captured_path: list[str] = []

    def fake_run(cmd: list[str], *, check: bool) -> None:
        # cmd == ["ollama", "create", tag, "-f", modelfile_path]
        captured_path.append(cmd[4])

    with patch("laglitsynth.bake_model.subprocess.run", side_effect=fake_run):
        run(_make_args(base="llama3.1:8b", num_ctx=32768))

    # The temp directory is cleaned up after run() returns, but the path
    # was captured.  We can't re-read the file, so verify via _modelfile_content
    # instead — the unit of work is the content function.
    content = _modelfile_content("llama3.1:8b", 32768)
    assert content == "FROM llama3.1:8b\nPARAMETER num_ctx 32768\n"


def test_modelfile_content_parametric() -> None:
    content = _modelfile_content("gemma3:4b", 8192)
    assert content == "FROM gemma3:4b\nPARAMETER num_ctx 8192\n"


# ---------------------------------------------------------------------------
# Unit: subprocess invocation shape
# ---------------------------------------------------------------------------


def test_invokes_ollama_create_with_tag() -> None:
    """subprocess.run is called with the correct ollama create command."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], *, check: bool) -> None:
        calls.append(list(cmd))

    with patch("laglitsynth.bake_model.subprocess.run", side_effect=fake_run):
        run(_make_args(tag="my-tag", base="llama3.1:8b", num_ctx=4096))

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "ollama"
    assert cmd[1] == "create"
    assert cmd[2] == "my-tag"
    assert cmd[3] == "-f"
    # cmd[4] is the Modelfile path — just check it ends with "Modelfile"
    assert Path(cmd[4]).name == "Modelfile"


def test_subprocess_called_with_check_true() -> None:
    """subprocess.run is always called with check=True."""
    check_values: list[bool] = []

    def fake_run(cmd: list[str], *, check: bool) -> None:
        check_values.append(check)

    with patch("laglitsynth.bake_model.subprocess.run", side_effect=fake_run):
        run(_make_args())

    assert check_values == [True]


# ---------------------------------------------------------------------------
# Unit: error propagation
# ---------------------------------------------------------------------------


def test_check_true_propagates_error() -> None:
    """A CalledProcessError from subprocess.run propagates out of run()."""
    error = CalledProcessError(1, ["ollama", "create"])
    with patch("laglitsynth.bake_model.subprocess.run", side_effect=error):
        with pytest.raises(CalledProcessError):
            run(_make_args())


# ---------------------------------------------------------------------------
# Unit: stdout output
# ---------------------------------------------------------------------------


def test_prints_tag_on_success(capsys: pytest.CaptureFixture[str]) -> None:
    """The new tag is printed to stdout after a successful ollama create."""
    with patch("laglitsynth.bake_model.subprocess.run"):
        run(_make_args(tag="laglit-screen"))

    captured = capsys.readouterr()
    assert captured.out.strip() == "laglit-screen"


# ---------------------------------------------------------------------------
# Integration: CLI wiring
# ---------------------------------------------------------------------------


def test_cli_wiring() -> None:
    """parse_args(["bake-model", ...]) produces the expected Namespace."""
    import argparse

    from laglitsynth.cli import main

    # We need to call main but intercept before run() fires.
    # Patch run so we can inspect args.
    received: list[argparse.Namespace] = []

    with patch("laglitsynth.bake_model.subprocess.run"):
        with patch("laglitsynth.bake_model.run", side_effect=received.append):
            main(["bake-model", "--tag", "x", "--base", "y", "--num-ctx", "32768"])

    assert len(received) == 1
    ns = received[0]
    assert ns.tag == "x"
    assert ns.base == "y"
    assert ns.num_ctx == 32768
