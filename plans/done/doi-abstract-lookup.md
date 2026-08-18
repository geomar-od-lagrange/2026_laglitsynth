# DOI → abstract lookup

## Goal

Backfill missing abstracts so [screening
(stage 4)](../../src/laglitsynth/screening_abstracts/) always has text to work
on. Not every catalogue record carries an abstract: a WoS Starter record
has none at all (see [wos-starter-api.md](../../docs/explorations/wos-starter-api.md)),
and even in OpenAlex roughly half of one live sample lacked an
`abstract_inverted_index`. A small enrichment step fills `Work.abstract`
from the DOI wherever it is `None`.

## Sources, tried in order

Per the comparison in
[zotero-retrieval.md](../../docs/explorations/zotero-retrieval.md):

1. **Semantic Scholar Graph** — `GET /graph/v1/paper/DOI:{doi}?fields=abstract`
   returns a plain-text abstract; the best single DOI→abstract source,
   public (a free key raises limits).
2. **OpenAlex** — `abstract_inverted_index` reconstructed to text;
   already fetched by [catalogue_fetch](../../src/laglitsynth/catalogue_fetch/fetch.py),
   so for OpenAlex-sourced works it is usually already present.
3. **Crossref** — `message.abstract` is JATS-XML, strip tags; partial
   coverage, last resort.

Stop at the first source returning a non-empty abstract. **None means
None**: a work for which no source has an abstract keeps `abstract=None`,
never `""`.

## Where it fits

A standalone enrichment over the catalogue, after
[dedup](../../src/laglitsynth/catalogue_dedup/) and before screening. It reads
works, and for each with `abstract is None` and a DOI, looks up and fills.
Output is a sidecar keyed by work id (matching the verdicts-only sidecar
pattern the pipeline already uses), so the catalogue file stays the
fetch/dedup output and the abstract is joined in downstream — not an
in-place rewrite. Works without a DOI are skipped and reported.

## Keep it small

Thin httpx + Pydantic clients per source; polite (contact email for
Crossref / Semantic Scholar, honour rate-limit headers, resumable via a
`--skip-existing` gate); plain text at ingest (reconstruct the inverted
index, strip JATS). Typed throughout, mypy-strict.

## Implementation

`pixi run typecheck` and `pixi run test` pass between each commit.

1. **Semantic Scholar client + model.** Typed `httpx` call, normalise to
   plain text. Test: mocked response → abstract; 404 / null → `None`.
2. **Crossref JATS-strip helper.** Test: a JATS abstract string → clean
   plain text; missing field → `None`.
3. **Enrichment runner + sidecar.** Read the catalogue, fill missing
   abstracts S2 → OpenAlex → Crossref, write an `AbstractRecord` sidecar
   (work id, abstract, source) plus meta counters (filled / still-missing /
   no-doi). Test: end-to-end on a fixture catalogue; `--skip-existing`
   processes only the gaps; `None` preserved when no source has one.
4. **Docs.** A short [docs/](../../docs/) stage note and a row in
   [docs/pipeline.md](../../docs/pipeline.md) / [docs/interfaces.md](../../docs/interfaces.md).

## Non-goals

Not a new catalogue source — no search, enrichment only. Not full text.
No paid APIs. Does not modify the catalogue file in place.

## Open questions

- Is Semantic Scholar's coverage high enough that Crossref is rarely
  exercised? Measure once running, don't assume.
- Should the enrichment run as its own CLI subcommand or fold into the
  screening stage's input-loading? Sidecar keeps them separable.
