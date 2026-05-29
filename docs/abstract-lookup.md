# Abstract lookup

A standalone catalogue-enrichment stage that backfills missing abstracts by
DOI so [screening](../src/laglitsynth/screening_abstracts/) always has text to
work on. Not every catalogue record carries an abstract: a Web of Science
Starter record has none at all (see
[wos-starter-api.md](explorations/wos-starter-api.md)), and even in OpenAlex a
live sample had a substantial fraction without an `abstract_inverted_index`.
This stage fills `Work.abstract` from the DOI wherever it is `None`.

It runs after [catalogue-dedup](catalogue-dedup.md) and before
[screening-abstracts](screening-abstracts.md). It reads the deduplicated
catalogue and, for each `Work` with `abstract is None` and a DOI, looks one up.
The abstract is written to a sidecar keyed by work id, never back into the
catalogue: the deduplicated catalogue stays the single source of `Work`
records, and the abstract is joined in downstream — the same flag-don't-filter
pattern the verdict sidecars use ([interfaces.md](interfaces.md)).

## Sources, tried in order

The first source returning a non-empty abstract wins; later sources are not
queried for that work.

### 1. Semantic Scholar Graph

`GET /graph/v1/paper/DOI:{doi}?fields=abstract` returns a plain-text
abstract — the best single DOI-to-abstract source. Public; an optional API
key (`--api-key`, sent as the `x-api-key` header) raises rate limits.

### 2. OpenAlex

`GET /works/doi:{doi}` selecting `abstract_inverted_index`, reconstructed to
plain text. For OpenAlex-sourced works the abstract is usually already present
from [catalogue-fetch](catalogue-fetch.md), so this mainly fills works that
entered the catalogue from another source.

### 3. Crossref

`GET /works/{doi}` whose `message.abstract` is JATS-XML; tags are stripped to
plain text. Partial coverage, last resort.

Plain text at ingest: the inverted index is reconstructed and JATS markup is
stripped before anything is written. **None means None** — a work for which no
source has an abstract keeps `abstract=None`, never `""`.

## Politeness and resumability

`--email` (with an `ABSTRACT_LOOKUP_EMAIL` `.env` fallback) is sent as the
Crossref / Semantic Scholar / OpenAlex polite contact. A `429` response is
retried a bounded number of times, honouring `Retry-After` when present and
otherwise backing off exponentially, capped so a misbehaving header cannot
stall a run. A source that errors is logged and skipped so one flaky source
never aborts the cascade.

`--skip-existing` re-reads the existing sidecar and carries forward every work
whose abstract was already resolved, looking up only the works still missing
one. Works without a DOI are skipped and counted in `no_doi_count`.

## Data models

### AbstractRecord

```python
class AbstractRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    abstract: str | None = None
    source: str | None = None  # "semantic_scholar" | "openalex" | "crossref" | None
```

A record is emitted for every input work. `source` is `None` both for works
whose abstract already lived in the catalogue and for works no source could
fill (the latter also carry `abstract=None`).

### AbstractLookupMeta

```python
class AbstractLookupMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    input_path: str
    input_count: int
    filled_count: int          # abstracts resolved by a source
    still_missing_count: int   # records left with abstract=None
    no_doi_count: int          # works skipped for lacking a DOI
    by_source: dict[str, int]  # counts per source label that yielded an abstract
```

## Storage layout

```
data/abstract-lookup/
  abstracts.jsonl             # one AbstractRecord per input work
  abstract-lookup-meta.json   # AbstractLookupMeta
```

## CLI interface

```
laglitsynth abstract-lookup \
    --input data/catalogue-dedup/deduplicated.jsonl \
    --output-dir data/abstract-lookup/ \
    --email user@example.com \
    [--api-key SEMANTIC_SCHOLAR_KEY] [--skip-existing]
```

## What is deferred

- A new catalogue source — this is enrichment only, no search.
- Full text (that is [fulltext-retrieval](fulltext-retrieval.md)).
- Paid abstract APIs.
- Rewriting the catalogue file in place.
