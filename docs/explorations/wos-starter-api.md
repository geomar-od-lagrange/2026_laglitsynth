# Using the Web of Science Starter API as a catalogue source

Desk research (May 2026) into the [Web of Science Starter
API](https://developer.clarivate.com/apis/wos-starter) as a possible
catalogue source for [stage 1
(`catalogue-fetch`)](../../src/laglitsynth/catalogue_fetch/fetch.py),
alongside or instead of OpenAlex. The "swap or merge a second catalogue"
option was anticipated in the PR #3 review (see
[plans/done/pr3-review-comments.md](../../plans/done/pr3-review-comments.md)
and the [glossary `work` entry](../glossary.md)).

Findings combine Clarivate's developer portal and the official
auto-generated client repo
([`clarivate/wosstarter_python_client`](https://github.com/clarivate/wosstarter_python_client))
with **live calls against our key** (May 2026) — see the [Live
verification](#live-verification-and-openalex-comparison-may-2026) section
below, which confirms auth, the `hits` envelope, the camelCase wire
format, the rate-limit tier, and the absence of abstracts.

## Two facts that decide the design

1. **The Starter API does not return abstracts.** The `Document` schema
   has no abstract field anywhere; the topic tag `TS` *searches over* the
   abstract but never returns its text. Our pipeline depends on abstracts:
   the [`work`](../glossary.md) definition includes one and [stage 4
   (`screening-abstracts`)](../../src/laglitsynth/screening_abstracts/)
   screens on them. So the Starter API **cannot replace OpenAlex at stage
   1** — it would feed screening empty abstracts. Abstracts come only from
   the separate, paid **Web of Science Expanded API**
   ([developer.clarivate.com/apis/wos](https://developer.clarivate.com/apis/wos)),
   a different product and key.

2. **No Web of Science API returns PDFs or full text.** Both Starter and
   Expanded are bibliographic-metadata services. The most Starter gives
   toward retrieval is `identifiers.doi` and a `links.record` URL into the
   WoS web platform; [stage 6
   (`fulltext-retrieval`)](../../src/laglitsynth/fulltext_retrieval/)
   would still resolve DOIs via the publisher / Unpaywall / a library link
   resolver exactly as from OpenAlex DOIs. **WoS adds nothing for PDF
   retrieval.**

The realistic role for a Starter key is therefore a **second metadata
catalogue / cross-check** (quality-controlled journal list, times-cited
counts), merged with OpenAlex which remains the abstract source — not a
stage-1 replacement and not a retrieval source.

## Authentication

API key in the `X-ApiKey` HTTP header. No OAuth or bearer token; the key
alone authenticates every request. All endpoints are `GET`, responses
JSON.

```bash
export WOS_STARTER_API_KEY="your-api-key"
```

Base URL:

```
https://api.clarivate.com/apis/wos-starter/v1
```

## Endpoints

| Path | Returns |
|------|---------|
| `GET /documents` | Search/filter documents → `DocumentsList` (array of `Document` + `metadata`). |
| `GET /documents/{uid}` | One document by WoS UID (e.g. `WOS:000267144200002`) → `Document`. |
| `GET /journals` | Query journals by ISSN → `JournalsList`. |
| `GET /journals/{id}` | One journal by ID → `Journal`. |

Only `/documents` and `/documents/{uid}` are relevant to the catalogue.

## Query syntax

Queries use the WoS Advanced Search field-tag language, URL-encoded in the
`q` parameter. Starter supports a subset of the tags (Expanded supports
all). Most useful tags:

| Tag | Meaning |
|-----|---------|
| `TS` | Topic — searches Title, Abstract, Author Keywords, Keywords Plus |
| `TI` | Title |
| `AU` | Author |
| `PY` | Year published (supports ranges, e.g. `PY=2020-2023`) |
| `DO` | DOI |
| `SO` | Source / journal title |
| `IS` | ISSN or ISBN |
| `UT` | Accession Number (UID) |
| `DT` | Document Type |
| `OG` | Organization (preferred names + variants) |

Boolean operators `AND OR NOT NEAR SAME` and parentheses work. Examples:

```
q=TS=(ocean eddies AND "Lagrangian")
q=AU=Smith AND PY=2020-2023
q=DO=10.1029/2019JC015533
```

Other `/documents` parameters:

- `db` — database, default `WOS` (Core Collection); also `MEDLINE`,
  `BIOSIS`, `DRCI`, … or `WOK` (all databases).
- `sort_field` — `LD` (load date), `PY`, `RS` (relevance), `TC` (times
  cited), each with `+A`/`+D`, e.g. `PY+D`.
- `modified_time_span` / `tc_modified_time_span` — date ranges
  `yyyy-mm-dd+yyyy-mm-dd` (not valid with `db=WOK`).
- `detail=short` — reduced metadata.

Example request URL:

```
https://api.clarivate.com/apis/wos-starter/v1/documents?q=PY%3D2020&db=WOS&limit=50&page=1&sort_field=PY%2BD
```

## Pagination

- `limit` — records per page, **1–50** (50 is the hard max), default 10.
- `page` — 1-based, default 1.
- Every response carries `metadata` with `total`, `page`, `limit`.
  Paginate by incrementing `page` until `total` is covered.

```json
"metadata": { "total": 91, "page": 1, "limit": 10 }
```

## Response fields (`Document`)

The response wraps the records in `hits` (a plain JSON array) alongside
`metadata` — confirmed live (May 2026). **The wire JSON is camelCase**,
not the snake_case the generated Python client exposes as attributes; a
thin httpx+Pydantic client must model the camelCase keys (use Pydantic
aliases). `uid` is the only always-present field. Observed shape from a
live `db=WOS` topic search:

- `uid` — WoS UID (e.g. `WOS:A1996BH09B00084`).
- `title`
- `types`, `sourceTypes` (e.g. `["Proceedings Paper"]`)
- `source` → `sourceTitle`, `publishYear`, `publishMonth`, `volume`,
  `issue`, `pages` (`{range, begin, end, count}`), …
- `names` → `authors` (each `{displayName, wosStandard, researcherId}`),
  plus `bookEditors`, editors, inventors, …
- `links` → WoS web URLs: `record` (+ `citingArticles`, `references`,
  `related` when present)
- `citations` → list of `{db, count}` (times-cited per database)
- `identifiers` → `doi`, `issn`, `eissn`, `isbn`, `eisbn`, `pmid` (only
  the ones the record has; older proceedings often carry just `isbn`)
- `keywords` → `authorKeywords` only (often `[]`)

**Not returned:** abstract, author affiliations/addresses, funding, cited
references, citing-article lists (only a count + link), Keywords Plus,
subject categories. All require the Expanded API. Confirmed live: no
`abstract` key anywhere in a record.

## Rate limits

From the developer-portal product page, tiered by plan:

| Plan | Requests/sec | Requests/day |
|------|-------------|--------------|
| Free Trial | 1 | 50 |
| Free Institutional Member | 5 | 5,000 |
| Free Institutional Integration | 5 | 20,000 |

Our key is the **Institutional Member** tier — confirmed live from the
response headers: `x-ratelimit-limit-second: 5`, `x-ratelimit-limit-day:
5000`, with `x-ratelimit-remaining-second` / `x-ratelimit-remaining-day`
counting down per call (and a `ratelimit-reset` seconds field). A fetcher
should read these headers to self-throttle. The per-day cap is the real
constraint: at `limit=50`, 5,000 calls/day is ~250,000 records/day in
principle.

## Python client

The official client `clarivate.wos_starter.client` (repo
[`clarivate/wosstarter_python_client`](https://github.com/clarivate/wosstarter_python_client))
is OpenAPI-Generator output and is **not published on PyPI**. Install from
GitHub if used:

```bash
pip install git+https://github.com/clarivate/wosstarter_python_client.git
```

```python
import os
import clarivate.wos_starter.client

configuration = clarivate.wos_starter.client.Configuration(
    host="https://api.clarivate.com/apis/wos-starter/v1"
)
configuration.api_key["ClarivateApiKeyAuth"] = os.environ["WOS_STARTER_API_KEY"]

with clarivate.wos_starter.client.ApiClient(configuration) as api_client:
    api = clarivate.wos_starter.client.DocumentsApi(api_client)
    resp = api.documents_get(
        q='TS=("Lagrangian" AND ocean)', db="WOS", limit=50, page=1, sort_field="PY+D"
    )
    print(resp.metadata.total)
```

Given the small API surface and the "typed data, not dicts" principle,
hand-rolled Pydantic models over `httpx` are likely cleaner than the
verbose, un-released generated client — worth weighing if we build a
fetcher. Official Java and JavaScript clients also exist.

## Live verification and OpenAlex comparison (May 2026)

Probed with the live key via [scripts/explore_wos.py](../../scripts/explore_wos.py)
(`probe` / `compare` / `coverage` modes). Query
`TS=("Lagrangian" AND "ocean" AND "particle tracking")`, `db=WOS`.

Query semantics differ sharply from OpenAlex and dominate any raw count
comparison. The WoS field-tag query is a strict boolean — all three
phrases must appear in the topic (title/abstract/keywords) — and matched
**437** records. The OpenAlex `search` for the same free-text terms is
relevance-ranked and OR-leaning, matching **16,764**. So the gap is about
query language, not corpus size; a fair count comparison needs equivalent
boolean queries on both sides. First-page-vs-first-page DOI overlap is
also noisy because the two services sort differently by default (WoS
returned old proceedings papers first, not by relevance or recency).

The actionable test — **can OpenAlex backfill what WoS lacks (abstracts),
joining on DOI?** — looked strong on this sample but with caveats:

- **DOI coverage: 90 / 90** WoS DOIs were found in OpenAlex (100% in this
  sample). OpenAlex does index the WoS-indexed papers, so DOI is a viable
  join key.
- **But ~10–15% of WoS records carry no DOI at all** (first 50 hits had 43
  DOIs; first 100 had 90 unique). Older proceedings papers expose only an
  `isbn`. Those WoS-only records cannot be joined to OpenAlex by DOI —
  they'd need title/ISBN matching or stay un-enriched.
- **Abstract backfill: only 42 / 90** of the matched OpenAlex records had
  an `abstract_inverted_index`. This sample skews to old proceedings
  (default WoS sort), which lack abstracts everywhere; a `sort_field=PY+D`
  or relevance-sorted query of recent articles would likely show higher
  coverage. Measured number, one query — not a general coverage estimate.

Takeaway holds and is now evidence-backed: WoS Starter is usable as a
discovery/cross-check catalogue whose DOIs mostly resolve in OpenAlex, but
abstracts must come from OpenAlex (or, per [zotero-retrieval.md](zotero-retrieval.md),
Semantic Scholar) — and neither WoS nor OpenAlex has an abstract for every
record.

## Open questions

- If WoS becomes a second source, the `Work` model needs the provenance
  fields the review recommended (`source_catalogues: list[str]`,
  `catalogue_ids: dict[str, str]` — see
  [review-2026-04-16-system.md](../../plans/done/review-2026-04-16-system.md)
  D-3) so stage 2 (dedup) can merge OpenAlex + WoS records. How do the two
  schemas reconcile (DOI as the join key; whose times-cited/journal-type
  wins)? And how to handle the ~10% of WoS records with no DOI?
- Is the Expanded API (abstracts + full metadata) worth pursuing for the
  quantitative-synthesis stages, or does OpenAlex coverage suffice?

## Sources

- [Web of Science Starter API — developer portal](https://developer.clarivate.com/apis/wos-starter)
- [clarivate/wosstarter_python_client (GitHub)](https://github.com/clarivate/wosstarter_python_client) — endpoints, auth, field tags, `Document` schema
- [Web of Science Expanded API](https://developer.clarivate.com/apis/wos) — the abstract-bearing alternative
- [Clarivate Developer Portal — API list](https://developer.clarivate.com/apis)
