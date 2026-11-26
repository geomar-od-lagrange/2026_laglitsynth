"""One OpenAI-compatible LLM endpoint for every LLM stage.

The three LLM stages register the same flags through ``add_endpoint_args``
and turn them into a frozen ``LlmEndpoint`` with ``resolve_endpoint``.
Chat stages build an ``openai.OpenAI`` with ``chat_client`` and call
``chat_json``; decisions stages build an ``httpx.Client`` with
``decisions_client`` and call ``decide``.

Errors fall in three classes. Transient errors (transport failures, 5xx,
429) become the ``llm-timeout`` sentinel after retries. Errors that would
fail every work the same way (401, 402, 403, 404) exit naming the URL. Any
other rejection becomes the ``llm-rejected`` sentinel carrying the error
body, and an unparseable 2xx becomes ``llm-parse-failure`` with the body
attached.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from typing import Any

import httpx
from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel, ConfigDict

from laglitsynth.concurrency import positive_int
from laglitsynth.dotenv import load_env_var
from laglitsynth.models import LlmMeta
from laglitsynth.prompts import DecisionQuestion

API_KEY_VAR = "LLM_API_KEY"

# Values stage 8's --reasoning-effort accepts.
REASONING_EFFORTS = ("low", "medium", "high")

_DECISIONS_TIMEOUT_SECONDS = 300.0
_DECISIONS_MAX_ATTEMPTS = 4
_DECISIONS_BACKOFF_SECONDS = 2.0
_CHAT_MAX_RETRIES = 3

# Statuses that fail every work the same way: stop instead of writing a
# run full of sentinels.
_FATAL_STATUSES = frozenset({401, 402, 403, 404})

logger = logging.getLogger(__name__)


class LlmEndpoint(BaseModel):
    """The resolved endpoint one LLM stage talks to."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    base_url: str  # OpenAI-compatible base, including /v1
    model: str
    api_key: str
    decisions_url: str | None  # None for chat-only stages
    reasoning_effort: str | None = None  # chat only; None sends no effort

    def llm_meta(self, prompt_sha256: str) -> LlmMeta:
        return LlmMeta(
            model=self.model,
            base_url=self.base_url,
            decisions_url=self.decisions_url,
            reasoning_effort=self.reasoning_effort,
            prompt_sha256=prompt_sha256,
        )


class Decision(BaseModel):
    """One decisions-endpoint answer, or a sentinel in ``reason``."""

    model_config = ConfigDict(extra="forbid")
    probability: float | None
    reason: str | None
    raw_response: str | None


class LlmFailure(Exception):
    """A per-work chat failure that becomes a sentinel row."""

    def __init__(self, reason: str, raw_response: str | None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.raw_response = raw_response


def add_endpoint_args(parser: argparse.ArgumentParser, *, decisions: bool) -> None:
    """Register ``--base-url``, ``--model``, ``--api-key`` and ``--concurrency``.

    ``decisions=True`` also registers ``--decisions-url``. ``--base-url``
    and ``--model`` have no default and are checked by ``resolve_endpoint``,
    since argparse ``required=True`` would ignore ``--config`` defaults.
    """
    parser.add_argument(
        "--base-url",
        default=None,
        help="OpenAI-compatible API base URL, including /v1. Required.",
    )
    parser.add_argument("--model", default=None, help="Model name. Required.")
    parser.add_argument(
        "--api-key",
        default=None,
        help=(
            f"API key for the endpoint (default: ${API_KEY_VAR}, else "
            f"{API_KEY_VAR} in .env). Never written to config.yaml."
        ),
    )
    if decisions:
        parser.add_argument(
            "--decisions-url",
            default=None,
            help="Decisions endpoint (default: <base-url>/decisions).",
        )
    parser.add_argument(
        "--concurrency",
        type=positive_int,
        default=1,
        help="In-flight LLM requests (default: 1). See docs/interfaces.md.",
    )


def resolve_endpoint(args: argparse.Namespace) -> LlmEndpoint:
    """Build the endpoint from parsed args; exit naming whatever is missing.

    The API key is ``--api-key``, else ``$LLM_API_KEY``, else
    ``LLM_API_KEY`` in ``.env``; the ``.env`` read is announced on stderr.
    The decisions URL is resolved only when the stage registered
    ``--decisions-url``; it defaults to ``<base-url>/decisions``. The
    reasoning effort is read only when the stage registered
    ``--reasoning-effort`` (stage 8).
    """
    base_url: str | None = args.base_url
    if not base_url:
        raise SystemExit("--base-url is required (the OpenAI-compatible base, including /v1)")
    model: str | None = args.model
    if not model:
        raise SystemExit("--model is required")
    api_key: str | None = args.api_key or os.environ.get(API_KEY_VAR)
    if not api_key:
        api_key = load_env_var(API_KEY_VAR)
        if api_key:
            print(f"Loaded {API_KEY_VAR} from .env", file=sys.stderr)
    if not api_key:
        raise SystemExit(
            f"no API key: pass --api-key, export {API_KEY_VAR} or add {API_KEY_VAR}=... to .env"
        )
    decisions_url: str | None = None
    if "decisions_url" in vars(args):
        decisions_url = args.decisions_url or base_url.rstrip("/") + "/decisions"
    reasoning_effort: str | None = vars(args).get("reasoning_effort")
    return LlmEndpoint(
        base_url=base_url,
        model=model,
        api_key=api_key,
        decisions_url=decisions_url,
        reasoning_effort=reasoning_effort,
    )


# --- chat ---


def chat_client(endpoint: LlmEndpoint, timeout: float) -> OpenAI:
    return OpenAI(
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        timeout=timeout,
        max_retries=_CHAT_MAX_RETRIES,
    )


def _fatal(url: str, status: int, body: str) -> SystemExit:
    return SystemExit(f"LLM endpoint {url} refused the request (HTTP {status}): {body[:200]}")


def chat_json(
    client: OpenAI, endpoint: LlmEndpoint, *, work_id: str, system: str, user: str
) -> str:
    """Send one JSON-mode chat completion and return the message content.

    Raises ``LlmFailure`` for per-work failures (``llm-timeout``,
    ``llm-rejected``) and ``SystemExit`` naming the base URL for errors
    that would fail every work. ``reasoning_effort`` is sent only when the
    endpoint sets one; otherwise the provider default applies.
    """
    extra: dict[str, Any] = {}
    if endpoint.reasoning_effort is not None:
        extra["reasoning_effort"] = endpoint.reasoning_effort
    try:
        response = client.chat.completions.create(
            model=endpoint.model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            **extra,
        )
    except (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError) as exc:
        logger.warning("LLM timeout for %s: %s", work_id, exc)
        raise LlmFailure("llm-timeout", None) from exc
    except (AuthenticationError, PermissionDeniedError, NotFoundError) as exc:
        raise _fatal(endpoint.base_url, exc.status_code, exc.response.text) from exc
    except BadRequestError as exc:
        logger.warning("LLM rejected request for %s: %s", work_id, exc)
        raise LlmFailure("llm-rejected", exc.response.text) from exc
    except APIStatusError as exc:
        if exc.status_code in _FATAL_STATUSES:
            raise _fatal(endpoint.base_url, exc.status_code, exc.response.text) from exc
        logger.warning("LLM rejected request for %s: %s", work_id, exc)
        raise LlmFailure("llm-rejected", exc.response.text) from exc
    return response.choices[0].message.content or "{}"


# --- decisions ---


def decisions_client(
    endpoint: LlmEndpoint, timeout: float = _DECISIONS_TIMEOUT_SECONDS
) -> httpx.Client:
    return httpx.Client(
        headers={"Authorization": f"Bearer {endpoint.api_key}"},
        timeout=timeout,
    )


def build_decision_request(
    model: str, input: str, name: str, question: DecisionQuestion
) -> dict[str, Any]:
    """OpenAI's Decisions request body: one predicate question, nothing else."""
    return {
        "model": model,
        "input": input,
        "questions": [
            {"type": "predicate", "name": name, "instructions": question.render()},
        ],
    }


def _parse_probability(body: Any, name: str) -> float:
    answers = body["answers"]
    if not isinstance(answers, list):
        raise TypeError("answers is not a list")
    for answer in answers:
        if isinstance(answer, dict) and answer.get("name") == name:
            probability = float(answer["probability"])
            if not 0.0 <= probability <= 1.0:  # also rejects NaN
                raise ValueError(f"probability out of range: {probability}")
            return probability
    raise KeyError(f"no answer named {name!r}")


def decide(
    client: httpx.Client,
    endpoint: LlmEndpoint,
    *,
    input: str,
    name: str,
    question: DecisionQuestion,
) -> Decision:
    """POST one decision to ``endpoint.decisions_url`` and read its probability.

    Transport errors, 5xx and 429 are retried with a linear backoff and give
    ``llm-timeout`` when attempts run out. 401, 402, 403 and 404 exit naming
    the URL. Any other non-2xx gives ``llm-rejected`` with the error body; a
    2xx without a valid probability for ``name`` gives ``llm-parse-failure``.
    """
    url = endpoint.decisions_url
    if url is None:
        raise ValueError("endpoint has no decisions_url")
    body = build_decision_request(endpoint.model, input, name, question)
    response: httpx.Response | None = None
    for attempt in range(1, _DECISIONS_MAX_ATTEMPTS + 1):
        try:
            response = client.post(url, json=body)
        except httpx.HTTPError as exc:
            logger.warning("decisions request failed (attempt %d): %s", attempt, exc)
            response = None
        else:
            if response.status_code < 500 and response.status_code != 429:
                break
            logger.warning("decisions HTTP %d (attempt %d)", response.status_code, attempt)
        if attempt < _DECISIONS_MAX_ATTEMPTS:
            time.sleep(_DECISIONS_BACKOFF_SECONDS * attempt)
    if response is None or response.status_code >= 500 or response.status_code == 429:
        return Decision(probability=None, reason="llm-timeout", raw_response=None)
    text = response.text
    if response.status_code in _FATAL_STATUSES:
        raise _fatal(url, response.status_code, text)
    if not response.is_success:
        logger.warning("decisions HTTP %d rejected", response.status_code)
        return Decision(probability=None, reason="llm-rejected", raw_response=text)
    try:
        probability = _parse_probability(json.loads(text), name)
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("decisions parse failure: %s", exc)
        return Decision(probability=None, reason="llm-parse-failure", raw_response=text)
    return Decision(probability=probability, reason=None, raw_response=text)
