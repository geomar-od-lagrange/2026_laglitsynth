"""DOI -> abstract source clients for the abstract-lookup stage.

Three thin, typed ``httpx`` clients (Semantic Scholar, OpenAlex, Crossref),
each taking an injected ``httpx.Client`` so tests can mount an
``httpx.MockTransport`` and avoid the network entirely.  Each returns the
plain-text abstract or ``None`` when the source has none.  Plain text at
ingest: the OpenAlex inverted index is reconstructed and Crossref's JATS-XML
is tag-stripped before returning.
"""

from __future__ import annotations

import logging
import re
import time
from urllib.parse import quote

import httpx
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

_DOI_PREFIX_RE = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)
_JATS_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# Bound on retry waits so a misbehaving Retry-After header cannot stall a run.
MAX_BACKOFF_SECONDS = 30.0
# Number of attempts when a request is rate-limited (429) before giving up.
MAX_RATE_LIMIT_RETRIES = 3


def _normalise_doi(doi: str) -> str:
    """Strip the standard DOI URL prefix; return the bare DOI."""
    return _DOI_PREFIX_RE.sub("", doi.strip())


def reconstruct_inverted_index(
    inverted_index: dict[str, list[int]] | None,
) -> str | None:
    """Reconstruct plain-text abstract from an OpenAlex inverted index.

    Mirrors ``catalogue_fetch.fetch._reconstruct_abstract`` so OpenAlex
    abstracts read the same here as at fetch time.
    """
    if not inverted_index:
        return None
    word_positions: list[tuple[int, str]] = []
    for word, positions in inverted_index.items():
        for pos in positions:
            word_positions.append((pos, word))
    word_positions.sort()
    return " ".join(word for _, word in word_positions)


def strip_jats(abstract: str | None) -> str | None:
    """Strip JATS-XML tags from a Crossref abstract, returning plain text.

    Crossref ``message.abstract`` is JATS-XML (``<jats:p>...</jats:p>`` etc.).
    Tags are removed and whitespace collapsed.  Returns ``None`` for missing
    input or when nothing but markup remains.
    """
    if abstract is None:
        return None
    text = _JATS_TAG_RE.sub(" ", abstract)
    text = _WS_RE.sub(" ", text).strip()
    return text or None


def _wait_for_rate_limit(response: httpx.Response, attempt: int) -> None:
    """Sleep before retrying a 429, honouring Retry-After within a bound."""
    retry_after = response.headers.get("Retry-After")
    delay: float
    if retry_after is not None:
        try:
            delay = float(retry_after)
        except ValueError:
            delay = 2.0 ** attempt
    else:
        delay = 2.0 ** attempt
    delay = min(delay, MAX_BACKOFF_SECONDS)
    logger.debug("Rate-limited (429); sleeping %.1fs before retry", delay)
    time.sleep(delay)


def _get_with_backoff(
    client: httpx.Client,
    url: str,
    *,
    params: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response | None:
    """GET ``url``, retrying bounded times on 429; ``None`` on 404.

    Returns the response for any other status so the caller can
    ``raise_for_status`` or inspect the body.  A persistent 429 after the
    retry budget is exhausted returns the final 429 response.
    """
    response: httpx.Response | None = None
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        response = client.get(
            url, params=params, headers=headers, follow_redirects=True
        )
        if response.status_code == 404:
            return None
        if response.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
            _wait_for_rate_limit(response, attempt)
            continue
        return response
    return response


class _SemanticScholarPaper(BaseModel):
    model_config = ConfigDict(extra="ignore")
    abstract: str | None = None


def fetch_semantic_scholar(
    doi: str,
    *,
    client: httpx.Client,
    email: str,
    api_key: str | None = None,
) -> str | None:
    """Look up an abstract on Semantic Scholar Graph by DOI.

    ``GET /graph/v1/paper/DOI:{doi}?fields=abstract`` returns a plain-text
    abstract.  ``email`` is sent as a polite contact; an optional ``api_key``
    (header ``x-api-key``) raises rate limits.  Returns ``None`` on 404, a
    null/empty abstract, or any non-2xx response.
    """
    bare = _normalise_doi(doi)
    url = (
        "https://api.semanticscholar.org/graph/v1/paper/"
        f"DOI:{quote(bare, safe='')}"
    )
    headers: dict[str, str] = {"User-Agent": f"laglitsynth (mailto:{email})"}
    if api_key is not None:
        headers["x-api-key"] = api_key
    response = _get_with_backoff(
        client, url, params={"fields": "abstract"}, headers=headers
    )
    if response is None or response.status_code >= 300:
        return None
    paper = _SemanticScholarPaper.model_validate(response.json())
    abstract = paper.abstract
    if abstract is None:
        return None
    abstract = abstract.strip()
    return abstract or None


class _OpenAlexWork(BaseModel):
    model_config = ConfigDict(extra="ignore")
    abstract_inverted_index: dict[str, list[int]] | None = None


def fetch_openalex(
    doi: str,
    *,
    client: httpx.Client,
    email: str,
    api_key: str | None = None,
) -> str | None:
    """Look up an abstract on OpenAlex by DOI and reconstruct it to text.

    Uses the public ``/works/doi:{doi}`` endpoint selecting only
    ``abstract_inverted_index``.  ``email`` is sent as the ``mailto`` polite
    parameter.  Returns ``None`` on 404, a missing index, or any non-2xx
    response.
    """
    bare = _normalise_doi(doi)
    url = f"https://api.openalex.org/works/doi:{quote(bare, safe='')}"
    params: dict[str, str] = {
        "select": "abstract_inverted_index",
        "mailto": email,
    }
    if api_key is not None:
        params["api_key"] = api_key
    headers = {"User-Agent": f"laglitsynth (mailto:{email})"}
    response = _get_with_backoff(client, url, params=params, headers=headers)
    if response is None or response.status_code >= 300:
        return None
    work = _OpenAlexWork.model_validate(response.json())
    return reconstruct_inverted_index(work.abstract_inverted_index)


class _CrossrefMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    abstract: str | None = None


class _CrossrefEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: _CrossrefMessage


def fetch_crossref(
    doi: str,
    *,
    client: httpx.Client,
    email: str,
    api_key: str | None = None,
) -> str | None:
    """Look up an abstract on Crossref by DOI and strip its JATS markup.

    ``message.abstract`` is JATS-XML; tags are stripped to plain text.
    ``email`` is sent as the ``mailto`` polite parameter.  ``api_key`` is
    unused (Crossref has no key) and accepted only for a uniform source
    signature.  Returns ``None`` on 404, a missing abstract, or any non-2xx
    response.
    """
    del api_key  # Crossref takes no key; uniform signature only.
    bare = _normalise_doi(doi)
    url = f"https://api.crossref.org/works/{quote(bare, safe='')}"
    headers = {"User-Agent": f"laglitsynth (mailto:{email})"}
    response = _get_with_backoff(
        client, url, params={"mailto": email}, headers=headers
    )
    if response is None or response.status_code >= 300:
        return None
    envelope = _CrossrefEnvelope.model_validate(response.json())
    return strip_jats(envelope.message.abstract)
