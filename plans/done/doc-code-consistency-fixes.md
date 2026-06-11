# Plan: doc/code consistency fixes (audit follow-up)

## Goal

Close the findings from the three-agent documentation audit (Agent A
extracted 440 checkable facts from the authoritative docs; B checked
them against code; C found code-present / doc-absent gaps; a manual
28-fact spot-check validated B's silent confirmations at 100%). The
audit turned up one real regression — the bundled runner
[`scripts/run-pipeline.sh`](../../scripts/run-pipeline.sh) passes flags the
diversified-retrieval + sticky-retrieval refactors removed, so it cannot
reach stages 5–8 (and NESH inherits this via
[`nesh-pipeline.sbatch`](../../scripts/nesh-pipeline.sbatch)) — plus a set
of doc/code inconsistencies, nine doc/doc conflicts to resolve toward
the code, and a batch of operator-visible behaviours the code has but no
doc names. After this plan the runner runs end-to-end again, every
documented flag / path / field / default matches code, and the
behaviours C surfaced are documented.

## Design decisions

Runner becomes sticky-aware. Stage 5's `--output-dir "$ROOT/fulltext-retrieval"`
becomes `--data-dir "$ROOT"` and `--skip-existing` is dropped (sticky is
now the default), so PDFs land at `$ROOT/pdfs/`; stage 6's `--pdf-dir`
follows to `$ROOT/pdfs`. This is the load-bearing fix — everything else
is documentation.

Every doc/doc conflict is resolved toward the code (the code is ground
truth, established by B with file:line): DroppedRecord fields are
`dropped_work_id`/`survived_work_id` (data-model.md was wrong); stage 3's
only output is the verdict sidecar (pipeline.md's "screened/rejected"
was wrong); the PDF store is `data/pdfs/` (fulltext-extraction.md's
`data/fulltext-retrieval/pdfs/` was wrong); stage 6's output is
`extraction.jsonl` and it has no `--run-id` (external-services.md's
crash-recovery snippet was wrong on both); the shipped `laglit-eligibility`
tag is baked from `gemma3:4b` (bake-model.md's `llama3.1:8b` example
disagreed with the sbatch); there is no `source_basis` field
(glossary.md described one); stages 7/8 do honour `--concurrency`
(README's "sequential" note was stale); interfaces.md's stage-7/8 flag
lists must gain `--concurrency`/`--num-ctx`; and llm-concurrency.md's
"no resume" is reconciled with the `--skip-existing` resume mode those
stages actually have.

C's two plausibly-intentional gaps stay undocumented by design:
`WOS_STARTER_API_KEY` lives only in an exploration script, and the
`_REVIEWER_*` placeholder strings are internal export scaffolding. Every
other HIGH/MED/LOW gap is documented.

## Implementation sequence

`pixi run typecheck` and `pixi run test` pass between commits (only the
code edits below can affect them; the doc edits cannot).

### Code / runner

1. [`scripts/run-pipeline.sh`](../../scripts/run-pipeline.sh): stage 5 (lines
   102–109) — replace `--output-dir "$ROOT/fulltext-retrieval"` with
   `--data-dir "$ROOT"` and delete the `--skip-existing` line (sticky is
   the default; `--refetch` is the opt-in we do *not* want here). Stage 6
   (line 113) — `--pdf-dir "$ROOT/fulltext-retrieval/pdfs"` →
   `--pdf-dir "$ROOT/pdfs"`. Fix the header comment (line 17) "N max
   records per stage" → max records fetched from OpenAlex at stage 1
   (the cap is applied once at fetch and flows down the data, not
   re-passed as a flag to later stages).

2. [`src/laglitsynth/fulltext_retrieval/models.py`](../../src/laglitsynth/fulltext_retrieval/models.py)
   line 25: the `pdf_path` comment says `"data/pdfs/<stem>.pdf"` but the
   stored value is `pdfs/<stem>.pdf` (`retrieve.py:217`). Correct the
   comment to match the value.

### Doc/code inconsistencies (B)

3. [docs/fulltext-extraction.md](../../docs/fulltext-extraction.md): `--grobid-url`
   is optional with default `http://localhost:8070` (not "required");
   add the `--timeout` flag (default `120.0`s per paper) to the
   flag list; the `--pdf-dir` example path is `data/pdfs/` (not
   `data/fulltext-retrieval/pdfs/`); delete the "Extraction quality gate
   / documents flagged in the extraction record" claim — there is no
   quality gate and `ExtractedDocument` has no quality field (a GROBID
   failure simply produces no record; the empty/malformed-TEI gate lives
   downstream as `tei-parse-failure`); note `grobid_version` falls back
   to the literal `"unknown"` when the `/api/version` probe fails.

4. [docs/grobid-lessons.md](../../docs/grobid-lessons.md): correct the "no
   `<div>` → single 'Body' section" claim — `sections()` returns an empty
   list when there are no top-level `<div>`s, which stages 7/8 record as
   `tei-parse-failure` (not a graceful single-section fallback).

5. [README.md](../../README.md): delete the stale "Stages 7 and 8 do not yet
   honour `LLM_CONCURRENCY` — they call Ollama sequentially" note (both
   register `--concurrency` and dispatch through `map_concurrent`); fix
   the "propagated as `--max-records` to each downstream stage" wording
   (the runner passes `--max-records` only to stage 1).

6. [docs/catalogue-fetch.md](../../docs/catalogue-fetch.md): `read_works_jsonl()`
   does not exist — the reader is `read_jsonl(path, Work)`; name the
   `FetchMeta` fields (`total_count`, `records_written`); note the
   retry backoff factor `0.5` and `per_page=200`; note the meta sidecar
   for a custom `-o foo.jsonl` is `foo.meta.json` (`with_suffix`).

7. [docs/extraction-codebook.md](../../docs/extraction-codebook.md): `CHAR_BUDGET`
   is `100_000`, empirically sized (largest observed paper 86,968 chars),
   not a "tuning placeholder"; document that LLM bool answers are coerced
   to the literal strings `"yes"`/`"no"` and list answers joined with
   `" / "` in record fields.

8. [docs/abstract-lookup.md](../../docs/abstract-lookup.md): document the
   `SEMANTIC_SCHOLAR_API_KEY` `.env` fallback for `--api-key` (announced
   on stderr); give the concrete retry constants (`MAX_RATE_LIMIT_RETRIES=3`,
   `MAX_BACKOFF_SECONDS=30`) and the `User-Agent`.

### Doc/doc conflicts resolved toward code (B)

9. [docs/external-services.md](../../docs/external-services.md): crash-recovery
   names the stage-6 output `extraction.jsonl` (not `extraction-records.jsonl`)
   and drops the `--run-id` from the stage-6 invocation (stage 6 has no
   such flag); document the Ollama model-pulled preflight gate (a run
   aborts with `ollama pull X` guidance if the model/baked tag is absent).

10. [docs/bake-model.md](../../docs/bake-model.md): the `laglit-eligibility`
    example bakes from `gemma3:4b` (matching the shipped sbatch), not
    `llama3.1:8b`.

11. [docs/pipeline.md](../../docs/pipeline.md) §3: stage 3 produces a verdict
    sidecar only — no "screened catalogue, rejected records".

12. [docs/data-model.md](../../docs/data-model.md): `DroppedRecord` fields are
    `dropped_work_id` / `survived_work_id` / `rule`; name the mandatory
    `Work` fields whose absence forces a `validation_skipped` drop; surface
    `is_retracted: bool | None` as a review-relevant field.

13. [docs/interfaces.md](../../docs/interfaces.md): add `--concurrency` and
    `--num-ctx` to the stage-7 and stage-8 entries under §Existing
    subcommands; name the `RetrievalMeta` fields (`total_works`,
    `retrieved_count`, `missing_count`, `by_source`); note `--screening-threshold`
    is `float` on stages 5/7 but `int` on stage 3.

14. [docs/glossary.md](../../docs/glossary.md): remove the "source basis field"
    from §source basis / §corpus / §extraction record — stages 7/8 are
    full-text-only and carry no such field.

15. [docs/llm-concurrency.md](../../docs/llm-concurrency.md): reconcile "clean
    rerun, not resume" with the `--skip-existing` resume mode stages 7/8
    have (skip-existing within a pinned `--run-id` dir loads prior
    verdicts/records and skips done work_ids; without it the output is
    truncated); fix the broken `fulltext-eligibility.md` link →
    `eligibility.md`.

### Undocumented behaviours (C)

16. Document that stages 5/7/8 silently **drop** catalogue works absent
    from the screening/eligibility verdicts file (the `_active_works`
    `if sv is None: continue`): add to
    [docs/fulltext-retrieval.md](../../docs/fulltext-retrieval.md) (intro),
    [docs/eligibility.md](../../docs/eligibility.md) §Input, and the
    [interfaces.md](../../docs/interfaces.md) §Resolution note.

17. [docs/screening-abstracts.md](../../docs/screening-abstracts.md): name the
    `ScreeningMeta.llm_parse_failure_count` / `llm_timeout_count` counters.

18. [docs/fulltext-retrieval.md](../../docs/fulltext-retrieval.md): the PDF
    download client uses a `timeout=30.0`s and a
    `User-Agent: laglitsynth/0.1 (mailto:{email})` header (distinct from
    the 1 req/s per-domain rate limit).

19. [docs/eligibility.md](../../docs/eligibility.md) and
    [docs/extraction-codebook.md](../../docs/extraction-codebook.md): describe
    the XLSX review-export sheet layout (Index sheet with reviewer-identity
    rows; per-work sheets with the structured reviewer block and the
    collapsed LLM block), matching the level of detail stage 3 already has.

20. GROBID startup time: keep both numbers but clarify — 30–60s typical,
    30–90s on a cold NESH node (so F189 is no longer read as a
    contradiction).

## Follow-ups

`WOS_STARTER_API_KEY` (exploration-script-only) and the `_REVIEWER_*`
export placeholder strings are left undocumented by design — internal
surface a pipeline operator never sets.

## Critical files

- [scripts/run-pipeline.sh](../../scripts/run-pipeline.sh),
  [src/laglitsynth/fulltext_retrieval/models.py](../../src/laglitsynth/fulltext_retrieval/models.py)
- [README.md](../../README.md) and the touched docs under [docs/](../../docs/):
  fulltext-extraction, grobid-lessons, catalogue-fetch, extraction-codebook,
  abstract-lookup, external-services, bake-model, pipeline, data-model,
  interfaces, glossary, llm-concurrency, screening-abstracts,
  fulltext-retrieval, eligibility.
