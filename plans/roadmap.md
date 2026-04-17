# Roadmap

One-page overview of what's done, in flight, and queued. Details live
in the linked plans and in the [2026-04-16 review](review-2026-04-16.md).
Update this file when a plan is written, implemented, or archived.

## Implemented

- Stages 1–6 end-to-end: `catalogue-fetch` → `catalogue-dedup` →
  `screening-abstracts` → `screening-adjudication` → `fulltext-retrieval`
  → `fulltext-extraction`. Ships with `pixi run typecheck` and `pixi
  run test` green (76 tests, 79% coverage).
- [Flag-don't-filter cutover](done/flag-dont-filter-cutover.md) — stage
  3 emits a `ScreeningVerdict` sidecar covering every input work; stage
  4 applies the threshold and emits `AdjudicationVerdict` records
  alongside `included.jsonl`. `Filter*` → `Screening*` rename folded
  in. `io.write_jsonl` flipped from `"x"` to `"w"`.
- [Stage 5/6 bug cluster](done/stage-5-6-bugs.md) —
  `RetrievalStatus.failed` wired, `unretrieved.txt` derived from
  records, `_validate_pdf` tightened to the PDF magic check, shared
  `laglitsynth.ids` module, GROBID preflight timeout, `lxml` parser
  hardened, `retrieval.jsonl` now appended per-record for crash
  safety.
- Documentation filename sweep: per-stage docs renamed to match CLI
  names (`catalogue-fetch.md`, `catalogue-dedup.md`,
  `screening-abstracts.md`, `screening-adjudication.md`,
  `fulltext-retrieval.md`, `fulltext-extraction.md`);
  [pipeline.md](../docs/pipeline.md) headings and mermaid labels
  synced; [README.md](../README.md) rewritten to list the real CLI.

## In flight

- [Reproducibility meta refactor](reproducibility-meta-refactor.md)
  — nested `_RunMeta` across every `*Meta`; `_Base` deletion with
  `extra="ignore"` on OpenAlex models and `extra="forbid"` on
  internal; stage 3 LLM determinism (explicit `temperature=0.8`,
  per-call random seed on each `ScreeningVerdict`, `prompt_sha256`
  on `ScreeningMeta.llm`). Skipped: commit SHA, input hashing,
  dirty-tree, CLI temp/seed flags. Two open questions (both
  low-stakes).

## Queued — ready to plan

- Thin TEI wrapper for `ExtractedDocument`. Stop rewriting TEI into
  lossy `(title, text)` pairs; carry `tei_path` (+ `content_sha256`
  once reproducibility lands) as the canonical reference, and expose a
  small accessor API (`doc.sections()`, `doc.figures()`,
  `doc.citations()`, `doc.bibliography()`) built lazily via XPath. Not
  a pydantic mirror of TEI — just typed views over the bytes we
  already keep on disk. Subsumes the flat-vs-recursive `parse_tei`
  question. Needs plan. Unblocks stages 7+ consumers that want more
  than plain section text.
- Commit `pixi.lock`. Remove from `.gitignore`, add to repo.

## Deferred until pipeline is feature-complete

- [Multi-run consensus](multi-run-consensus.md) — plan exists but is
  deferred until stages 7–12 have landed. Consensus only becomes
  meaningful once there's an end-to-end answer to compare robustness
  against; running it earlier tunes a knob whose downstream effect
  isn't observable yet. Plan text also predates the cutover and
  needs freshening when picked back up.

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
