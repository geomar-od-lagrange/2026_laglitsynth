"""Decisions backend for abstract screening.

Scores each work with a hosted decisions model (TypeSafe Jev and the like)
through OpenRouter's ``/api/alpha/decisions`` endpoint. The request carries
the work listing as ``state`` and one ``noul`` (yes/no) question built from
the criteria file's ``decision`` block; the answer is a probability that the
work is relevant, stored as ``relevance_score = round(100 * p)``. There is
no free-text justification, so ``reason`` is None; the raw answers JSON is
kept in ``raw_response`` so the exact probability survives.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

from laglitsynth.prompts import DecisionQuestion
from laglitsynth.screening_abstracts.models import ScreeningVerdict

DEFAULT_DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
QUESTION_KEY = "relevant"
_TIMEOUT_SECONDS = 60.0
_MAX_ATTEMPTS = 4
_BACKOFF_SECONDS = 2.0

logger = logging.getLogger(__name__)


def decisions_client(api_key: str) -> httpx.Client:
    return httpx.Client(
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=_TIMEOUT_SECONDS,
    )


def build_request(model: str, state: str, question: DecisionQuestion) -> dict[str, Any]:
    return {
        "model": model,
        "state": state,
        "questions": {
            QUESTION_KEY: {
                "type": "noul",
                "instructions": question.instructions,
                "criteria": {"true": question.criteria.true, "false": question.criteria.false},
            }
        },
    }


def _timeout_verdict(work_id: str) -> ScreeningVerdict:
    return ScreeningVerdict(
        work_id=work_id, relevance_score=None, reason="llm-timeout", seed=None, raw_response=None
    )


def classify_decision(
    work_id: str,
    state: str,
    question: DecisionQuestion,
    *,
    model: str,
    url: str,
    client: httpx.Client,
) -> ScreeningVerdict:
    """POST one decision and compose the verdict.

    Transport errors and non-2xx responses are retried with a fixed
    backoff; when attempts run out the ``llm-timeout`` sentinel is
    returned. A 2xx body without ``answers.relevant.noul`` returns the
    ``llm-parse-failure`` sentinel with the body attached.
    """
    body = build_request(model, state, question)
    response: httpx.Response | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = client.post(url, json=body)
        except httpx.HTTPError as exc:
            logger.warning("decisions request failed for %s (%d): %s", work_id, attempt, exc)
            response = None
        else:
            if response.status_code < 500 and response.status_code != 429:
                break
            logger.warning(
                "decisions HTTP %d for %s (%d)", response.status_code, work_id, attempt
            )
        if attempt < _MAX_ATTEMPTS:
            time.sleep(_BACKOFF_SECONDS * attempt)
    if response is None or response.status_code >= 500 or response.status_code == 429:
        return _timeout_verdict(work_id)
    if response.status_code in (401, 403):
        # A bad key fails every work the same way; stop instead of writing
        # a run full of parse failures.
        raise SystemExit(
            f"decisions endpoint refused the API key (HTTP {response.status_code}): "
            f"{response.text[:200]}"
        )

    text = response.text
    try:
        answer = response.json()["answers"][QUESTION_KEY]
        probability = float(answer["noul"])
        if not 0.0 <= probability <= 1.0:  # also rejects NaN
            raise ValueError(f"probability out of range: {probability}")
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("decisions parse failure for %s: %s", work_id, exc)
        return ScreeningVerdict(
            work_id=work_id,
            relevance_score=None,
            reason="llm-parse-failure",
            seed=None,
            raw_response=text,
        )
    return ScreeningVerdict(
        work_id=work_id,
        relevance_score=round(100 * probability),
        reason=None,
        seed=None,
        raw_response=json.dumps(answer, sort_keys=True),
    )
