"""Tests for the screening_abstracts submodule (decisions endpoint)."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
import yaml

from laglitsynth.llm import LlmEndpoint
from laglitsynth.models import LlmMeta, RunMeta
from laglitsynth.prompts import DecisionQuestion
from laglitsynth.screening_abstracts.models import ScreeningMeta, ScreeningVerdict
from laglitsynth.screening_abstracts.screen import (
    USER_TEMPLATE,
    format_screening_input,
    run,
)

from conftest import (
    TEST_RUN_ID,
    _make_work,
    _write_manifest,
    _write_works_jsonl,
)

_DECISION_BLOCK = {
    "instructions": "Is this about ocean particle tracking?",
    "criteria": {"true": "Ocean flow tracking.", "false": "Anything else."},
}
# An inlined screening-criteria spec (dict form, consumed directly by
# resolve_yaml_arg) standing in for the on-disk YAML in run()-level tests.
_TEST_CRITERIA_SPEC: dict[str, Any] = {"id": "test-criteria", "decision": _DECISION_BLOCK}

_BASE = "https://llm.example.org/v1"

# Probability the mock endpoint returns, keyed by the abstract in the input.
_Probs = dict[str, float]
_DEFAULT_PROBS: _Probs = {"Ocean currents.": 0.73, "Forest ecology.": 0.1}


# --- format_screening_input ---


def test_format_screening_input_complete_work() -> None:
    work = _make_work(
        "W1",
        title="A Real Title",
        abstract="An abstract.",
        publication_year=2023,
    )
    rendered = format_screening_input(work)
    assert "Title: A Real Title" in rendered
    assert "Abstract: An abstract." in rendered
    assert "Year: 2023" in rendered
    # No authorships fixture data → "<unknown>"
    assert "Authors: <unknown>" in rendered


def test_format_screening_input_missing_fields_render_unknown() -> None:
    work = _make_work(
        "W1",
        title=None,
        abstract="Has abstract.",
        publication_year=None,
    )
    rendered = format_screening_input(work)
    assert "Title: <unknown>" in rendered
    assert "Year: <unknown>" in rendered


# --- models ---


def test_screening_verdict_defaults() -> None:
    v = ScreeningVerdict(work_id="W1")
    assert v.relevance_score is None
    assert v.reason is None


@pytest.mark.parametrize("extra", ["seed", "bonus_field"])
def test_screening_verdict_extra_fields_forbidden(extra: str) -> None:
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        ScreeningVerdict.model_validate({"work_id": "W1", extra: 1})


def test_screening_meta_serialization() -> None:
    meta = ScreeningMeta(
        run=RunMeta(
            tool="laglitsynth.screening_abstracts.screen",
            run_at="2026-01-01T00:00:00+00:00",
            validation_skipped=0,
        ),
        llm=LlmMeta(
            model="decider",
            base_url=_BASE,
            decisions_url=_BASE + "/decisions",
            prompt_sha256="a" * 64,
        ),
        input_path="data/catalogue-dedup/deduplicated.jsonl",
        input_count=17,
        scored_count=15,
        skipped_count=2,
    )
    data = meta.model_dump()
    assert data["scored_count"] == 15
    assert data["llm_rejected_count"] == 0
    assert "threshold" not in data
    assert "criterion" not in data
    assert "temperature" not in data["llm"]


# --- run() against a mocked decisions endpoint ---


def _run_args(tmp_path: Path, **overrides: Any) -> argparse.Namespace:
    """Build a Namespace mirroring screen.build_subparser defaults."""
    base: dict[str, Any] = {
        "input": tmp_path / "input.jsonl",
        "screening_criteria": _TEST_CRITERIA_SPEC,
        "data_dir": tmp_path,
        "run_id": "test-run-id",
        "base_url": _BASE,
        "model": "decider",
        "api_key": "k",
        "decisions_url": None,
        "max_records": None,
        "dry_run": False,
        "concurrency": 1,
        "config": None,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


def _handler(
    probs: _Probs, bodies: list[dict[str, Any]], seen: list[str]
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        seen.append(str(request.url))
        for phrase, p in probs.items():
            if phrase in body["input"]:
                if p < 0:  # negative marks a malformed reply
                    return httpx.Response(200, text="garbage")
                return httpx.Response(
                    200,
                    json={
                        "model": body["model"],
                        "answers": [{"type": "predicate", "name": "relevant", "probability": p}],
                    },
                )
        return httpx.Response(200, json={"answers": []})

    return handler


class _Run:
    """Result of a mocked run: request bodies, request URLs, client factory."""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.urls: list[str] = []
        self.client_factory: MagicMock = MagicMock()


def _run(
    args: argparse.Namespace,
    probs: _Probs | None = None,
    hook: Callable[[], None] | None = None,
) -> _Run:
    result = _Run()
    handler = _handler(probs or _DEFAULT_PROBS, result.bodies, result.urls)

    def wrapped(request: httpx.Request) -> httpx.Response:
        if hook is not None:
            hook()
        return handler(request)

    def make_client(endpoint: LlmEndpoint) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(wrapped))

    with patch(
        "laglitsynth.screening_abstracts.screen.decisions_client", side_effect=make_client
    ) as factory:
        result.client_factory = factory
        run(args)
    return result


def _out_dir(tmp_path: Path, run_id: str = "test-run-id") -> Path:
    return tmp_path / "screening-abstracts" / run_id


def _three_works(tmp_path: Path) -> None:
    _write_works_jsonl(
        tmp_path / "input.jsonl",
        [
            _make_work("W1", abstract="Ocean currents."),
            _make_work("W2", abstract=None),
            _make_work("W3", abstract="Forest ecology."),
        ],
    )


def _verdicts(tmp_path: Path) -> dict[str, ScreeningVerdict]:
    lines = (_out_dir(tmp_path) / "verdicts.jsonl").read_text().splitlines()
    return {
        v.work_id: v for v in (ScreeningVerdict.model_validate_json(x) for x in lines)
    }


def test_writes_probability_scores_and_no_abstract(tmp_path: Path) -> None:
    _three_works(tmp_path)
    result = _run(_run_args(tmp_path))

    endpoint = result.client_factory.call_args.args[0]
    assert endpoint.api_key == "k"
    assert len(result.bodies) == 2
    assert set(result.urls) == {_BASE + "/decisions"}
    assert {b["model"] for b in result.bodies} == {"decider"}
    question = result.bodies[0]["questions"][0]
    assert question["name"] == "relevant"
    assert question["instructions"].startswith(_DECISION_BLOCK["instructions"])

    verdicts = _verdicts(tmp_path)
    assert set(verdicts) == {"W1", "W2", "W3"}
    assert verdicts["W1"].relevance_score == 73
    assert verdicts["W1"].reason is None
    assert verdicts["W1"].raw_response is not None
    assert verdicts["W3"].relevance_score == 10
    assert verdicts["W2"].reason == "no-abstract"
    assert verdicts["W2"].relevance_score is None
    assert verdicts["W2"].raw_response is None


def test_probability_near_one_rounds_to_100(tmp_path: Path) -> None:
    _write_works_jsonl(tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")])
    _run(_run_args(tmp_path), probs={"Ocean currents.": 0.996})
    assert _verdicts(tmp_path)["W1"].relevance_score == 100


def test_decisions_url_override(tmp_path: Path) -> None:
    _write_works_jsonl(tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")])
    result = _run(_run_args(tmp_path, decisions_url="https://other.example/decide"))
    assert result.urls == ["https://other.example/decide"]


def test_parse_failure_counted_in_meta(tmp_path: Path) -> None:
    _three_works(tmp_path)
    _run(_run_args(tmp_path), probs={"Ocean currents.": -1.0, "Forest ecology.": 0.2})

    verdicts = _verdicts(tmp_path)
    assert verdicts["W1"].reason == "llm-parse-failure"
    assert verdicts["W1"].raw_response == "garbage"
    meta = ScreeningMeta.model_validate_json(
        (_out_dir(tmp_path) / "screening-meta.json").read_text()
    )
    assert meta.scored_count == 1
    assert meta.skipped_count == 2
    assert meta.llm_parse_failure_count == 1


def test_rejected_counted_in_meta(tmp_path: Path) -> None:
    _write_works_jsonl(tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")])

    def make_client(endpoint: LlmEndpoint) -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(400, text="too long"))
        )

    with patch(
        "laglitsynth.screening_abstracts.screen.decisions_client", side_effect=make_client
    ):
        run(_run_args(tmp_path))

    verdict = _verdicts(tmp_path)["W1"]
    assert verdict.reason == "llm-rejected"
    assert verdict.raw_response == "too long"
    meta = json.loads((_out_dir(tmp_path) / "screening-meta.json").read_text())
    assert meta["llm_rejected_count"] == 1


def test_meta_records_endpoint_and_question_hash(tmp_path: Path) -> None:
    _three_works(tmp_path)
    _run(_run_args(tmp_path))

    raw = json.loads((_out_dir(tmp_path) / "screening-meta.json").read_text())
    assert raw["llm"] == {
        "model": "decider",
        "base_url": _BASE,
        "decisions_url": _BASE + "/decisions",
        "prompt_sha256": hashlib.sha256(
            (
                DecisionQuestion.model_validate(_DECISION_BLOCK).canonical_json()
                + "\n"
                + USER_TEMPLATE
            ).encode("utf-8")
        ).hexdigest(),
    }
    meta = ScreeningMeta.model_validate(raw)
    assert meta.input_count == 3
    assert meta.scored_count == 2
    assert meta.skipped_count == 1


def test_run_dry_run(tmp_path: Path) -> None:
    _three_works(tmp_path)
    _run(_run_args(tmp_path, dry_run=True))

    out_dir = _out_dir(tmp_path)
    assert not (out_dir / "verdicts.jsonl").exists()
    assert not (out_dir / "screening-meta.json").exists()
    assert not (out_dir / "config.yaml").exists()


def test_run_writes_meta_before_loop_starts(tmp_path: Path) -> None:
    """Meta is written upfront so a mid-run reader sees the fingerprint."""
    _write_works_jsonl(
        tmp_path / "input.jsonl",
        [_make_work(f"W{i}", abstract="Ocean currents.") for i in range(3)],
    )
    meta_path = _out_dir(tmp_path) / "screening-meta.json"
    captured: dict[str, Any] = {}

    def hook() -> None:
        if "meta" not in captured:
            captured["meta"] = json.loads(meta_path.read_text())

    _run(_run_args(tmp_path), hook=hook)

    upfront = captured["meta"]
    assert upfront["scored_count"] == 0
    assert upfront["llm"]["model"] == "decider"
    assert upfront["input_count"] == 3
    final = json.loads(meta_path.read_text())
    assert final["scored_count"] == 3


def test_run_streaming_append_partial_is_valid(tmp_path: Path) -> None:
    """A killed run leaves a valid partial verdicts.jsonl."""
    _write_works_jsonl(
        tmp_path / "input.jsonl",
        [_make_work(f"W{i}", abstract="Ocean currents.") for i in range(4)],
    )
    calls = {"n": 0}

    def hook() -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated crash")

    with pytest.raises(RuntimeError, match="simulated crash"):
        _run(_run_args(tmp_path), hook=hook)

    lines = (_out_dir(tmp_path) / "verdicts.jsonl").read_text().splitlines()
    assert 0 < len(lines) < 4
    for line in lines:
        json.loads(line)


def test_concurrent_run_scores_every_work(tmp_path: Path) -> None:
    works = [_make_work(f"W{i}", abstract=f"Ocean currents. {i}") for i in range(8)]
    works.append(_make_work("W8", abstract=None))
    _write_works_jsonl(tmp_path / "input.jsonl", works)

    _run(_run_args(tmp_path, concurrency=4))

    verdicts = _verdicts(tmp_path)
    assert set(verdicts) == {f"W{i}" for i in range(9)}
    assert verdicts["W8"].reason == "no-abstract"
    assert all(verdicts[f"W{i}"].relevance_score == 73 for i in range(8))


def test_max_records_counts_all_works(tmp_path: Path) -> None:
    _write_works_jsonl(
        tmp_path / "input.jsonl",
        [
            _make_work("W1", abstract=None),
            _make_work("W2", abstract="Ocean currents."),
            _make_work("W3", abstract=None),
            _make_work("W4", abstract="Ocean currents."),
        ],
    )
    _run(_run_args(tmp_path, max_records=3))
    assert set(_verdicts(tmp_path)) == {"W1", "W2", "W3"}


# --- config.yaml snapshot and --config ---


def test_config_yaml_records_resolved_args_without_key(tmp_path: Path) -> None:
    _three_works(tmp_path)
    _run(_run_args(tmp_path, api_key="secret-key"))

    text = (_out_dir(tmp_path) / "config.yaml").read_text()
    written = yaml.safe_load(text)
    assert written["model"] == "decider"
    assert written["base_url"] == _BASE
    assert written["screening_criteria"]["decision"] == _DECISION_BLOCK
    assert "api_key" not in written
    assert "secret-key" not in text
    assert "backend" not in written
    assert "run_id" not in written
    assert "config" not in written


def test_config_seeds_defaults_explicit_cli_wins(tmp_path: Path) -> None:
    """End-to-end through cli.main: --config seeds defaults; explicit CLI flag wins."""
    cfg = tmp_path / "cfg.yaml"
    cfg.write_text(
        "model: from-config\nbase_url: https://cfg/v1\nmax_records: 75\nconcurrency: 4\n",
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    def fake_run(args: Any) -> None:
        captured["args"] = args

    argv = [
        "screening-abstracts",
        str(tmp_path / "input.jsonl"),
        "--config",
        str(cfg),
        "--model",
        "from-cli",
    ]
    from laglitsynth.cli import main

    with patch("laglitsynth.screening_abstracts.screen.run", fake_run):
        main(argv)

    args = captured["args"]
    assert args.model == "from-cli"  # CLI wins
    assert args.base_url == "https://cfg/v1"  # config seeded
    assert args.max_records == 75
    assert args.concurrency == 4
    assert args.decisions_url is None


def test_backend_flag_is_gone() -> None:
    from laglitsynth.cli import main

    with pytest.raises(SystemExit):
        main(["screening-abstracts", "--backend", "decisions"])


def test_run_dir_printed_to_stderr_at_end(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_works_jsonl(tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")])
    _run(_run_args(tmp_path))

    err = capsys.readouterr().err
    last_line = [line for line in err.splitlines() if line.strip()][-1]
    assert last_line == f"Run dir: {_out_dir(tmp_path)}"


# --- missing inputs exit before any request ---


class TestExitsBeforeRequests:
    def test_missing_api_key_exits_naming_the_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _three_works(tmp_path)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit, match="LLM_API_KEY"):
            _run(_run_args(tmp_path, api_key=None))
        assert not (tmp_path / "screening-abstracts").exists()

    def test_missing_base_url_exits(self, tmp_path: Path) -> None:
        _three_works(tmp_path)
        with pytest.raises(SystemExit, match="--base-url"):
            _run(_run_args(tmp_path, base_url=None))
        assert not (tmp_path / "screening-abstracts").exists()

    def test_inline_criteria_without_decision_block_raises(self, tmp_path: Path) -> None:
        _three_works(tmp_path)
        with pytest.raises(SystemExit, match="no 'decision' block"):
            _run(_run_args(tmp_path, screening_criteria={"id": "x"}))
        assert not _out_dir(tmp_path).exists()

    def test_criteria_file_without_decision_block_names_the_file(
        self, tmp_path: Path
    ) -> None:
        _three_works(tmp_path)
        criteria = tmp_path / "no-decision-criteria.yaml"
        criteria.write_text("id: x\n", encoding="utf-8")
        with pytest.raises(SystemExit, match="no-decision-criteria.yaml"):
            _run(_run_args(tmp_path, screening_criteria=str(criteria)))


# --- conventional input path and run-id from the manifest ---


class TestDataDirResolution:
    def test_omitted_input_reads_the_conventional_dedup_output(
        self, tmp_path: Path
    ) -> None:
        dedup_output = tmp_path / "catalogue-dedup" / "deduplicated.jsonl"
        dedup_output.parent.mkdir()
        _write_works_jsonl(dedup_output, [_make_work("W1", abstract="Ocean currents.")])

        args = _run_args(tmp_path, input=None)
        _run(args)

        assert args.input == dedup_output
        assert list(_verdicts(tmp_path)) == ["W1"]

    def test_missing_dedup_output_exits_before_any_request(self, tmp_path: Path) -> None:
        args = _run_args(tmp_path, input=None)
        with pytest.raises(SystemExit, match="catalogue-dedup") as exc_info:
            _run(args)
        assert "deduplicated.jsonl" in str(exc_info.value)
        assert not (tmp_path / "screening-abstracts").exists()

    def test_omitted_run_id_adopts_the_manifest(self, tmp_path: Path) -> None:
        _write_works_jsonl(
            tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")]
        )
        _write_manifest(tmp_path)

        args = _run_args(tmp_path, run_id=None)
        _run(args)

        assert args.run_id == TEST_RUN_ID
        assert (_out_dir(tmp_path, TEST_RUN_ID) / "verdicts.jsonl").exists()

    def test_explicit_run_id_wins_over_the_manifest(self, tmp_path: Path) -> None:
        _write_works_jsonl(
            tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")]
        )
        _write_manifest(tmp_path)

        args = _run_args(tmp_path, run_id="explicit-run-id")
        _run(args)

        assert (_out_dir(tmp_path, "explicit-run-id") / "verdicts.jsonl").exists()
        assert not _out_dir(tmp_path, TEST_RUN_ID).exists()

    def test_no_manifest_and_no_run_id_exits(self, tmp_path: Path) -> None:
        _write_works_jsonl(
            tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")]
        )
        with pytest.raises(SystemExit, match="manifest-init"):
            _run(_run_args(tmp_path, run_id=None))
        assert not (tmp_path / "screening-abstracts").exists()

    def test_rerun_into_an_existing_run_dir_says_so(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _write_works_jsonl(
            tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")]
        )
        _run(_run_args(tmp_path))
        assert "Reusing run dir" not in capsys.readouterr().err
        _run(_run_args(tmp_path))
        assert "Reusing run dir" in capsys.readouterr().err

    def test_writes_no_manifest(self, tmp_path: Path) -> None:
        _write_works_jsonl(
            tmp_path / "input.jsonl", [_make_work("W1", abstract="Ocean currents.")]
        )
        _run(_run_args(tmp_path))
        assert not (tmp_path / "manifest.json").exists()
