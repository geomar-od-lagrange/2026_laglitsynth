# Roadmap

One-page overview of what's done, in flight, and queued. Details live
in the linked plans and in the [2026-04-16 review](done/review-2026-04-16.md).
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

## In flight

- [LLM-stage timeouts and reviewer-xlsx restructure](llm-timeout-and-reviewer-xlsx.md)
  — fixes the `openai.APITimeoutError` that killed
  `nesh-pipeline-22047331` mid-stage 8 (timeout + retries on the OpenAI
  client in stages 3/7/8, new `llm-timeout` sentinel); flips stage 4
  to pass-through every null-score sentinel so `no-abstract` works
  reach fulltext-retrieval; gives stage 3 the same title/authors/year/
  abstract block the human reviewer sees so scoring is symmetric;
  reworks the stage-3 reviewer xlsx per [remarks.md](../remarks.md)
  with bibliographic header, verbatim criterion, percent-formatted
  score, reviewer placeholders, LLM verdict block collapsed by default.

## Queued — ready to plan

- (None.)

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
  not branch on these.
- `ExtractedDocument` quality gate — `extraction_status` enum + metrics.
  Defer until a stage 9+ consumer arrives — stages 7 and 8 both fell
  back to source-basis selection instead.
- Shared CSV-export-for-human-review helper across stages 3, 4, 7, 8,
  9. Each stage emits JSONL today; human spot-checking uses ad-hoc
  conversions.

## Latest review

[plans/done/review-2026-04-16.md](done/review-2026-04-16.md) indexes the three
sub-reviews ([surface](done/review-2026-04-16-surface.md),
[code](done/review-2026-04-16-code.md),
[system](done/review-2026-04-16-system.md)) and groups findings into seven
cross-cutting themes. Every queued item above traces to a finding
there.
