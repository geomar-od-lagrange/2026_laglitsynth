# Catalogue search widening

## Goal

Replace the six October 2026 OpenAlex queries with a measured, wider set,
each query with its own record cap, and run them from the review config.
The current queries were truncated at 2,000 records while still returning
relevant work. Whole application communities (oil spill, search-and-rescue
drift, larval models, icebergs, sediment, aquaculture) and named tools
(OpenDrift, Ichthyop, Parcels) are missing from the catalogue. The
measurements are in
[catalogue-search-widening.md](../docs/explorations/catalogue-search-widening.md).

## Non-goals

- **Citation chaining** is a follow-up, not part of this plan.
- **Other catalogue sources:** Web of Science is in
  [wos-starter-api.md](../docs/explorations/wos-starter-api.md).
- **The screening question** is out of scope: changing the stage-3
  question is [issue #31](https://github.com/geomar-od-lagrange/2026_laglitsynth/issues/31),
  and the scope wording is
  [issue #26](https://github.com/geomar-od-lagrange/2026_laglitsynth/issues/26).

The production run needs #31 settled first (see Risks), but this plan
doesn't change it.

## Target state

The review config carries a list of queries instead of one string. In
[review.py](../src/laglitsynth/review.py), `ReviewConfig.query: str` and
`max_records` become:

```yaml
queries:
  - label: lagrangian-oceanography
    query: "Lagrangian oceanography"
    max_records: 2000
  - label: tool-opendrift
    query: '(OpenDrift OR OpenOil OR MEDSLIK OR "MEDSLIK-II") AND (drift OR trajectory OR trajectories OR oil OR spill)'
    # max_records absent: fetch every hit
```

Each entry is a small `QueryConfig` model.

- **Labels** must be unique kebab-case and name the fetch output file,
  `catalogue-fetch/<label>.jsonl`.
- **`max_records`** absent means fetching every hit.

`review-config` emits the list in a form the shell can loop over. The emit
format is chosen at implementation and documented in
[configs.md](../docs/configs.md).

[run-pipeline.sh](../scripts/run-pipeline.sh) changes in two places:

- **Stage 1** runs `catalogue-fetch` once per entry, with `--max-records`
  when the entry sets one.
- **Manifest:** `manifest-init` receives every query string; it already
  takes `nargs="+"`.

The env override `QUERY` / `N` keeps working for smoke runs: when `QUERY`
is set, it replaces the list with a single entry.

A new review config,
`examples/reviews/lagrangian-oceanography-wide.yaml`, holds the candidate
list from
[catalogue-search-widening-queries.tsv](../docs/explorations/catalogue-search-widening-queries.tsv):
Tier A entries active, Tier B entries present but commented out. The
existing smoke config moves to a one-entry `queries:` list.

Query selection rules, as measured:

- **Repaired:** "Lagrangian particle tracking" gains an ocean qualifier, and
  "ocean particle tracking" is rewritten as a boolean query.
- **Kept as they are:** the other four current queries.
- **Caps:** 2,000 where ranks 800–1,200 still keep at least 30 %, 400 below
  that, and the whole result set for queries with fewer than 1,200 hits.

Which tiers go to production is open: the operator revisits the list before
the production run.

## Implementation sequence

1. **`QueryConfig` and `queries` in `ReviewConfig`.** `query` and
   `max_records` are removed. The emitter outputs the list, and the smoke
   config is converted. Tests in `tests/test_review.py`:
   - a multi-entry list round-trips through `review-config`;
   - a duplicate label is rejected;
   - a label that isn't kebab-case is rejected;
   - an entry without `max_records` emits no cap;
   - a query with quotes and parentheses survives the shell emit.
2. **Runner loops stage 1 over the entries.** It writes
   `catalogue-fetch/<label>.jsonl`, passes every query to `manifest-init`,
   and keeps the `QUERY`/`N` override as a single entry. The runner test in
   `tests/test_run_pipeline.py` gets a two-entry config and asserts two fetch outputs
   and a manifest listing both queries.
3. **`examples/reviews/lagrangian-oceanography-wide.yaml`** from the TSV,
   with Tier A active. A test loads it and checks:
   - the labels are unique;
   - no query contains `*` or `?`, which OpenAlex rejects;
   - every boolean operator is upper case.

   [configs.md](../docs/configs.md) and
   [catalogue-fetch.md](../docs/catalogue-fetch.md) describe the list.
   The roadmap links this plan.

## Follow-ups

- **Citation chaining** for references cited by at least five eligible
  papers. 25 % of those are relevant, the same as a mid-ranked query, and
  that band catches works no keyword finds. It needs a stage that reads
  `referenced_works` and fetches by id.
- **Re-probe after the production fetch.** The per-query shares rest on
  12–30 abstracts each; the production screening run gives the real yield
  per query, and the list is pruned from it.

## Risks

- **Screening load and cost.** Tier A alone fetches up to 42,500 records
  before deduplication, and every new work goes through stage 3. The run
  waits for a raised LLM budget and for #31's recall-oriented question, so
  that the catalogue is screened once under the final wording.
- **Ranking drift.** OpenAlex relevance ranking and its index change over
  time, so a cap of 400 fetches a different head on another day. The fetch
  meta already records the query and run time; the per-label output files
  keep that traceable.
- **`NOT` clauses** drop a work when the term appears anywhere in its full
  text. The candidate list uses none, and new entries should avoid them.

## Critical files

- [src/laglitsynth/review.py](../src/laglitsynth/review.py)
- [src/laglitsynth/catalogue_fetch/fetch.py](../src/laglitsynth/catalogue_fetch/fetch.py)
- [src/laglitsynth/manifest.py](../src/laglitsynth/manifest.py)
- [scripts/run-pipeline.sh](../scripts/run-pipeline.sh)
- [examples/reviews/lagrangian-oceanography.yaml](../examples/reviews/lagrangian-oceanography.yaml)
- [docs/configs.md](../docs/configs.md)
- [docs/catalogue-fetch.md](../docs/catalogue-fetch.md)
- [docs/explorations/catalogue-search-widening.md](../docs/explorations/catalogue-search-widening.md)
