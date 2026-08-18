# Roadmap

One-page overview of what's done, in flight, and queued. Details live
in the linked plans and in the [2026-04-16 review](done/review-2026-04-16.md).
Update this file when a plan is written, implemented, or archived.

## Implemented

- Stages 1–3, 5–8 end-to-end: `catalogue-fetch` → `catalogue-dedup` →
  `screening-abstracts` → `fulltext-retrieval` → `fulltext-extraction`
  → `fulltext-eligibility` → `extraction-codebook`. Stage 4
  (`screening-adjudication`) was deleted in the verdicts-only cutover.
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
- [Reproducibility meta refactor](done/reproducibility-meta-refactor.md)
  — `_Base` deleted; OpenAlex models use `extra="ignore"`, internal
  models use `extra="forbid"`. Shared `RunMeta` and `LlmMeta` live
  in `src/laglitsynth/models.py`; every `*Meta` nests `run: RunMeta`
  (`tool`, `tool_version`, `run_at`, `validation_skipped`). Stage 3
  passes `temperature=0.8` and a per-call `random.randint(...)` seed
  to Ollama; each `ScreeningVerdict` carries its seed;
  `ScreeningMeta.llm` carries `prompt_sha256`.
- [Thin TEI wrapper for `ExtractedDocument`](done/tei-wrapper.md) —
  `ExtractedDocument` trims to `{work_id, tei_path, content_sha256,
  extracted_at}`; lazy `TeiDocument` wrapper exposes
  `sections()`, `figures()`, `citations()`, `bibliography()` over
  the TEI bytes we already keep on disk. Recursive `Section.children`
  resolves the flat-vs-recursive question.
- [Stage 7 `fulltext-eligibility`](done/stage-7-fulltext-eligibility.md) —
  one LLM pass per included work deciding inclusion on the full text
  (or the abstract when no extraction is available). Tri-state
  `eligible: bool | None` with three sentinel reasons (`no-source`,
  `tei-parse-failure`, `llm-parse-failure`). `ExtractedDocument.extraction_status`
  was not needed: missing and empty `sections()` already trigger the
  right fallback.
- [Stage 8 `extraction-codebook`](done/stage-8-extraction-codebook.md) —
  one LLM pass per eligible work filling a structured
  [`ExtractionRecord`](../src/laglitsynth/extraction_codebook/models.py)
  (every value paired with a verbatim `*_context`, every content field
  `str | None` per [codebook.md](../docs/codebook.md)). Same cascade and
  sentinel vocabulary as stage 7, plus a `truncated` flag and a
  `CHAR_BUDGET` placeholder in
  [`prompts.py`](../src/laglitsynth/extraction_codebook/prompts.py) to
  tune on the first smoke run. `ExtractedDocument.extraction_status`
  was not needed for stage 8 either — malformed TEI records a
  `tei-parse-failure` sentinel as in stage 7.
- [Pre-prod cleanup](done/pre-prod-cleanup.md) — hold-the-prod and
  high-leverage simplifications before the first NESH prod run.
  Stage tools become flags-only (`--api-key` on catalogue-fetch,
  `--email` on fulltext-retrieval); driver scripts source `.env` and
  pass the flag. `--max-records` default cap dropped. Stages 7 and 8
  refuse `--skip-existing` when the recorded `prompt_sha256` differs
  from the current run's. `flatten_sections` lifts to
  [`tei.py`](../src/laglitsynth/fulltext_extraction/tei.py); stages 7
  and 8 share it. `ExtractionRecord` subclasses `_ExtractionPayload`
  so the 28 codebook fields no longer drift between two declarations.
  `screening-abstracts-export` collapses to one subcommand with
  `--format csv|xlsx`. `_RunMeta`/`_LlmMeta` lose their leading
  underscores. NESH sbatch wait loops emit real elapsed time. Test
  helpers move to `tests/conftest.py`; mock-heavy seed/temperature
  call-site assertions go. Production code: −114 lines net.

- [LLM-stage timeouts and reviewer-xlsx restructure](done/llm-timeout-and-reviewer-xlsx.md)
  — fixes the `openai.APITimeoutError` that killed
  `nesh-pipeline-22047331` mid-stage 8 (timeout + retries on the OpenAI
  client in stages 3/7/8, new `llm-timeout` sentinel); flips stage 4
  to pass-through every null-score sentinel so `no-abstract` works
  reach fulltext-retrieval; gives stage 3 the same title/authors/year/
  abstract block the human reviewer sees so scoring is symmetric;
  reworks the stage-3 reviewer xlsx with bibliographic header, verbatim
  criterion, percent-formatted score, reviewer placeholders, LLM
  verdict block collapsed by default.

- [Verdicts-only cutover](done/verdicts-only-cutover.md) — stage 4
  (`screening-adjudication`) deleted; stages 5, 7, 8 rewired to inline-join
  the deduplicated catalogue against upstream verdict sidecars; no more
  `included.jsonl` or `eligible.jsonl` convenience copies.

- [DOI → abstract lookup](done/doi-abstract-lookup.md) — `abstract-lookup`
  stage backfills missing abstracts by DOI (Semantic Scholar → OpenAlex →
  Crossref, first non-empty wins) into an
  [`AbstractRecord`](../src/laglitsynth/abstract_lookup/models.py) sidecar keyed
  by work id; the deduplicated catalogue is never rewritten in place. Runs
  between `catalogue-dedup` and `screening-abstracts`. Source clients take an
  injected `httpx.Client` (tested via `httpx.MockTransport`, no network);
  `--email`/`--api-key` with `.env` fallback, `Retry-After` honoured with a
  bounded backoff, `--skip-existing` re-processes only gaps. See
  [abstract-lookup.md](../docs/abstract-lookup.md).

- [Diversifying full-text retrieval](done/fulltext-retrieval-diversified.md) —
  stage 5 now maintains a persistent, work-keyed PDF store at
  `data/pdfs/<stem>.pdf` with a `provenance.jsonl` sidecar
  ([`PdfProvenanceRecord`](../src/laglitsynth/fulltext_retrieval/models.py)),
  replacing the per-run `retrieval.jsonl` / `RetrievalRecord`.
  `fulltext-retrieval` is rewired onto the store (`--data-dir`,
  sticky-by-default retrieval, `--manual-dir` / `unretrieved.txt`
  removed); `fulltext-retrieval-export` emits the DOI/RIS/round-trip-CSV gap
  bundle and `fulltext-retrieval-import` ingests a collaborator's returned
  folder (match by embedded-DOI / stem / sidecar, magic-byte validate,
  sha256 dedup). Single-writer, A-driven drop-zone collaboration model
  ([cross-machine.md](../docs/cross-machine.md)); manifest wiring deferred.
  See [fulltext-retrieval.md](../docs/fulltext-retrieval.md).

- [Stages 7–8 full-text-only + review exports](done/fulltext-stages-full-text-only-and-review.md)
  — `fulltext-eligibility` and `extraction-codebook` no longer fall back to
  the abstract: a work without a full-text extraction is skipped (no verdict/
  record row, no count), and the `SourceBasis` field, `no-source` sentinel,
  `abstract_only` / `by_source_basis` counters, and stage 10's abstract-only
  uncertainty note are all gone. Empty and malformed TEI both record
  `tei-parse-failure`. New XLSX-only `fulltext-eligibility-export` /
  `extraction-codebook-export` subcommands write a sampled review workbook
  (Index + per-work tabs; stage 8's tab lists each codebook field as
  `value` / `context` / `reviewer_correction`, driven off the codebook) for
  spot-checking the LLM stages and tuning prompts. CSV is retired throughout,
  including stage 3's now-purposeless `--format csv`. See
  [eligibility.md](../docs/eligibility.md) and
  [extraction-codebook.md](../docs/extraction-codebook.md).

- [PR #20 review fixes](done/pr20-review-fixes.md) — closes the
  [PR #20 review](https://github.com/geomar-od-lagrange/2026_laglitsynth/pull/20).
  Full-text retrieval is now sticky by default: a normal run skips every
  work that already holds a non-`missing` PDF and attempts only
  `missing`/unseen ones, never overwriting an on-disk PDF (`--skip-existing`
  removed; `--refetch` opts back into deliberate re-download). A
  no-downgrade guard at the single `upsert_provenance` write point keeps a
  failed `--refetch` from clobbering a held PDF with `missing`.
  `RetrievalMeta.abstract_only_count` → `missing_count` (`failed_count`
  dropped); `_try_oa_urls` returns `tuple[PdfSource, str] | None` (the
  `_AllAttemptsFailedError` ceremony deleted); embedded-DOI matching in
  `fulltext-retrieval-import` strips trailing `.,;:)`; the three export
  modules' `_load_meta` now returns a typed `LlmMeta | None` instead of
  falling back to an untyped dict.

- [Doc/code consistency fixes](done/doc-code-consistency-fixes.md) —
  closes a three-agent documentation audit (440 doc facts extracted,
  checked against code, gaps found; spot-check validated). Fixes one real
  regression — `scripts/run-pipeline.sh` (and NESH via the sbatch) passed
  the removed `--output-dir`/`--skip-existing` to `fulltext-retrieval`, so
  it could not reach stages 5–8; now `--data-dir "$ROOT"` with sticky
  default and stage-6 `--pdf-dir "$ROOT/pdfs"`. Plus ~16 docs corrected to
  match code: `--grobid-url` optional (+ `--timeout`), the dead
  extraction-quality-gate claim removed, `read_works_jsonl` →
  `read_jsonl`, stage-3 output is the verdict sidecar only, `DroppedRecord`
  field names, no `source_basis` field, stages 7/8 honour `--concurrency`,
  and newly-documented behaviours (silent-drop of works absent from the
  verdicts file, download timeout, retry constants, Ollama model-pulled
  preflight, XLSX review-export layouts).

- [Per-review config, externalized screening prompt, knob cleanups](done/runner-review-config-and-screening-criteria.md)
  — acts on the UI-knobs investigation. The runner is now driven by one
  per-review YAML: `scripts/run-pipeline.sh [config.yaml]` reads a typed
  [`ReviewConfig`](../src/laglitsynth/review.py) via a `review-config`
  emitter subcommand and applies env > config > default precedence,
  replacing scattered positionals and the two hardcoded threshold
  literals. Stage 3's screening prompt is externalized to a
  `--screening-criteria` YAML (a shared
  [`load_system_prompt`](../src/laglitsynth/prompts.py)), mirroring
  stages 7/8 — the last un-externalized LLM system prompt. The two corpus
  thresholds stay independently settable (`RETRIEVAL_THRESHOLD` /
  `ELIGIBILITY_THRESHOLD`, shared `SCREENING_THRESHOLD` default) with
  unified float handling. `--consolidate-citations` exposed on stage 6;
  `--from-year`/`--to-year` threaded into the runner; `.env.example`
  gains the two missing keys. Deferred: a sweep/matrix driver (investigation
  direction 4).

- [Run manifest](done/run-manifest.md) — one typed `RunManifest` at
  `data/manifest.json` pinning the review's queries, the shared run-id, and an
  append-only per-stage input→output log. `manifest-init` mints a review;
  every wired stage resolves an omitted input flag and `--run-id` from the log
  (flag wins, else the manifest, else the previous required-flag error) and
  records its own entry. `catalogue-dedup`, `screening-abstracts`,
  `fulltext-retrieval`, `fulltext-retrieval-export`, `fulltext-retrieval-import`,
  `fulltext-eligibility`, and `extraction-codebook` are wired;
  `catalogue-fetch` and `fulltext-extraction` are the remaining two. The
  manifest is additive: a fully-flagged invocation is unchanged, and
  `record_stage` is a no-op where a review has none. Paths store relative to
  the project root when they sit under it, so a manifest resolves in a
  checkout at a different absolute location. See
  [run-manifest.md](../docs/run-manifest.md).

- [Collaborator handoff loop](done/collaborator-handoff-loop.md) — the DOI
  handoff is now two commands: `fulltext-retrieval-export --data-dir data`
  writes the bundle, and `fulltext-retrieval-import --import-dir <folder>
  --source manual --data-dir data` ingests what comes back, both resolving
  their paths from the manifest. The bundle gained `title`/`year` columns on
  `pdf-manifest.csv`, a `no-doi.csv` worklist for the works `dois.txt` cannot
  represent, and a `README.md` that travels with it. `EXPORT_GAP=1` runs the
  export from the runner, so `STOP_AFTER_STAGE=5 EXPORT_GAP=1` is the whole
  handoff run. The `title`/`year` columns are the precursor for the
  title-matching import tier in
  [contributor-pdf-matching.md](../docs/explorations/contributor-pdf-matching.md).

- [Import matching tiers](done/import-matching-tiers.md) — driven by a
  measurement over 26 real publisher PDFs
  ([contributor-pdf-matching.md](../docs/explorations/contributor-pdf-matching.md)),
  renamed with no filename discipline and fed to the real importer. Import now
  gathers every self-identifying DOI — Info dictionary, XMP, title-page link
  annotations, first-page text — and keeps the first that appears in
  `pdf-manifest.csv`, instead of trusting the first string that looks like a
  DOI: publisher Info dictionaries often carry the journal's DOI rather than
  the article's. The DOI pattern gained the angle brackets AMS DOIs contain.
  Filed PDFs went from 16 to 19 of 26, and no PDF now yields candidates
  without the correct one among them. The remaining 7 embed no DOI anywhere
  inspectable, which is the title-matching question and stays open.

## In flight

- [Usability docs](usability-docs.md) — D1 done (`docs/external-services.md` runbook); D2 (per-stage prereq blocks + `interfaces.md` STOP HERE) and D3 (README hygiene) pending. The complementary [running-the-pipeline.md](../docs/explorations/running-the-pipeline.md) exploration covers operationally driving stages, storage clarity, and cross-machine/collaborator runs — its top candidate is now planned as the [run manifest](done/run-manifest.md).
- [Cross-machine + storage convention](../docs/cross-machine.md) — the third follow-up candidate from [running-the-pipeline.md](../docs/explorations/running-the-pipeline.md) landed as an authoritative doc: project = working-directory boundary, run-from-root + fixed-`data/` path contract, per-machine `.env`, and the safe sync direction per `data/` subdir (bulk PDFs/TEI union, per-run-id gate outputs authoritative-side-wins, deduplicated spine rsynced once read-only). The Zotero-import open question from [fulltext-retrieval-diversified.md](done/fulltext-retrieval-diversified.md) is withdrawn: import reads identifiers out of the PDF and matches them against `pdf-manifest.csv`, so Zotero's filename convention does not enter the design. The probe script and its note are removed; the reasoning is recorded in [contributor-pdf-matching.md](../docs/explorations/contributor-pdf-matching.md).

## Queued — ready to plan

Nothing queued. The next candidates are the two unwired manifest stages
(`catalogue-fetch`, `fulltext-extraction`) and title-based import matching,
which needs a threshold tuned against real collaborator returns rather than
one library's PDFs.

## Deferred until pipeline is feature-complete

- [Multi-run consensus](multi-run-consensus.md) — plan exists but is
  deferred until stages 7–12 have landed. Consensus only becomes
  meaningful once there's an end-to-end answer to compare robustness
  against; running it earlier tunes a knob whose downstream effect
  isn't observable yet. Plan text also predates the cutover and
  needs freshening when picked back up.

## Queued — driven by future stages

- Stages 9–12 (`extraction-adjudication`, `synthesis-quantitative`,
  `synthesis-thematic`, `synthesis-narrative`). No plans yet.
- [Two-pass extraction](../docs/two-pass-extraction.md) for stage 8 —
  deferred until phase 3 codebook review flags attention-dilution on
  specific fields. `passage_locations` is captured faithfully by the
  stage 8 prototype as ground truth for pass-1 calibration.
- `Work` model additions — `source_catalogues: list[str]`,
  `catalogue_ids: dict[str, str]`, derived `is_peer_reviewed: bool |
  None`. Defer until a stage 7+ consumer arrives — stage 7 itself does
  not branch on these. Now also motivated by adding Web of Science as a
  second catalogue source ([wos-starter-api.md](../docs/explorations/wos-starter-api.md)),
  which the diversified-retrieval direction would consume.
- `ExtractedDocument` quality gate — `extraction_status` enum + metrics.
  Defer until a stage 9+ consumer arrives — stages 7 and 8 are now
  full-text-only, so a missing or empty extraction is simply skipped
  (or recorded as `tei-parse-failure`) rather than routed to a fallback.
- Shared export-for-human-review helper across stages 3, 4, 9. Exports
  are XLSX-only now (CSV retired); stages 7 and 8 landed as separate
  self-contained `export.py` modules by design — the
  [full-text-only plan](done/fulltext-stages-full-text-only-and-review.md)
  deliberately declined to force a shared abstraction before stage 9
  shows what it needs. Each stage emits JSONL today; the open question
  is whether stages 3, 4, and 9 should share scaffolding once stage 9's
  reviewer-column ingestion is designed.

## Withdrawn

Directions that were considered and dropped. Each entry says what was
dropped and why, so the same idea is not re-proposed from the gap it leaves.

- **Abstract sidecar into screening.** Stage 2b
  [`abstract-lookup`](done/doi-abstract-lookup.md) writes an `AbstractRecord`
  sidecar keyed by work id, and no stage reads it. The planned fix was an
  `--abstracts` overlay on `screening-abstracts` that filled a null
  `Work.abstract` from the sidecar at read time. Backfilled abstracts were
  tested against screening and gave no additional benefit, so the overlay is
  not built and the sidecar stays unconsumed. Whether to delete the stage
  outright is open.
- **Stage 9 as a pass-through.** The design that copied stage 8 records to a
  `validated.jsonl` unchanged died with the stage 4 pass-through it was
  modelled on. Reviewer corrections arrive through the stage 8 XLSX workbook
  instead; see [adjudication-extraction.md](../docs/adjudication-extraction.md).
- **Zotero export probe.** Withdrawn with the Zotero-import open question,
  under [Cross-machine + storage convention](#in-flight) above.

## Latest review

[plans/done/review-2026-04-16.md](done/review-2026-04-16.md) indexes the three
sub-reviews ([surface](done/review-2026-04-16-surface.md),
[code](done/review-2026-04-16-code.md),
[system](done/review-2026-04-16-system.md)) and groups findings into seven
cross-cutting themes. Every queued item above traces to a finding
there.
