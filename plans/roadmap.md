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
  high-leverage simplifications before the first production run.
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
  underscores. Test
  helpers move to `tests/conftest.py`; mock-heavy seed/temperature
  call-site assertions go. Production code: −114 lines net.

- [LLM-stage timeouts and reviewer-xlsx restructure](done/llm-timeout-and-reviewer-xlsx.md)
  — fixes the `openai.APITimeoutError` that killed a production run
  mid-stage 8 (timeout + retries on the OpenAI
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
  `AbstractRecord` sidecar keyed
  by work id; the deduplicated catalogue is never rewritten in place. Runs
  between `catalogue-dedup` and `screening-abstracts`. Source clients take an
  injected `httpx.Client` (tested via `httpx.MockTransport`, no network);
  `--email`/`--api-key` with `.env` fallback, `Retry-After` honoured with a
  bounded backoff, `--skip-existing` re-processes only gaps. Removed again
  by the [pipeline restructure](done/pipeline-restructure.md).

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
  regression — `scripts/run-pipeline.sh` passed
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

- [Run manifest](done/run-manifest.md) — `data/manifest.json` is a typed
  `RunManifest {run, run_id, queries}` written once by `manifest-init`. Every
  stage defaults its input flags to the conventional path under `--data-dir`
  ([`layout.py`](../src/laglitsynth/layout.py)) and exits naming the upstream
  stage when that path is missing; `--run-id` resolves flag, else manifest,
  else exit, and no stage mints its own. The runner creates the manifest when
  absent and passes no paths. See [run-manifest.md](../docs/run-manifest.md).

- [Collaborator handoff loop](done/collaborator-handoff-loop.md) — the DOI
  handoff is `fulltext-retrieval-export` then `fulltext-retrieval-import
  --import-dir <folder> --source manual`. The bundle carries `title`/`year`
  on `pdf-manifest.csv`, a `no-doi.csv` worklist, and a `README.md` that asks
  the collaborator to send PDFs back as downloaded, unrenamed. `EXPORT_GAP=1`
  runs the export from the runner after stage 5.

- [Import matching tiers](done/import-matching-tiers.md) — import gathers
  every self-identifying DOI (Info dictionary, XMP, link annotations,
  first-page text) and keeps the first metadata hit present in
  `pdf-manifest.csv` (now the whole catalogue, see the
  [pipeline restructure](done/pipeline-restructure.md)); page text counts only when it names one work. The DOI
  pattern accepts the angle brackets in AMS DOIs. Measured on real publisher
  PDFs in
  [contributor-pdf-matching.md](../docs/explorations/contributor-pdf-matching.md);
  the residual that embeds no DOI is the title-matching question.

- [Import title matching](done/import-title-matching.md) — a returned PDF
  with no usable DOI is filed by the catalogue title in its metadata or
  page-1 title block (`rapidfuzz` ratio), accepted only as a clear,
  unshared winner, and never for a PDF whose metadata names a DOI outside
  the catalogue; the rule was chosen by hiding the DOI of DOI-confirmed
  store PDFs
  ([contributor-pdf-matching.md](../docs/explorations/contributor-pdf-matching.md#title-matching-measured-against-the-pdf-store)).

- [Unscreened sample run and packed handoff](done/unscreened-sample-run.md) —
  `catalogue-sample` (seeded subset as its own catalogue),
  `screening-abstracts --pass-all` (`unscreened` sentinel, no model call),
  `fulltext-retrieval-export --packs N` plus an `export-meta.json` that
  records how each bundle was made.

- [Decisions backend for abstract screening](done/decisions-screening-backend.md)
  — `screening-abstracts --backend decisions` scores abstracts with a hosted
  decisions model (TypeSafe Jev via OpenRouter) asking the criteria file's
  `decision` question; probability becomes the score, no JSON to parse.
  Criteria scope wording is
  [issue #26](https://github.com/geomar-od-lagrange/2026_laglitsynth/issues/26).

- [Pipeline restructure](done/pipeline-restructure.md) — mission statement:
  acquire, then analyse, meeting at a self-contained corpus
  ([corpus.md](../docs/corpus.md)); local LLMs, adjudication, abstract
  lookup and the screening/eligibility XLSX exports are gone. Implemented
  by [pipeline-restructure-implementation.md](done/pipeline-restructure-implementation.md):
  one OpenAI-compatible LLM endpoint (decisions for stages 3 and 7, chat
  for stage 8), `corpus-assemble` as the only place the screening threshold
  applies, stages 6–8 reading only `data/corpus/` and checking its
  `corpus_id`, and the collaborator procedure of
  [pdf-store.md](../docs/pdf-store.md) in the CLI: the export's screening
  filter is optional and its RIS carries `AN`, `UR` and `T2`, and import
  matches against the whole catalogue with `--source zotero-desktop |
  browser | institutional | manual`.

- [`--skip-existing` retries sentinel rows](done/skip-existing-retry-sentinels.md)
  — stages 7 and 8 skip only works with a successful row; the sentinel
  rows of the works sent again are dropped from the JSONL (atomic rewrite
  via the shared `io.drop_jsonl_rows`), so a run cut short by
  a budget limit or an outage resumes without hand-editing.

- [Usability docs](done/usability-docs.md) — the external-services runbook
  ([external-services.md](../docs/external-services.md)), one Prerequisites
  block shape (inputs, services, credentials) right after the
  introduction of every stage doc, and a leaner README. The Ollama, Apple
  Silicon and "STOP HERE" parts were dropped after the pipeline
  restructure; the plan's status note lists what was dropped and why.

- [Cross-machine + storage convention](../docs/cross-machine.md) — the
  project is the working directory, every stage runs from the repo root
  against a fixed `data/`, `.env` is per machine, and each `data/` subdir
  has one safe sync direction; collaborators run no stage and hand back
  folders of PDFs. The Zotero-import open question from
  [fulltext-retrieval-diversified.md](done/fulltext-retrieval-diversified.md)
  is withdrawn (see below).

## In flight

Nothing.

## Queued — ready to plan

- Catalogue search widening
  ([catalogue-search-widening.md](catalogue-search-widening.md)): a
  measured list of OpenAlex queries with per-query record caps, run from
  the review config. The query list is a candidate and gets revisited
  before the production run; measurements in
  [catalogue-search-widening.md](../docs/explorations/catalogue-search-widening.md).
- Recall-oriented abstract screening
  ([issue #31](https://github.com/geomar-od-lagrange/2026_laglitsynth/issues/31)):
  the screening question asks whether the abstract shows particle tracking,
  so a low score doesn't mean "confidently out" (see the comment in
  [the screening criteria](../examples/screening-criteria/lagrangian-oceanography.yaml)).
  Re-screen with an inverted wording and measure both against full-text
  eligibility verdicts on every PDF before choosing a threshold. Scope
  wording is
  [issue #26](https://github.com/geomar-od-lagrange/2026_laglitsynth/issues/26).
- Retrieval over every OpenAlex location of a work plus Semantic Scholar
  and arXiv ([issue #36](https://github.com/geomar-od-lagrange/2026_laglitsynth/issues/36); step 2 of the
  [pipeline restructure](done/pipeline-restructure.md)).
  `fulltext-retrieval` tries only OpenAlex's best open-access URL and
  Unpaywall; the store already holds PDFs from the other routes, fetched by
  one-off scripts ([pdf-store.md](../docs/pdf-store.md)), so this does not
  block the analysis run.

## Queued — driven by future stages

- Codebook refinement ([issue #37](https://github.com/geomar-od-lagrange/2026_laglitsynth/issues/37)), a work package of its own
  once the end-to-end workflow is settled. Known starting point: `config_available` and
  `rationale` come back filled for nearly every paper, because the
  questions don't separate configuration published as files from settings
  described in prose, or a rationale for the numerical choices from other
  reasons
  ([first-full-run-2026-11.md](../docs/explorations/first-full-run-2026-11.md#first-look-at-the-codebook)).
- Synthesis: one step replacing the former stages 10–12 (quantitative,
  thematic, narrative). It aggregates the extraction records into answers
  to RQ1.1–RQ1.3 and is the only aggregation across papers. Whether the
  RQ1.3 rationale grouping keeps human labelling is decided when it is
  planned; it waits for the first codebook results.
- [Two-pass extraction](../docs/two-pass-extraction.md) for stage 8 —
  deferred until phase 3 codebook review flags attention dilution on
  specific fields, or until the `llm-rejected` count shows full texts
  exceeding the model's context (nothing truncates them).
  `in_text_locations` is captured by stage 8 as ground truth for pass-1
  calibration.
- Agentic reading of a paper — orthogonal to the one-question-per-call
  stages. Chat UIs give the model tools over a PDF (embedding search,
  page/section lookup) so it can follow up instead of answering once. For
  this pipeline that would mean deploying a real tool-using agent per
  paper and keeping its transcripts as outputs. Revisit if per-call
  extraction, even per section, stays the quality limit. No plan yet.
- `Work` model additions — `source_catalogues: list[str]`,
  `catalogue_ids: dict[str, str]`, derived `is_peer_reviewed: bool |
  None`. Defer until a stage 7+ consumer arrives — stage 7 itself does
  not branch on these. Now also motivated by adding Web of Science as a
  second catalogue source ([wos-starter-api.md](../docs/explorations/wos-starter-api.md)),
  which the diversified-retrieval direction would consume.
- `ExtractedDocument` quality gate — `extraction_status` enum + metrics.
  Defer until a consumer needs it — stages 7 and 8 are full-text-only, so
  a missing or empty extraction is simply skipped (or recorded as
  `tei-parse-failure`) rather than routed to a fallback.
- Refining the [vocabulary](../docs/vocabulary.md) — waits for the first
  codebook results.

## Withdrawn

Directions that were considered and dropped. Each entry says what was
dropped and why, so the same idea is not re-proposed from the gap it leaves.

- **Abstract sidecar into screening, and stage 2b itself.**
  [`abstract-lookup`](done/doi-abstract-lookup.md) wrote an `AbstractRecord`
  sidecar keyed by work id that no stage read. The planned fix was an
  `--abstracts` overlay on `screening-abstracts` that filled a null
  `Work.abstract` from the sidecar at read time. Backfilled abstracts were
  tested against screening and gave no additional benefit, so the overlay
  was not built, and the [pipeline restructure](done/pipeline-restructure.md)
  deleted the stage. Works without an abstract get a `no-abstract`
  sentinel, which `corpus-assemble` includes so stage 7 judges them on the
  full text.
- **Stage 9 as a pass-through.** The design that copied stage 8 records to a
  `validated.jsonl` unchanged died with the stage 4 pass-through it was
  modelled on. Reviewer corrections arrive through the stage 8 XLSX workbook
  instead; stage 9 itself is dropped by the
  [pipeline restructure](done/pipeline-restructure.md).
- **Multi-run consensus.** Running the LLM stages several times and
  combining the results was planned as an optional robustness check. The
  [pipeline restructure](done/pipeline-restructure.md) drops consensus runs, and
  the plan is deleted.
- **Stages 10–12 as three stages.** Quantitative, thematic and narrative
  synthesis were specified as separate stages with their own artifacts
  (`statistics.json`, `rationale-taxonomy.json`, `synthesis-draft.md`).
  The [pipeline restructure](done/pipeline-restructure.md) replaces them with
  one planned synthesis step (queued above), whose outputs are designed
  with it.
- **Shared export-for-human-review helper.** The open question was
  whether the review exports of stages 3, 4, 7 and 9 should share
  scaffolding. Stage 4 and stage 9 are gone, and the
  [pipeline restructure](done/pipeline-restructure.md) dropped the screening and
  eligibility XLSX exports: verdicts are inspected on their
  `verdicts.jsonl`. `extraction-codebook-export` is the only workbook left,
  so there is nothing to share.
- **Citation-network analysis, author review and the feedback loops.**
  Optional extensions in the old [pipeline.md](../docs/pipeline.md):
  citation-graph centrality, a final author-review stage, and loops from
  screening review back into search or calibration. The restructure
  removes them: refining the search, criteria, codebook or vocabulary is
  ordinary editing of the configs followed by a re-run.
- **Zotero export probe and the Zotero-import open question.** Import reads
  identifiers out of the PDF and matches them against the catalogue, so
  Zotero's filename convention does not enter the design. The probe script
  and its note are removed; the reasoning is in
  [contributor-pdf-matching.md](../docs/explorations/contributor-pdf-matching.md).
- NESH (the Kiel HPC cluster) as a pipeline target is dropped, with its
  batch wrapper: the LLM stages call a hosted endpoint and GROBID runs in
  Docker on the laptop, which leaves nothing for the cluster to do.

## Latest review

[plans/done/review-2026-04-16.md](done/review-2026-04-16.md) indexes the three
sub-reviews ([surface](done/review-2026-04-16-surface.md),
[code](done/review-2026-04-16-code.md),
[system](done/review-2026-04-16-system.md)) and groups findings into seven
cross-cutting themes. Every queued item above traces to a finding
there.
