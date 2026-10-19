"""Tests for the decisions backend of abstract screening."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any
from unittest.mock import patch

import httpx
import pytest

from laglitsynth.prompts import DecisionCriteria, DecisionQuestion, load_decision_question
from laglitsynth.screening_abstracts.decisions import (
    _MAX_ATTEMPTS,
    DEFAULT_DECISIONS_URL,
    QUESTION_KEY,
    build_request,
    classify_decision,
    decisions_client,
)

_QUESTION = DecisionQuestion(
    instructions="Is this about ocean particle tracking?",
    criteria=DecisionCriteria(true="Ocean flow tracking.", false="Anything else."),
)

_Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _no_sleep() -> Iterator[None]:
    """Retries back off with ``time.sleep``; don't actually wait."""
    with patch("laglitsynth.screening_abstracts.decisions.time.sleep"):
        yield


def _answer_body(answer: dict[str, Any]) -> dict[str, Any]:
    return {"answers": {QUESTION_KEY: answer}}


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


def _classify(client: httpx.Client) -> Any:
    return classify_decision(
        "W1",
        "Title: x\nAbstract: y",
        _QUESTION,
        model="typesafe/jev",
        url=DEFAULT_DECISIONS_URL,
        client=client,
    )


# --- build_request ---


def test_build_request_shape() -> None:
    body = build_request("typesafe/jev", "the state", _QUESTION)
    assert body == {
        "model": "typesafe/jev",
        "state": "the state",
        "questions": {
            "relevant": {
                "type": "noul",
                "instructions": "Is this about ocean particle tracking?",
                "criteria": {"true": "Ocean flow tracking.", "false": "Anything else."},
            }
        },
    }


# --- classify_decision ---


def test_probability_maps_to_score() -> None:
    answer = {"noul": 0.73, "extra": "kept"}
    client, seen = _counting([_respond(200, _answer_body(answer))])
    verdict = _classify(client)

    assert len(seen) == 1
    sent = json.loads(seen[0].content)
    assert sent == build_request("typesafe/jev", "Title: x\nAbstract: y", _QUESTION)
    assert str(seen[0].url) == DEFAULT_DECISIONS_URL
    assert verdict.work_id == "W1"
    assert verdict.relevance_score == 73
    assert verdict.reason is None
    assert verdict.seed is None
    assert verdict.raw_response == json.dumps(answer, sort_keys=True)


def test_probability_near_one_rounds_to_100() -> None:
    client, _ = _counting([_respond(200, _answer_body({"noul": 0.996}))])
    assert _classify(client).relevance_score == 100


def test_body_without_noul_is_parse_failure() -> None:
    raw = '{"answers": {"relevant": {"other": 1}}}'
    client, seen = _counting([_respond(200, text=raw)])
    verdict = _classify(client)

    assert len(seen) == 1
    assert verdict.reason == "llm-parse-failure"
    assert verdict.relevance_score is None
    assert verdict.raw_response == raw


def test_non_json_body_is_parse_failure() -> None:
    client, _ = _counting([_respond(200, text="not json")])
    verdict = _classify(client)
    assert verdict.reason == "llm-parse-failure"
    assert verdict.raw_response == "not json"


def test_http_500_on_every_attempt_is_timeout() -> None:
    client, seen = _counting([_respond(500, text="boom")])
    with patch("laglitsynth.screening_abstracts.decisions.time.sleep") as sleep:
        verdict = _classify(client)

    assert len(seen) == _MAX_ATTEMPTS == 4
    assert sleep.call_count == _MAX_ATTEMPTS - 1
    assert verdict.reason == "llm-timeout"
    assert verdict.relevance_score is None
    assert verdict.raw_response is None


def test_http_429_is_retried() -> None:
    client, seen = _counting([_respond(429), _respond(200, _answer_body({"noul": 0.2}))])
    verdict = _classify(client)
    assert len(seen) == 2
    assert verdict.relevance_score == 20


def test_500_then_200_succeeds_on_second_attempt() -> None:
    client, seen = _counting([_respond(500), _respond(200, _answer_body({"noul": 0.4}))])
    verdict = _classify(client)

    assert len(seen) == 2
    assert verdict.relevance_score == 40
    assert verdict.reason is None


def test_connect_error_is_timeout() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        raise httpx.ConnectError("refused", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    verdict = _classify(client)

    assert len(calls) == _MAX_ATTEMPTS
    assert verdict.reason == "llm-timeout"


def test_http_400_is_not_retried_and_is_parse_failure() -> None:
    raw = '{"error": "bad request"}'
    client, seen = _counting([_respond(400, text=raw)])
    verdict = _classify(client)

    assert len(seen) == 1
    assert verdict.reason == "llm-parse-failure"
    assert verdict.raw_response == raw


def test_http_401_stops_the_run() -> None:
    client, seen = _counting([_respond(401, text='{"error": "bad key"}')])
    with pytest.raises(SystemExit, match="HTTP 401"):
        _classify(client)
    assert len(seen) == 1


@pytest.mark.parametrize("noul", [float("nan"), 1.7, -0.2])
def test_probability_outside_unit_interval_is_parse_failure(noul: float) -> None:
    body = json.dumps(_answer_body({"type": "noul", "noul": noul}))
    client, _ = _counting([_respond(200, text=body)])
    verdict = _classify(client)

    assert verdict.reason == "llm-parse-failure"
    assert verdict.relevance_score is None


def test_decisions_client_sends_bearer_key() -> None:
    client = decisions_client("secret")
    try:
        assert client.headers["Authorization"] == "Bearer secret"
    finally:
        client.close()


# --- DecisionQuestion / load_decision_question ---


def test_load_without_decision_block_names_the_spec(tmp_path: Any) -> None:
    path = tmp_path / "criteria.yaml"
    path.write_text("system_prompt: hi\n", encoding="utf-8")
    with pytest.raises(ValueError, match="criteria.yaml"):
        load_decision_question(path)


def test_load_inline_spec_without_decision_raises() -> None:
    with pytest.raises(ValueError, match="no 'decision' block"):
        load_decision_question({"system_prompt": "hi"})


def test_load_valid_block_returns_model() -> None:
    spec = {
        "system_prompt": "hi",
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
    from pathlib import Path

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


def test_render_contains_instructions_and_both_criteria() -> None:
    rendered = _QUESTION.render()
    assert _QUESTION.instructions in rendered
    assert f"true: {_QUESTION.criteria.true}" in rendered
    assert f"false: {_QUESTION.criteria.false}" in rendered


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
