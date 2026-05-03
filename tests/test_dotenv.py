"""Tests for laglitsynth.dotenv.load_env_var."""

from __future__ import annotations

from pathlib import Path

import pytest

from laglitsynth.dotenv import load_env_var


def test_file_absent_returns_none(tmp_path: Path) -> None:
    result = load_env_var("MY_KEY", env_path=tmp_path / "nonexistent.env")
    assert result is None


def test_key_absent_returns_none(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("OTHER_KEY=hello\n")
    assert load_env_var("MY_KEY", env_path=env) is None


def test_key_present_returns_value(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("MY_KEY=secret123\n")
    assert load_env_var("MY_KEY", env_path=env) == "secret123"


def test_comments_and_blank_lines_ignored(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# this is a comment\n"
        "\n"
        "MY_KEY=abc\n"
        "# another comment\n"
    )
    assert load_env_var("MY_KEY", env_path=env) == "abc"


def test_double_quoted_value_stripped(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text('MY_KEY="quoted value"\n')
    assert load_env_var("MY_KEY", env_path=env) == "quoted value"


def test_single_quoted_value_stripped(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("MY_KEY='single quoted'\n")
    assert load_env_var("MY_KEY", env_path=env) == "single quoted"


def test_whitespace_around_key_and_value(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("  MY_KEY  =  hello  \n")
    assert load_env_var("MY_KEY", env_path=env) == "hello"


def test_does_not_return_partial_key_match(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("MY_KEY_EXTRA=wrong\nMY_KEY=right\n")
    assert load_env_var("MY_KEY", env_path=env) == "right"
