# Plan: Pre-prod cleanup

## Goal

Land the hold-the-prod fixes and the highest-leverage simplifications
identified in the whole-repo review, before the first NESH production
run. Outcome: a contributor reads
[scripts/run-pipeline.sh](../scripts/run-pipeline.sh) top-to-bottom and
walks the eight stage modules without surprises in CLI surface, env-var
plumbing, or documentation.

## Non-goals

No new features, no stage additions, no changes to the data model
beyond removing one dead enum branch and one redundant field
re-declaration. Stages 7 and 8 do not gain `--concurrency` here — that
is a separate piece of work tracked in
[docs/llm-concurrency.md](../docs/llm-concurrency.md). No new
abstractions, base classes, or stage-runner protocols: every package
in this plan deletes or inlines, never extracts.

## Target state

### CLI surface

Every stage tool exposes all parameters as `--flag` arguments. The
tool never reads from `.env` or `os.environ` for parameters that
affect a run. Scripts source `.env` and pass `--flag $VAR`. This
applies to `OPENALEX_API_KEY` (catalogue-fetch gains `--api-key`) and
`UNPAYWALL_EMAIL` (fulltext-retrieval gains `--email`). The
[docs/interfaces.md](../docs/interfaces.md) `--email` claim becomes
true; the runbook lists every flag.

`catalogue-fetch` drops the silent `--max-records=199` default; the
flag becomes purely opt-in. The pre-fetch `Query matches X works.`
print already serves as the warning before download.

The two screening-abstracts export commands collapse into one.
`screening-abstracts-export` takes `--format csv|xlsx` and dispatches
to one of two writer functions inside a single `export.py`. The
`-export-xlsx` subcommand and its file disappear.
`--n-subset` / `--subset-seed` are valid only with `--format xlsx`
and raise a clear error otherwise.

### Stages 7 and 8

`--skip-existing` reads the existing `screening-meta.json` /
`eligibility-meta.json` / `extraction-meta.json` and refuses to
proceed if the recorded `prompt_sha256` differs from the prompt the
current invocation would produce. Mixing prompt versions silently in
one verdict file is the failure mode being closed.

`ExtractionRecord` becomes a subclass of `_ExtractionPayload` rather
than re-declaring its 28 fields by hand. Field drift between the LLM
prompt schema and the validated record becomes structurally
impossible.

`_flatten_section` lifts to
[src/laglitsynth/fulltext_extraction/tei.py](../src/laglitsynth/fulltext_extraction/tei.py)
as `flatten_sections(tei: TeiDocument) -> list[str]`. Stages 7 and 8
import it. Stage 8 keeps `_truncate_blocks` next to its prompt; stage
7's `render_fulltext` collapses to one line.
`render_abstract(abstract) -> abstract` is inlined at both call sites
and the helper deleted.

### Scripts

[scripts/run-pipeline.sh](../scripts/run-pipeline.sh) and
[scripts/nesh-pipeline.sbatch](../scripts/nesh-pipeline.sbatch) source
`.env` (`set -a; source .env; set +a`) and pass every parameter as a
flag. The sbatch's Ollama and GROBID wait loops emit elapsed time on
both success and failure so we can see how close the 30 s / 180 s
upper bounds are to the real cold-start times. The `STOP_AFTER_STAGE`
regex relaxes to `^[0-9]+$` so the script doesn't need editing when
stage 9 lands.

### Plans and docs

Four implemented plans move to [plans/done/](done/):
[screening-abstracts-csv-export.md](screening-abstracts-csv-export.md),
[screening-abstracts-xlsx-export.md](screening-abstracts-xlsx-export.md),
[stage-7-fulltext-eligibility.md](stage-7-fulltext-eligibility.md),
[stage-8-extraction-codebook.md](stage-8-extraction-codebook.md).
[plans/roadmap.md](roadmap.md) is updated to reflect the move.

[docs/extraction-codebook.md](../docs/extraction-codebook.md) drops
the unsourced "~60 s/~140 s per paper" throughput claim (project rule:
no fabricated numbers). [docs/data-model.md](../docs/data-model.md)
and [docs/adjudication-extraction.md](../docs/adjudication-extraction.md)
fix their references to the deleted `_Base` symbol — the docs are
PEP/NEP-style design references and stay; only the wrong content
goes.

`docs/interfaces.md` is brought in sync with the new CLI surface
(every new flag, removed flag, removed enum) as the final opus pass.

### Cross-cutting cleanup

`_RunMeta` and `_LlmMeta` lose their leading underscores — they are
imported and used as public types by every stage's meta sidecar.
`read_works_jsonl` inlines into `read_jsonl(path, Work)` at all
callers; the helper is removed. `verdicts_path.write_text("")` in
[screening_abstracts/screen.py](../src/laglitsynth/screening_abstracts/screen.py)
becomes `path.unlink(missing_ok=True)` to match stages 7/8.
[cli.py](../src/laglitsynth/cli.py) tightens its imports + register
block. The `concurrency <= 1` legacy branch in `screen_works` deletes
(`ThreadPoolExecutor(max_workers=1)` already gives ordered sequential
dispatch). `RetrievalStatus.retrieved_preprint` and its label are
removed (no code emits it). The unused `"reject"` and `"skip"`
literals on `screening_adjudication.AdjudicationDecision.decision`
are removed until those branches actually land. The
`_preflight(args)` unused-arg in
[catalogue_fetch/fetch.py](../src/laglitsynth/catalogue_fetch/fetch.py)
loses its parameter. `pixi.lock` is committed for prod-run
reproducibility.

`tests/conftest.py` collects the duplicated fixtures
(`_make_work`, `_mock_openai_response`, `_write_works_jsonl`,
`_write_extractions_jsonl`, `_write_tei`) currently copy-pasted
across seven test files. The three large mock-heavy test files
(`test_extraction_codebook.py`, `test_fulltext_eligibility.py`,
`test_fulltext_retrieval.py`) drop assertions that pin implementation
(`mock_client.chat.completions.create.assert_called_with(...)`)
rather than behaviour.

## Implementation sequence

Four waves. Each numbered package is a single agent run; `pixi run
typecheck && pixi run test` must pass at the end of every package.
Files listed under each package are owned exclusively by that package
within its wave — no overlap with siblings. `docs/interfaces.md` is
not touched in waves 1–3; it is reconciled in wave 4.

### Wave 1 — five parallel sonnet agents

1. **W1.A — Catalogue-fetch overhaul.** Drop the `--max-records=199`
   default; `default=None` and update the help text to say so. Add
   `--api-key` (required); remove `load_dotenv()` /
   `os.environ.get("OPENALEX_API_KEY")` from the tool. Count
   validation drops in `search_openalex` and write the real number to
   the meta `validation_skipped`. Remove the duplicate post-fetch
   warning print. Drop the unused `args` param on `_preflight`.
   Files:
   [src/laglitsynth/catalogue_fetch/fetch.py](../src/laglitsynth/catalogue_fetch/fetch.py),
   [tests/test_catalogue_fetch.py](../tests/test_catalogue_fetch.py),
   [docs/catalogue-fetch.md](../docs/catalogue-fetch.md).
   Tests: extend `test_catalogue_fetch.py` with one case asserting
   `--api-key` plumbing through `pyalex.config.api_key` and one
   asserting `validation_skipped` reflects actual drops. Do not
   touch any script.

2. **W1.B — Fulltext-retrieval overhaul.** Add `--email` (required);
   remove `load_dotenv()` / `os.environ.get("UNPAYWALL_EMAIL")` from
   the tool. Remove `RetrievalStatus.retrieved_preprint` and its
   `_STATUS_LABELS` entry. Inline `_load_existing` (it's read once,
   in `run`). Files:
   [src/laglitsynth/fulltext_retrieval/retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py),
   [src/laglitsynth/fulltext_retrieval/models.py](../src/laglitsynth/fulltext_retrieval/models.py),
   [tests/test_fulltext_retrieval.py](../tests/test_fulltext_retrieval.py),
   [docs/fulltext-retrieval.md](../docs/fulltext-retrieval.md). Tests:
   one new case for `--email` plumbing into the Unpaywall request URL.
   Do not touch any script.

3. **W1.C — Stages 7+8 cleanup.** Add `flatten_sections(tei)` to
   [src/laglitsynth/fulltext_extraction/tei.py](../src/laglitsynth/fulltext_extraction/tei.py)
   alongside `Section`. Replace the two duplicated `_flatten_section`
   definitions in stage 7 and stage 8 prompts with imports of the
   shared helper. Inline `render_abstract` at both call sites and
   delete the helper. Make `ExtractionRecord` subclass
   `_ExtractionPayload` (drop the 28-field re-declaration; keep only
   the identification + meta fields). Add the `prompt_sha256`
   guard to `--skip-existing` in both stages: read the existing meta,
   compare to the freshly-computed hash, exit with a clear error if
   they differ. Update [docs/eligibility.md](../docs/eligibility.md)
   and [docs/extraction-codebook.md](../docs/extraction-codebook.md)
   to document the new `--skip-existing` semantics; in the same pass
   delete the unsourced "~60 s/~140 s per paper" line in
   `extraction-codebook.md`. Files:
   [src/laglitsynth/fulltext_eligibility/eligibility.py](../src/laglitsynth/fulltext_eligibility/eligibility.py),
   [src/laglitsynth/fulltext_eligibility/prompts.py](../src/laglitsynth/fulltext_eligibility/prompts.py),
   [src/laglitsynth/extraction_codebook/extract.py](../src/laglitsynth/extraction_codebook/extract.py),
   [src/laglitsynth/extraction_codebook/models.py](../src/laglitsynth/extraction_codebook/models.py),
   [src/laglitsynth/extraction_codebook/prompts.py](../src/laglitsynth/extraction_codebook/prompts.py),
   [src/laglitsynth/fulltext_extraction/tei.py](../src/laglitsynth/fulltext_extraction/tei.py),
   [tests/test_fulltext_eligibility.py](../tests/test_fulltext_eligibility.py),
   [tests/test_extraction_codebook.py](../tests/test_extraction_codebook.py),
   [tests/test_fulltext_tei.py](../tests/test_fulltext_tei.py),
   [docs/eligibility.md](../docs/eligibility.md),
   [docs/extraction-codebook.md](../docs/extraction-codebook.md).
   Tests: one case in `test_fulltext_tei.py` covering
   `flatten_sections` over a nested-section fixture; one case per
   stage covering the prompt-hash refusal path.

4. **W1.D — Docs and plans housekeeping.** Fix the `_Base` references
   in [docs/data-model.md](../docs/data-model.md) and
   [docs/adjudication-extraction.md](../docs/adjudication-extraction.md):
   the symbol no longer exists; the docs should describe the
   per-stage `_RunMeta`-based shape that replaced it. `git mv` four
   plan files into `plans/done/` and update
   [plans/roadmap.md](roadmap.md) to point at the new locations.
   Files:
   [docs/data-model.md](../docs/data-model.md),
   [docs/adjudication-extraction.md](../docs/adjudication-extraction.md),
   [plans/screening-abstracts-csv-export.md](screening-abstracts-csv-export.md),
   [plans/screening-abstracts-xlsx-export.md](screening-abstracts-xlsx-export.md),
   [plans/stage-7-fulltext-eligibility.md](stage-7-fulltext-eligibility.md),
   [plans/stage-8-extraction-codebook.md](stage-8-extraction-codebook.md),
   [plans/roadmap.md](roadmap.md). No tests; no code.

5. **W1.E — Screening-abstracts export consolidation.** Merge
   `export_xlsx.py` into `export.py`. Single subparser
   `screening-abstracts-export` with `--format csv|xlsx`; CSV path is
   the existing `export_review_csv`; XLSX path is the existing
   `export_review_xlsx`. `--n-subset` and `--subset-seed` are
   accepted only with `--format xlsx` (raise `SystemExit` otherwise).
   Delete
   [src/laglitsynth/screening_abstracts/export_xlsx.py](../src/laglitsynth/screening_abstracts/export_xlsx.py)
   and
   [tests/test_screening_abstracts_export_xlsx.py](../tests/test_screening_abstracts_export_xlsx.py)
   (their content folds into the consolidated files). Drop the
   second screening-export subparser registration in
   [cli.py](../src/laglitsynth/cli.py). Update
   [docs/screening-abstracts.md](../docs/screening-abstracts.md) to
   document the new shape. Files:
   [src/laglitsynth/screening_abstracts/export.py](../src/laglitsynth/screening_abstracts/export.py),
   [src/laglitsynth/screening_abstracts/export_xlsx.py](../src/laglitsynth/screening_abstracts/export_xlsx.py)
   (delete),
   [src/laglitsynth/cli.py](../src/laglitsynth/cli.py),
   [tests/test_screening_abstracts_export.py](../tests/test_screening_abstracts_export.py),
   [tests/test_screening_abstracts_export_xlsx.py](../tests/test_screening_abstracts_export_xlsx.py)
   (delete),
   [docs/screening-abstracts.md](../docs/screening-abstracts.md).
   Tests: the CSV and XLSX cases consolidate; one new case asserts
   that `--n-subset` with `--format csv` is rejected.

### Wave 2 — two parallel sonnet agents (after wave 1 lands)

6. **W2.F — Scripts update.** In both
   [scripts/run-pipeline.sh](../scripts/run-pipeline.sh) and
   [scripts/nesh-pipeline.sbatch](../scripts/nesh-pipeline.sbatch),
   source `.env` (`set -a; source .env; set +a`) and pass
   `--api-key "$OPENALEX_API_KEY"` to `catalogue-fetch` and
   `--email "$UNPAYWALL_EMAIL"` to `fulltext-retrieval`. In the
   sbatch, replace the bare wait loops with timed loops that print
   `==> Ollama up in Ns` / `==> GROBID up in Ns` on success and
   `... did not come up within Ns` (with the real elapsed) on
   failure. Relax the `STOP_AFTER_STAGE` regex in
   `run-pipeline.sh` to `^[0-9]+$`. The script does not invoke any
   export, so the `--format` flag does not need to appear here.
   Files: the two scripts only. Acceptance: `bash -n` parses both;
   manual dry-run with `STOP_AFTER_STAGE=1` and a real `.env` exits
   cleanly.

7. **W2.G — Cross-cutting Python cleanup.** Rename `_RunMeta` →
   `RunMeta` and `_LlmMeta` → `LlmMeta` everywhere; this touches
   [src/laglitsynth/models.py](../src/laglitsynth/models.py) and
   every stage's main module plus its tests. Inline
   `read_works_jsonl` into `read_jsonl(path, Work)` at every caller,
   then delete the helper from
   [src/laglitsynth/io.py](../src/laglitsynth/io.py). Replace
   `verdicts_path.write_text("")` in
   [screening_abstracts/screen.py](../src/laglitsynth/screening_abstracts/screen.py)
   with `path.unlink(missing_ok=True)`. Delete the `concurrency <= 1`
   sequential branch in `screen_works`. Delete the `"reject"` and
   `"skip"` literals on
   [screening_adjudication/models.py](../src/laglitsynth/screening_adjudication/models.py)'s
   decision field. Tighten
   [cli.py](../src/laglitsynth/cli.py): collapse the long-form
   imports + per-line `build_*_subparser` calls into a flat list.
   Commit `pixi.lock`. Files: every stage's main module, every
   stage's `models.py`, `models.py`, `io.py`, `cli.py`, every test
   file (rename only). Tests: existing tests pass after rename;
   add no new tests in this package — all changes are mechanical or
   covered by existing cases.

### Wave 3 — one sonnet agent (after wave 2 lands)

8. **W3.H — Test conftest extraction.** Create `tests/conftest.py`
   holding the duplicated `_make_work`, `_mock_openai_response`,
   `_write_works_jsonl`, `_write_extractions_jsonl`, `_write_tei`
   fixtures (one signature each — pick the most-permissive variant
   from the existing copies, parametrise where signatures diverge).
   Update every `tests/test_*.py` file to import the fixture rather
   than redefine it. In the same pass, drop assertions in
   `test_extraction_codebook.py`, `test_fulltext_eligibility.py`, and
   `test_fulltext_retrieval.py` that pin implementation details
   (`mock.assert_called_with(...)` on framework methods rather than
   on the unit under test). Files: every `tests/test_*.py` plus the
   new `tests/conftest.py`. Acceptance: `pixi run test` passes; the
   three big test files shrink visibly.

### Wave 4 — opus review

9. **W4 — Reconcile and ship.** Pull every wave-1/2/3 change. Run
   `pixi run typecheck && pixi run test`; fix anything that broke.
   Update [docs/interfaces.md](../docs/interfaces.md) once, against
   the final CLI: `--api-key` on catalogue-fetch, `--email` on
   fulltext-retrieval, `--format` on screening-abstracts-export, no
   more `retrieved_preprint`. Verify the `Already good` items from
   the whole-repo review remain untouched. Move this plan to
   `plans/done/` and update the roadmap.

## Follow-ups

`docs/explorations/ollama-throughput.md` and similar exploration notes
are not in scope: per AGENTS.md they may stay messy. Stage 7/8
`--concurrency` is the next piece of work, separately tracked.
`pixi.lock` arrives in W2.G; if `pixi.lock` regeneration produces a
large diff, leave the regeneration as its own follow-up commit.

## Risks

The sole real coordination risk is wave-1 packages writing to files a
sibling also touches. The package boundaries above were drawn so each
package's file set is disjoint from its siblings'; the only files
multiple packages might want — `docs/interfaces.md` and
`scripts/*` — are explicitly deferred to wave 4 and wave 2
respectively. If a wave-1 agent finds it needs a file outside its
package list, it stops and reports rather than reaching across.

The `--api-key` and `--email` flags moving from env-only to flag-only
is an internal-API break; per AGENTS.md "no backwards-compat" this is
fine, but the W2.F scripts update must land in the same merge as
W1.A and W1.B or the scripts break.

Renaming `_RunMeta` / `_LlmMeta` in W2.G is a wide mechanical change;
agents must not also start refactoring meta shapes during the rename
or the diff becomes unreviewable. The package description is
explicit about rename-only.

## Critical files

Source modules touched:
[catalogue_fetch/fetch.py](../src/laglitsynth/catalogue_fetch/fetch.py),
[fulltext_retrieval/retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py),
[fulltext_retrieval/models.py](../src/laglitsynth/fulltext_retrieval/models.py),
[fulltext_eligibility/eligibility.py](../src/laglitsynth/fulltext_eligibility/eligibility.py),
[fulltext_eligibility/prompts.py](../src/laglitsynth/fulltext_eligibility/prompts.py),
[extraction_codebook/extract.py](../src/laglitsynth/extraction_codebook/extract.py),
[extraction_codebook/models.py](../src/laglitsynth/extraction_codebook/models.py),
[extraction_codebook/prompts.py](../src/laglitsynth/extraction_codebook/prompts.py),
[fulltext_extraction/tei.py](../src/laglitsynth/fulltext_extraction/tei.py),
[screening_abstracts/screen.py](../src/laglitsynth/screening_abstracts/screen.py),
[screening_abstracts/export.py](../src/laglitsynth/screening_abstracts/export.py),
[screening_adjudication/models.py](../src/laglitsynth/screening_adjudication/models.py),
[io.py](../src/laglitsynth/io.py),
[models.py](../src/laglitsynth/models.py),
[cli.py](../src/laglitsynth/cli.py).

Scripts: [run-pipeline.sh](../scripts/run-pipeline.sh),
[nesh-pipeline.sbatch](../scripts/nesh-pipeline.sbatch).

Docs touched:
[catalogue-fetch.md](../docs/catalogue-fetch.md),
[fulltext-retrieval.md](../docs/fulltext-retrieval.md),
[eligibility.md](../docs/eligibility.md),
[extraction-codebook.md](../docs/extraction-codebook.md),
[screening-abstracts.md](../docs/screening-abstracts.md),
[data-model.md](../docs/data-model.md),
[adjudication-extraction.md](../docs/adjudication-extraction.md),
[interfaces.md](../docs/interfaces.md) (wave 4 only).

Plans archived to `done/`:
[screening-abstracts-csv-export.md](screening-abstracts-csv-export.md),
[screening-abstracts-xlsx-export.md](screening-abstracts-xlsx-export.md),
[stage-7-fulltext-eligibility.md](stage-7-fulltext-eligibility.md),
[stage-8-extraction-codebook.md](stage-8-extraction-codebook.md).
