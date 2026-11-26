"""Tests for scripts/run-pipeline.sh against a stub ``laglitsynth`` CLI.

Each test runs the runner in a temporary working directory with a stub
``laglitsynth`` first on PATH and a minimal environment, so nothing reaches
the network or the real data tree.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "run-pipeline.sh"

# Logs each call's subcommand and the LLM-side variables it sees.
# review-config fails when STUB_REVIEW_CONFIG_FAILS is set, else emits a
# minimal CFG_* block naming an endpoint and a screening model.
_STUB = """\
#!/usr/bin/env bash
echo "$1 LLM_API_KEY=${LLM_API_KEY:-} QUERY=${QUERY:-}" >> "$STUB_LOG"
if [[ "$1" == review-config ]]; then
    if [[ -n "${STUB_REVIEW_CONFIG_FAILS:-}" ]]; then
        echo "invalid review config" >&2
        exit 1
    fi
    echo "CFG_LLM_BASE_URL=http://stub/v1"
    echo "CFG_SCREENING_MODEL=stub-model"
fi
"""

_DOTENV = (
    "# credentials\n"
    "\n"
    "OPENALEX_API_KEY=openalex\n"
    "UNPAYWALL_EMAIL='me@example.org'\n"
    "LLM_API_KEY=from-dotenv\n"
    'QUERY="from dotenv"\n'
)

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run(
    tmp_path: Path, env: dict[str, str], dotenv: str = _DOTENV
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "laglitsynth"
    stub.write_text(_STUB, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    work = tmp_path / "work"
    work.mkdir()
    (work / ".env").write_text(dotenv, encoding="utf-8")
    log = tmp_path / "calls.log"
    log.touch()
    full_env = {
        "PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin",
        "HOME": str(tmp_path),
        "STUB_LOG": str(log),
        "STOP_AFTER_STAGE": "3",
        **env,
    }
    result = subprocess.run(
        ["bash", str(RUNNER), "review.yaml"],
        cwd=work,
        env=full_env,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, log.read_text(encoding="utf-8").splitlines()


def _call(calls: list[str], subcommand: str) -> str:
    matches = [c for c in calls if c.split(" ", 1)[0] == subcommand]
    assert len(matches) == 1, calls
    return matches[0]


def test_exported_variable_wins_over_dotenv(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, {"LLM_API_KEY": "from-env"})
    assert result.returncode == 0, result.stderr
    assert "LLM_API_KEY=from-env " in _call(calls, "screening-abstracts")


def test_exported_variable_wins_over_empty_dotenv_entry(tmp_path: Path) -> None:
    dotenv = "OPENALEX_API_KEY=openalex\nUNPAYWALL_EMAIL=me@example.org\nLLM_API_KEY=\n"
    result, calls = _run(tmp_path, {"LLM_API_KEY": "from-env"}, dotenv)
    assert result.returncode == 0, result.stderr
    assert "LLM_API_KEY=from-env " in _call(calls, "screening-abstracts")


def test_dotenv_fills_unset_variables_and_strips_quotes(tmp_path: Path) -> None:
    result, calls = _run(tmp_path, {})
    assert result.returncode == 0, result.stderr
    call = _call(calls, "screening-abstracts")
    assert "LLM_API_KEY=from-dotenv " in call
    assert call.endswith("QUERY=from dotenv")


def test_failing_review_config_aborts_the_runner(tmp_path: Path) -> None:
    # The environment names everything the config would, so only the
    # failed validation can stop the run.
    env = {
        "STUB_REVIEW_CONFIG_FAILS": "1",
        "LLM_BASE_URL": "http://stub/v1",
        "SCREENING_MODEL": "stub-model",
    }
    result, calls = _run(tmp_path, env)
    assert result.returncode != 0
    assert [c.split(" ", 1)[0] for c in calls] == ["review-config"]


@pytest.mark.parametrize("stop", ["0", "x", "-1"])
def test_stop_after_stage_must_be_a_positive_integer(tmp_path: Path, stop: str) -> None:
    result, calls = _run(tmp_path, {"STOP_AFTER_STAGE": stop})
    assert result.returncode == 2
    assert "positive integer" in result.stderr
    assert [c.split(" ", 1)[0] for c in calls] == ["review-config"]
