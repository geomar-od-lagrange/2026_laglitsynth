# Diversifying full-text retrieval

## Why this is a design direction, not yet a build spec

Stage 6 today ([fulltext_retrieval/retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py))
runs one process against OA URLs, Unpaywall, and a single manual drop
directory. PDF coverage from any single source is patchy, publishers
throttle a single IP, and the people who can legitimately reach paywalled
PDFs are several collaborators with different institutional access — not
one machine. An earlier draft of this plan answered that with a full
attempt-log plus pluggable-resolver framework. That is premature
complexity. This revision keeps the load-bearing ideas — a work-keyed
store that accumulates across sources, legitimate-access-only, and cheap
manual paths for a community that already does manual steps — and
**defers the heavy machinery and the implementation sequence** until the
decisions below are settled.

## Decouple the catalogue and PDF store from the search term

The key realisation: a review runs *many* search terms, and each term's
catalogue and PDF set overlap heavily — they vary only at the margin. So
nothing about storage should be tied to a search:

- The **catalogue is a persistent, work-keyed store** (deduplicated by
  DOI / work id), the union of every search's results. A search is an
  *event* that adds works at the margin; provenance records which
  queries surfaced each work. This generalises what
  [dedup](../src/laglitsynth/catalogue_dedup/) already does for multiple
  fetches.
- The **PDF store is likewise work-keyed and persistent**: a PDF is
  fetched once per work (by DOI) and reused by every search that includes
  that work.
- A given search or review is then a **selection — a view over the shared
  store**, not a private copy.

With this decoupling, retrieval cost is incremental: a new search mostly
reuses PDFs already fetched, and "diversification" falls out for free —
many searches, sources, people, and points in time all contribute to the
same work-keyed store, deduplicated. It connects to the
`source_catalogues` / `catalogue_ids` provenance ([D-3](done/review-2026-04-16-system.md))
and the run-manifest / project-grouping idea in
[running-the-pipeline.md](../docs/explorations/running-the-pipeline.md).

## Diversifying retrieval, cheaply

Because the store is work-keyed, diversification is just many contributors
filling gaps in one store. Ordered by effort:

1. **Automatic OA** — keep today's Unpaywall + OA-URL retrieval; it fills
   the easy fraction with no human in the loop.
2. **Manual via Zotero** (the low-tech path) — for works still missing a
   PDF, export a list of `https://doi.org/…` links or an RIS/BibTeX file.
   A collaborator imports it into Zotero (or similar), runs "Find
   Available PDF" — which uses *their* institutional access plus Unpaywall
   — and exports the attached PDFs. We ingest those back, matched by DOI.
   This offloads the hard part (publisher access, link resolvers, rate
   limits, captchas) onto a mature tool plus a human with the right
   subscription. Crucially this uses the Zotero *application*, not its
   code, so it sidesteps the AGPL/vendoring problem flagged in
   [zotero-retrieval.md](../docs/explorations/zotero-retrieval.md).
3. **Direct drop** — a collaborator simply saves PDFs named by DOI / work
   stem and ships the folder; we ingest by filename match. The no-tool
   fallback.

All three feed the same work-keyed PDF store; ingestion deduplicates by
DOI and validates that the bytes are actually a PDF.

## What stays minimal for now

Do not build the attempt-log + pluggable-resolver framework yet. The
minimal shape:

- A work-keyed PDF store (`pdfs/<work-stem>.pdf`) shared across searches,
  with a light provenance sidecar recording, per work, where its PDF came
  from (`oa` / `zotero-import` / `manual`) or that it is still missing.
- An **export** step: list the works in the current selection that have no
  PDF, as DOI links + RIS + a CSV naming the expected `<work-stem>.pdf`.
- An **import** step: ingest a returned PDF folder (match by DOI / stem,
  validate magic bytes, dedup), updating provenance.
- Keep today's automatic OA retrieval as-is.

The richer per-attempt / version / resolver model is recorded as a future
option, to add only if multiple PDF versions per work or fine-grained
provenance prove necessary.

## Non-goals

Legitimate access only — OA plus collaborators' own licensed access via
their own tools; no scraping behind paywalls, no captcha-solving, no
Sci-Hub. No bulk publisher hammering. No commitment yet to the
attempt-log/resolver framework or to a specific metadata backend. No text
extraction — that is stage 7
([fulltext_extraction](../src/laglitsynth/fulltext_extraction/)).

## Open questions

- Where does the persistent catalogue + PDF store live relative to
  per-search runs? This is the same gap as project/run grouping in
  [running-the-pipeline.md](../docs/explorations/running-the-pipeline.md) —
  likely wants resolving together.
- How do PDFs returned from Zotero carry their DOI for matching — via
  Zotero's export filename, or a CSV mapping we round-trip? Confirm
  against a real Zotero export before designing import.
- One PDF per work, or multiple versions (preprint + published)? This
  decides whether the deferred attempt model is ever needed.
- ~10% of WoS-only records carry no DOI (see
  [wos-starter-api.md](../docs/explorations/wos-starter-api.md)) — how are
  those keyed in a DOI-keyed store?

## Deferred implementation

No commit sequence yet, by design. Once the open questions settle, the
build is small: a work-keyed store plus export/import commands wrapping
today's OA retrieval — not a new framework.
