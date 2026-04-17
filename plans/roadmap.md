# Roadmap

One-page overview of what's done, in flight, and queued. Details live
in the linked plans and in the [2026-04-16 review](review-2026-04-16.md).
Update this file when a plan is written, implemented, or archived.

## Implemented

- Stages 1–6 end-to-end: `catalogue-fetch` → `catalogue-dedup` →
  `screening-abstracts` → `screening-adjudication` → `fulltext-retrieval`
  → `fulltext-extraction`. Ships with `pixi run typecheck` and `pixi
  run test` green (46 tests, 70% coverage).
- Known issues catalogued in the
  [2026-04-16 review](review-2026-04-16.md); most are addressed by the
  "in flight" and "queued" items below.

## In flight

- [Flag-don't-filter cutover](flag-dont-filter-cutover.md) — build
  `laglitsynth.resolve`, rewrite stage 3 and stage 4 to verdict
  sidecars, change stage 5's CLI to consume via resolve, fold in the
  `Filter*`→`Screening*` rename. Plan written; five open questions
  outstanding. Unblocks stage 7.
- [Stage 5/6 bug cluster](stage-5-6-bugs.md) — `RetrievalStatus.failed`
  wiring, `unretrieved.txt` derived from records, `_validate_pdf`
  tightening, PDF-stem → `work_id` check, shared `laglitsynth.ids`
  module, GROBID preflight timeout, TEI hardening. Plan in progress.

## Queued — ready to plan once the cutover lands

- Reproducibility meta refactor + `_Base` deletion. Introduce a
  `_RunMeta` shape (`tool_commit_sha`, `input_paths`,
  `input_content_sha`, `validation_skipped`) across every meta model;
  record LLM sampling params (temperature, seed, prompt digest, model
  digest); drop `_Base` and set `ConfigDict(extra="ignore")` on `Work`
  + `ConfigDict(extra="forbid")` on internal models. Touches every
  file the cutover rewrites — wait for the cutover to stabilize the
  shapes.
- Documentation cleanup. Rename per-stage docs to match CLI names
  (`fetch-publications.md` → `catalogue-fetch.md` etc.); fix
  [README.md](../README.md) command examples; sync
  [docs/pipeline.md](../docs/pipeline.md) headings + mermaid labels
  to the canonical two-component names; fix the broken link at
  [docs/interfaces.md:20](../docs/interfaces.md). Small checklist; no
  separate plan needed.
- [Multi-run consensus](multi-run-consensus.md) — plan already exists.
  Depends on the verdict-sidecar contract from the cutover.
- Commit `pixi.lock`. Remove from `.gitignore`, add to repo.

## Queued — driven by future stages

- Stage 7 (`fulltext-eligibility`). Needs plan. Specced in
  [docs/eligibility.md](../docs/eligibility.md) but that spec pre-dates
  the flag-don't-filter design; update or replace when planning.
- Stage 8 (`extraction-codebook`). Needs a stage 8 doc first (does not
  exist yet), then a plan. `ExtractionRecord` decided to be fully
  typed, regenerated alongside codebook changes.
- Stages 9–12 (`extraction-adjudication`, `synthesis-quantitative`,
  `synthesis-thematic`, `synthesis-narrative`). No plans yet.
- `Work` model additions — `source_catalogues: list[str]`,
  `catalogue_ids: dict[str, str]`, derived `is_peer_reviewed: bool |
  None`. Defer until stage 7+ consumer arrives.
- `ExtractedDocument` quality gate — `extraction_status` enum + metrics.
  Defer until stage 7/8 consumer arrives.

## Latest review

[plans/review-2026-04-16.md](review-2026-04-16.md) indexes the three
sub-reviews ([surface](review-2026-04-16-surface.md),
[code](review-2026-04-16-code.md),
[system](review-2026-04-16-system.md)) and groups findings into seven
cross-cutting themes. Every queued item above traces to a finding
there.
