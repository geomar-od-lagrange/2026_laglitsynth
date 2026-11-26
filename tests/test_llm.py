"""Tests for the shared OpenAI-compatible endpoint in ``laglitsynth.llm``."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

from laglitsynth.llm import (
    _DECISIONS_MAX_ATTEMPTS,
    LlmEndpoint,
    LlmFailure,
    add_endpoint_args,
    build_decision_request,
    chat_json,
    decide,
    decisions_client,
    resolve_endpoint,
)
from laglitsynth.prompts import DecisionCriteria, DecisionQuestion, load_decision_question

from conftest import _mock_openai_response

_QUESTION = DecisionQuestion(
    instructions="Is this about ocean particle tracking?",
    criteria=DecisionCriteria(true="Ocean flow tracking.", false="Anything else."),
)
_BASE = "https://llm.example.org/v1"
_ENDPOINT = LlmEndpoint(
    base_url=_BASE, model="decider", api_key="secret", decisions_url=_BASE + "/decisions"
)

_Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _no_sleep() -> Iterator[None]:
    """Retries back off with ``time.sleep``; don't actually wait."""
    with patch("laglitsynth.llm.time.sleep"):
        yield


def _answers(*pairs: tuple[str, float]) -> dict[str, Any]:
    return {
        "model": "decider",
        "answers": [
            {"type": "predicate", "name": name, "probability": p} for name, p in pairs
        ],
        "usage": {"input_tokens": 1},
    }


def _counting(handlers: list[_Handler]) -> tuple[httpx.Client, list[httpx.Request]]:
    """A client whose transport serves ``handlers`` in turn, the last repeated."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handlers[min(len(seen), len(handlers)) - 1](request)

    return httpx.Client(transport=httpx.MockTransport(handler)), seen


def _respond(status: int, payload: Any = None, text: str | None = None) -> _Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if text is not None:
            return httpx.Response(status, text=text)
        return httpx.Response(status, json=payload)

    return handler


def _decide(client: httpx.Client, endpoint: LlmEndpoint = _ENDPOINT) -> Any:
    return decide(
        client, endpoint, input="Title: x\nAbstract: y", name="relevant", question=_QUESTION
    )


# --- decide: request ---


def test_request_goes_to_default_url_with_predicate_body() -> None:
    client, seen = _counting([_respond(200, _answers(("relevant", 0.73)))])
    _decide(client)

    assert len(seen) == 1
    assert str(seen[0].url) == _BASE + "/decisions"
    body = json.loads(seen[0].content)
    assert body == {
        "model": "decider",
        "input": "Title: x\nAbstract: y",
        "questions": [
            {
                "type": "predicate",
                "name": "relevant",
                "instructions": (
                    "Is this about ocean particle tracking?\n\n"
                    "True if: Ocean flow tracking.\n"
                    "False if: Anything else."
                ),
            }
        ],
    }
    assert body == build_decision_request("decider", "Title: x\nAbstract: y", "relevant", _QUESTION)


def test_decisions_url_override_is_used() -> None:
    endpoint = _ENDPOINT.model_copy(update={"decisions_url": "https://other.example/decide"})
    client, seen = _counting([_respond(200, _answers(("relevant", 0.5)))])
    _decide(client, endpoint)
    assert str(seen[0].url) == "https://other.example/decide"


def test_decisions_client_sends_bearer_key() -> None:
    client = decisions_client(_ENDPOINT)
    try:
        assert client.headers["Authorization"] == "Bearer secret"
    finally:
        client.close()


# --- decide: response ---


def test_probability_from_matching_answer() -> None:
    payload = _answers(("other", 0.1), ("relevant", 0.73))
    client, _ = _counting([_respond(200, payload)])
    decision = _decide(client)

    assert decision.probability == 0.73
    assert decision.reason is None
    assert json.loads(decision.raw_response) == payload


def test_no_matching_answer_is_parse_failure() -> None:
    raw = json.dumps(_answers(("other", 0.4)))
    client, seen = _counting([_respond(200, text=raw)])
    decision = _decide(client)

    assert len(seen) == 1
    assert decision.reason == "llm-parse-failure"
    assert decision.probability is None
    assert decision.raw_response == raw


@pytest.mark.parametrize("raw", ["not json", '{"answers": {"relevant": 0.4}}', "{}"])
def test_malformed_body_is_parse_failure(raw: str) -> None:
    client, _ = _counting([_respond(200, text=raw)])
    decision = _decide(client)
    assert decision.reason == "llm-parse-failure"
    assert decision.raw_response == raw


@pytest.mark.parametrize("p", [float("nan"), 1.7, -0.2])
def test_probability_outside_unit_interval_is_parse_failure(p: float) -> None:
    client, _ = _counting([_respond(200, text=json.dumps(_answers(("relevant", p))))])
    assert _decide(client).reason == "llm-parse-failure"


# --- decide: errors ---


@pytest.mark.parametrize("status", [500, 503, 429])
def test_transient_status_is_retried_then_timeout(status: int) -> None:
    client, seen = _counting([_respond(status, text="busy")])
    with patch("laglitsynth.llm.time.sleep") as sleep:
        decision = _decide(client)

    assert len(seen) == _DECISIONS_MAX_ATTEMPTS == 4
    assert sleep.call_count == _DECISIONS_MAX_ATTEMPTS - 1
    assert decision.reason == "llm-timeout"
    assert decision.probability is None
    assert decision.raw_response is None


@pytest.mark.parametrize("status", [500, 429])
def test_transient_status_then_success(status: int) -> None:
    client, seen = _counting([_respond(status), _respond(200, _answers(("relevant", 0.4)))])
    decision = _decide(client)
    assert len(seen) == 2
    assert decision.probability == 0.4


def test_connect_error_is_retried_then_timeout() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    decision = _decide(client)

    assert len(calls) == _DECISIONS_MAX_ATTEMPTS
    assert decision.reason == "llm-timeout"


def test_http_400_is_rejected_with_body() -> None:
    raw = '{"error": "context length exceeded"}'
    client, seen = _counting([_respond(400, text=raw)])
    decision = _decide(client)

    assert len(seen) == 1
    assert decision.reason == "llm-rejected"
    assert decision.probability is None
    assert decision.raw_response == raw


@pytest.mark.parametrize("status", [401, 402, 403, 404])
def test_fatal_status_exits_naming_url(status: int) -> None:
    client, seen = _counting([_respond(status, text='{"error": "no"}')])
    with pytest.raises(SystemExit, match=f"HTTP {status}") as exc_info:
        _decide(client)
    assert _BASE + "/decisions" in str(exc_info.value)
    assert len(seen) == 1


# --- chat_json ---


def _fake_response(status: int) -> httpx.Response:
    return httpx.Response(
        status, text='{"error": "x"}', request=httpx.Request("POST", _BASE + "/chat")
    )


def _chat(side_effect: Any) -> str:
    client = MagicMock()
    client.chat.completions.create.side_effect = side_effect
    return chat_json(client, _ENDPOINT, work_id="W1", system="S", user="U")


def test_chat_sends_model_messages_and_json_mode_only() -> None:
    client = MagicMock()
    client.chat.completions.create.return_value = _mock_openai_response('{"a": 1}')
    content = chat_json(client, _ENDPOINT, work_id="W1", system="S", user="U")

    assert content == '{"a": 1}'
    _, kwargs = client.chat.completions.create.call_args
    assert kwargs == {
        "model": "decider",
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "S"},
            {"role": "user", "content": "U"},
        ],
    }


@pytest.mark.parametrize(
    "exc",
    [
        APITimeoutError(request=MagicMock()),
        APIConnectionError(request=MagicMock()),
        RateLimitError("slow", response=_fake_response(429), body=None),
        InternalServerError("boom", response=_fake_response(500), body=None),
    ],
)
def test_chat_transient_errors_are_timeout(exc: Exception) -> None:
    with pytest.raises(LlmFailure) as info:
        _chat(exc)
    assert info.value.reason == "llm-timeout"
    assert info.value.raw_response is None


def test_chat_bad_request_is_rejected_with_body() -> None:
    with pytest.raises(LlmFailure) as info:
        _chat(BadRequestError("too long", response=_fake_response(400), body=None))
    assert info.value.reason == "llm-rejected"
    assert info.value.raw_response == '{"error": "x"}'


@pytest.mark.parametrize(
    "exc",
    [
        AuthenticationError("bad key", response=_fake_response(401), body=None),
        PermissionDeniedError("no", response=_fake_response(403), body=None),
        NotFoundError("gone", response=_fake_response(404), body=None),
        APIStatusError("pay", response=_fake_response(402), body=None),
    ],
)
def test_chat_fatal_errors_exit_naming_url(exc: Exception) -> None:
    with pytest.raises(SystemExit) as info:
        _chat(exc)
    assert _BASE in str(info.value)


# --- add_endpoint_args / resolve_endpoint ---


def _parse(argv: list[str], *, decisions: bool) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    add_endpoint_args(parser, decisions=decisions)
    return parser.parse_args(argv)


@pytest.fixture
def _no_env_key(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.mark.usefixtures("_no_env_key")
def test_resolve_full_decisions_endpoint() -> None:
    args = _parse(
        ["--base-url", _BASE + "/", "--model", "m", "--api-key", "k"], decisions=True
    )
    endpoint = resolve_endpoint(args)
    assert endpoint == LlmEndpoint(
        base_url=_BASE + "/", model="m", api_key="k", decisions_url=_BASE + "/decisions"
    )
    assert args.concurrency == 1


@pytest.mark.usefixtures("_no_env_key")
def test_resolve_decisions_url_override() -> None:
    args = _parse(
        ["--base-url", _BASE, "--model", "m", "--api-key", "k", "--decisions-url", "https://d"],
        decisions=True,
    )
    assert resolve_endpoint(args).decisions_url == "https://d"


@pytest.mark.usefixtures("_no_env_key")
def test_resolve_chat_endpoint_has_no_decisions_url() -> None:
    args = _parse(["--base-url", _BASE, "--model", "m", "--api-key", "k"], decisions=False)
    assert resolve_endpoint(args).decisions_url is None
    assert not hasattr(args, "decisions_url")


@pytest.mark.usefixtures("_no_env_key")
@pytest.mark.parametrize(
    ("argv", "missing"),
    [
        (["--model", "m", "--api-key", "k"], "--base-url"),
        (["--base-url", _BASE, "--api-key", "k"], "--model"),
        (["--base-url", _BASE, "--model", "m"], "LLM_API_KEY"),
    ],
)
def test_resolve_exits_naming_what_is_missing(argv: list[str], missing: str) -> None:
    with pytest.raises(SystemExit, match=missing):
        resolve_endpoint(_parse(argv, decisions=True))


def test_key_from_environment_before_dotenv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("LLM_API_KEY=from-dotenv\n", encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY", "from-env")
    args = _parse(["--base-url", _BASE, "--model", "m"], decisions=False)
    assert resolve_endpoint(args).api_key == "from-env"


def test_key_from_dotenv_when_environment_unset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    (tmp_path / ".env").write_text("LLM_API_KEY=from-dotenv\n", encoding="utf-8")
    args = _parse(["--base-url", _BASE, "--model", "m"], decisions=False)
    assert resolve_endpoint(args).api_key == "from-dotenv"


def test_flag_key_wins_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_API_KEY", "from-env")
    args = _parse(["--base-url", _BASE, "--model", "m", "--api-key", "flag"], decisions=False)
    assert resolve_endpoint(args).api_key == "flag"


def test_llm_meta_carries_urls() -> None:
    meta = _ENDPOINT.llm_meta("a" * 64)
    assert meta.model == "decider"
    assert meta.base_url == _BASE
    assert meta.decisions_url == _BASE + "/decisions"
    assert meta.prompt_sha256 == "a" * 64


# --- DecisionQuestion / load_decision_question ---


def test_load_without_decision_block_names_the_spec(tmp_path: Path) -> None:
    path = tmp_path / "criteria.yaml"
    path.write_text("id: x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="criteria.yaml"):
        load_decision_question(path)


def test_load_inline_spec_without_decision_raises() -> None:
    with pytest.raises(ValueError, match="no 'decision' block"):
        load_decision_question({"id": "x"})


def test_load_valid_block_returns_model() -> None:
    spec = {
        "decision": {
            "instructions": "Q?",
            "criteria": {"true": "yes case", "false": "no case"},
        },
    }
    question = load_decision_question(spec)
    assert question == DecisionQuestion(
        instructions="Q?", criteria=DecisionCriteria(true="yes case", false="no case")
    )


def test_load_shipped_example_criteria() -> None:
    example = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "screening-criteria"
        / "lagrangian-oceanography.yaml"
    )
    question = load_decision_question(example)
    assert question.instructions
    assert question.criteria.true
    assert question.criteria.false


def test_canonical_json_is_stable_under_key_order() -> None:
    a = DecisionQuestion.model_validate(
        {"instructions": "Q?", "criteria": {"true": "t", "false": "f"}}
    )
    b = DecisionQuestion.model_validate(
        {"criteria": {"false": "f", "true": "t"}, "instructions": "Q?"}
    )
    assert a.canonical_json() == b.canonical_json()
    assert json.loads(a.canonical_json()) == {
        "criteria": {"false": "f", "true": "t"},
        "instructions": "Q?",
    }
