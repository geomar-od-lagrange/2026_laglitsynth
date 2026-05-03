# Verdicts-only cutover

## Goal

Finish the [flag-don't-filter cutover](done/flag-dont-filter-cutover.md)
that landed half-way: today every gate stage still emits a derived
`Work`-record copy alongside its verdict sidecar, and a pass-through
stage 4 exists only to write one of them. This plan kills the derived
files, deletes stage 4, and implements real dedup for stage 2 — which
the doc already specifies but the code currently no-ops. After landing,
the dedup catalogue at
[`data/catalogue-dedup/deduplicated.jsonl`](../data/catalogue-dedup/deduplicated.jsonl)
is the single source of `Work` records pipeline-wide; every downstream
stage joins inline against verdict sidecars at read time. Each consumer
joins only against its immediately-upstream gate — earlier gates are
implicitly applied by the gate above.

## Non-goals

- Building [`laglitsynth.resolve`](../src/laglitsynth/) as a shared
  cross-stage helper. Three inline-join copies (stages 5, 7, 8) is
  below the abstraction threshold; defer until stage 9+ adds a fourth
  consumer.
- Pipeline-level config file (`A1` from
  [usability-review.md](../docs/explorations/usability-review.md));
  individual `--screening-threshold` flags will appear on stages 5 and
  7, with the wrapper threading the same value. P8 is documented but
  not solved here.
- `--num-ctx`, `--concurrency`, `bake-model`, `.env` fallback,
  preflight error split (the second-bucket usability findings).
  Independent commits.
- README's `## Tools` numbering and `external-services.md`. Pure-doc
  hygiene; separate commit.
- Reviewer-export workflow rewire (XLSX export stays on stage 3 with
  current shape).

## Target state

### Stage shape after cutover

| Stage | Inputs | Outputs |
|---|---|---|
| 1 `catalogue-fetch` | OpenAlex query | `data/catalogue-fetch/<slug>_<ts>.jsonl` |
| 2 `catalogue-dedup` | `--input <fetch>.jsonl` (or glob) | `data/catalogue-dedup/{deduplicated,dropped}.jsonl` + meta |
| 3 `screening-abstracts` | `--input deduplicated.jsonl` | `data/screening-abstracts/<run-id>/{verdicts.jsonl,screening-meta.json,config.yaml}` |
| 5 `fulltext-retrieval` | `--catalogue + --screening-verdicts + --screening-threshold + --email + ...` | `retrieval.jsonl + pdfs/ + manual/ + unretrieved.txt` + meta |
| 6 `fulltext-extraction` | `--pdf-dir + --grobid-url + ...` | `extraction.jsonl + tei/` + meta |
| 7 `fulltext-eligibility` | `--catalogue + --screening-verdicts + --screening-threshold + --extractions + ...` | `<run-id>/{verdicts.jsonl,eligibility-meta.json,config.yaml}` (no `eligible.jsonl`) |
| 8 `extraction-codebook` | `--catalogue + --eligibility-verdicts + --extractions + ...` | `<run-id>/{records.jsonl,extraction-codebook-meta.json,config.yaml}` |

Stage 4 (`screening-adjudication`) is deleted entirely.

### Files removed

- [`src/laglitsynth/screening_adjudication/`](../src/laglitsynth/screening_adjudication/)
  — entire package.
- [`tests/test_screening_adjudication.py`](../tests/test_screening_adjudication.py).
- [`docs/screening-adjudication.md`](../docs/screening-adjudication.md).
- The subparser registration in
  [`src/laglitsynth/cli.py`](../src/laglitsynth/cli.py).

### Files no longer written

- `data/screening-adjudication/included.jsonl` — stage 4 gone.
- `data/screening-adjudication/verdicts.jsonl` — stage 4 gone.
- `data/screening-adjudication/adjudication-meta.json` — stage 4 gone.
- `data/fulltext-eligibility/<run-id>/eligible.jsonl` — stage 7 stops
  emitting; stage 8 reads `verdicts.jsonl` instead and filters inline.

### Stage 2 — real dedup

[`src/laglitsynth/catalogue_dedup/dedup.py`](../src/laglitsynth/catalogue_dedup/dedup.py)
is replaced. Spec from
[`docs/catalogue-dedup.md`](../docs/catalogue-dedup.md):

- Rule 1: exact match on `Work.id`.
- Rule 2: exact match on normalised DOI (lowercase, strip
  `https://doi.org/` / `http://doi.org/` / `https://dx.doi.org/`
  prefix).
- Rule 3: exact match on `(normalised_title, first_author_lowercase,
  publication_year)` tuple. Title normalisation: lowercase, strip
  punctuation, collapse whitespace. Works without a usable first
  author or without a `publication_year` skip this rule.

When a duplicate is detected, retain the record with the most complete
metadata (prefer DOI present, then more `authorships` entries, then
first-encountered as tiebreak). Dropped records go to
`dropped.jsonl` with the surviving record's `work_id` and the rule
that matched.

Multi-source merge: stage 2 grows a `--input` glob/multi-flag so the
user can `laglitsynth catalogue-dedup --input "data/catalogue-fetch/*.jsonl"
--output-dir data/catalogue-dedup/` without a manual `cat`.
[`docs/catalogue-dedup.md`](../docs/catalogue-dedup.md) L82–84
becomes "the stage handles multi-file input directly."

### Inline join shape (stages 5, 7, 8)

Each downstream stage gets a small file-local helper. Three copies on
purpose; no shared module.

Stage 5:

```python
verdicts = {v.work_id: v for v in read_jsonl(screening_verdicts_path, ScreeningVerdict)}
for w in read_jsonl(catalogue_path, Work):
    sv = verdicts.get(w.id)
    if sv is None:
        continue
    if sv.relevance_score is not None and sv.relevance_score < screening_threshold:
        continue
    yield w
```

Null-score sentinels (`no-abstract`, `llm-parse-failure`,
`llm-timeout`) ride through, matching the deleted stage 4's
pass-through behaviour. Stage 7 uses the same shape against the
catalogue + screening verdicts + threshold.

Stage 8:

```python
ev = {v.work_id: v for v in read_jsonl(eligibility_verdicts_path, EligibilityVerdict)}
catalogue = {w.id: w for w in read_jsonl(catalogue_path, Work)}
for v in ev.values():
    if v.eligible is not True:
        continue
    yield catalogue[v.work_id]
```

No screening flags. The eligibility verdict is sufficient because
stage 7 only ran on above-threshold works to begin with.

### Run-id surface (ride-along)

Stages 3 and 7 print `Run dir: <data-dir>/<stage-subdir>/<run-id>/` to
stderr at the end of `run()`. Stage 8 already does this implicitly.
Resolves the most painful manual-user surprise (P1 in
[usability-review.md](../docs/explorations/usability-review.md)) at
trivial cost.

## Design decisions

**Three `--screening-threshold` flags.** Stages 3 (informational), 5
(gating), 7 (gating). Wrapper threads the same value to all three. The
maintenance hazard is real but bounded; the pipeline-level config that
collapses it is a separate plan.

**Stage 2 multi-input via glob.** The wrapper currently writes a
single `catalogue.jsonl` directly, sidestepping the multi-file
question. Real users running multiple OpenAlex queries will hit it
immediately. Glob support is ~5 LOC and unblocks the documented
multi-source workflow.

**Stage 8 takes `--catalogue` despite the eligibility verdict file
already keying by `work_id`.** Reason: stage 8's LLM input falls back
to the abstract when the TEI is missing, so it needs the `Work`
record, not just an ID. Eligibility verdicts don't carry abstracts.

**`generate-run-id` subcommand stays.** The wrapper still uses it; the
new stderr print makes it discoverable for manual users without
removing it.

**Verdict gate cascades.** Each consumer joins against the gate
immediately above it: stage 5 against screening, stage 7 against
screening, stage 8 against eligibility. Stage 8 does *not* re-apply
the screening threshold — implicit through stage 7's input filtering.
This keeps each stage's CLI surface to one verdict file.

## Implementation sequence

Six commits, each leaving `pixi run typecheck` and `pixi run test`
green.

### 1. Stage 2 real dedup

Replace [`src/laglitsynth/catalogue_dedup/dedup.py`](../src/laglitsynth/catalogue_dedup/dedup.py)
with the three-rule implementation from `docs/catalogue-dedup.md`.
Grow `--input` to accept a glob or multiple paths.
[`tests/test_catalogue_dedup.py`](../tests/test_catalogue_dedup.py)
gets full coverage:
`test_dedup_by_openalex_id`, `test_dedup_by_doi_normalisation`,
`test_dedup_by_title_author_year`, `test_dedup_keeps_most_complete`,
`test_dropped_jsonl_records_rule`, `test_multi_input_glob`,
`test_works_without_doi_use_title_author_year`,
`test_no_duplicates_passes_through_unchanged`. No CLI shape change for
downstream stages — stage 3's `--input deduplicated.jsonl` still
points at the same file.

### 2. Delete stage 4 + rewire stage 5 + wrapper update

Atomic commit. Delete
[`src/laglitsynth/screening_adjudication/`](../src/laglitsynth/screening_adjudication/),
the subparser entry in
[`src/laglitsynth/cli.py`](../src/laglitsynth/cli.py),
[`tests/test_screening_adjudication.py`](../tests/test_screening_adjudication.py),
and [`docs/screening-adjudication.md`](../docs/screening-adjudication.md).
Stage 5 ([`src/laglitsynth/fulltext_retrieval/retrieve.py`](../src/laglitsynth/fulltext_retrieval/retrieve.py))
swaps `--input` for `--catalogue` + `--screening-verdicts` +
`--screening-threshold`; the file-local `_active_works` helper
materialises the inline join. Wrapper
([`scripts/run-pipeline.sh`](../scripts/run-pipeline.sh) and
[`scripts/nesh-pipeline.sbatch`](../scripts/nesh-pipeline.sbatch))
loses the stage 4 invocation and threads the new flags into stage 5.
[`tests/test_fulltext_retrieval.py`](../tests/test_fulltext_retrieval.py)
gets a `test_active_works_threshold` and a
`test_null_score_sentinels_ride_through`; the existing
`--input`-based tests are rewritten.

### 3. Stage 7 rewire

[`src/laglitsynth/fulltext_eligibility/eligibility.py`](../src/laglitsynth/fulltext_eligibility/eligibility.py)
swaps `--catalogue <included.jsonl>` for `--catalogue
<deduplicated.jsonl>` + `--screening-verdicts` +
`--screening-threshold`. Same `_active_works` helper as stage 5.
Stage 7 still emits `eligible.jsonl` in this commit so stage 8
keeps working; that file goes away in step 4. Wrapper updated for
stage 7 invocation.
[`tests/test_fulltext_eligibility.py`](../tests/test_fulltext_eligibility.py)
gets new tests for the threshold join and updates the `--catalogue`
fixture from `included.jsonl` to `deduplicated.jsonl`.

### 4. Stage 8 rewire + stage 7 drops `eligible.jsonl`

Atomic commit.
[`src/laglitsynth/extraction_codebook/extract.py`](../src/laglitsynth/extraction_codebook/extract.py)
swaps `--eligible <eligible.jsonl>` for `--catalogue
<deduplicated.jsonl>` + `--eligibility-verdicts`. Inline join filters
verdicts to `eligible is True` and looks up the `Work` from the
catalogue.
[`src/laglitsynth/fulltext_eligibility/eligibility.py`](../src/laglitsynth/fulltext_eligibility/eligibility.py)
stops emitting `eligible.jsonl`; remove the catalogue-rebuild block at
[`eligibility.py:455–460`](../src/laglitsynth/fulltext_eligibility/eligibility.py).
Wrapper updated for stage 8.
[`tests/test_extraction_codebook.py`](../tests/test_extraction_codebook.py)
gets a `test_eligibility_gate_filters_to_eligible_true` and
`test_non_eligible_works_are_skipped`; the `--eligible` fixture path
becomes `--eligibility-verdicts` + `--catalogue`.
[`tests/test_fulltext_eligibility.py`](../tests/test_fulltext_eligibility.py)
loses every assertion about `eligible.jsonl`.

### 5. Run-id stderr print

Stages 3 and 7 print `Run dir: <output-dir>` to stderr at the end of
`run()`. Stage 8 already prints `Output dir:` near the start; mirror
the same line at the end so completion-line scrapers always find it.
Tests assert the line is on stderr.

### 6. Doc sweep

One commit covering every doc the cutover invalidates:

- [`docs/pipeline.md`](../docs/pipeline.md) — drop stage 4 from the
  stage table and the mermaid flowchart; rewrite the stage 5 / 7 / 8
  artifact descriptions.
- [`docs/interfaces.md`](../docs/interfaces.md) — rewrite the stage 4
  / 5 / 7 / 8 artifact tables, the CLI contract block, and the
  end-to-end sequence at L273–358.
- [`docs/eligibility.md`](../docs/eligibility.md) — drop the
  `eligible.jsonl` storage-layout entry; update CLI examples.
- [`docs/extraction-codebook.md`](../docs/extraction-codebook.md) —
  swap `--eligible` examples for `--catalogue + --eligibility-verdicts`;
  fix the L196–206 path bug from the
  [usability review](../docs/explorations/usability-review.md) P2.
- [`docs/fulltext-retrieval.md`](../docs/fulltext-retrieval.md) —
  update CLI examples to the new flag set.
- [`docs/data-model.md`](../docs/data-model.md) — drop
  `AdjudicationVerdict`/`AdjudicationMeta` from the model dependency
  graph.
- [`docs/catalogue-dedup.md`](../docs/catalogue-dedup.md) — describe
  the now-real dedup behaviour as fact, not as planned scope; document
  the multi-input glob.
- [`README.md`](../README.md) — drop stage 4 from `## Tools`; update
  the manual `Locally` invocation; update the wrapper invocation to
  reflect the new flag set.
- [`plans/roadmap.md`](roadmap.md) — move this plan from "in flight"
  to "done" when it lands; archive
  [`plans/done/flag-dont-filter-cutover.md`](done/flag-dont-filter-cutover.md)
  remains a record of the half-cutover.

## Follow-ups

- `laglitsynth.resolve` shared module — when stage 9 (extraction
  adjudication) lands and a fourth inline-join copy would otherwise
  appear.
- Pipeline-level config file collapsing the three
  `--screening-threshold` flags into one (A1 in
  [usability-review.md](../docs/explorations/usability-review.md)).
- README hygiene + `external-services.md` (P10 + the Ollama/GROBID
  doc bucket from
  [usability-review.md](../docs/explorations/usability-review.md)).
- Run-id elevation to a pipeline-level concept (A2). The stderr print
  is the cheap interim fix; the long-term answer is one
  `pipeline-run-id` set once.

## Risks

**Wrapper script breakage during rollout.** Each commit either updates
the wrapper in lockstep (steps 2, 3, 4) or doesn't touch the
invocation surface (steps 1, 5, 6). Per AGENTS.md no-backwards-compat,
in-flight runs against the old shape break — acceptable.

**Test churn.** Stages 5, 7, 8 tests all change shape. Step 2 also
deletes a whole test file. Plan ~30% test surgery in the touched
files. Mitigation: each step's own commit is small enough that the
test rewrite is bounded.

**Stage 8 `--catalogue` requirement is non-obvious.** A reader
expecting symmetry with stages 5/7 might think "stage 8 takes
eligibility verdicts; why does it also need the catalogue?" The
docstring at the top of `extract.py` and the CLI help for `--catalogue`
need to spell out: the catalogue is needed for the abstract fallback,
not for the gate.

**`generate-run-id` discoverability.** The wrapper still depends on
it; the stderr print covers the manual workflow. The subcommand stays
undocumented in `docs/` for now (separate doc commit).

## Critical files

- [`src/laglitsynth/catalogue_dedup/dedup.py`](../src/laglitsynth/catalogue_dedup/dedup.py)
  — replaced wholesale.
- [`src/laglitsynth/screening_adjudication/`](../src/laglitsynth/screening_adjudication/)
  — deleted.
- [`src/laglitsynth/fulltext_retrieval/retrieve.py`](../src/laglitsynth/fulltext_retrieval/retrieve.py)
  — CLI rewire + inline join.
- [`src/laglitsynth/fulltext_eligibility/eligibility.py`](../src/laglitsynth/fulltext_eligibility/eligibility.py)
  — CLI rewire + drop `eligible.jsonl` emission.
- [`src/laglitsynth/extraction_codebook/extract.py`](../src/laglitsynth/extraction_codebook/extract.py)
  — CLI rewire + eligibility-verdicts inline join.
- [`src/laglitsynth/cli.py`](../src/laglitsynth/cli.py) — drop stage 4
  subparser registration.
- [`src/laglitsynth/screening_abstracts/screen.py`](../src/laglitsynth/screening_abstracts/screen.py)
  — run-id stderr print only.
- [`scripts/run-pipeline.sh`](../scripts/run-pipeline.sh) +
  [`scripts/nesh-pipeline.sbatch`](../scripts/nesh-pipeline.sbatch) —
  drop stage 4, rewire stages 5/7/8 flags.
- [`docs/pipeline.md`](../docs/pipeline.md),
  [`docs/interfaces.md`](../docs/interfaces.md),
  [`docs/eligibility.md`](../docs/eligibility.md),
  [`docs/extraction-codebook.md`](../docs/extraction-codebook.md),
  [`docs/fulltext-retrieval.md`](../docs/fulltext-retrieval.md),
  [`docs/data-model.md`](../docs/data-model.md),
  [`docs/catalogue-dedup.md`](../docs/catalogue-dedup.md),
  [`README.md`](../README.md) — doc sweep in step 6.
- [`plans/roadmap.md`](roadmap.md) — index update.
