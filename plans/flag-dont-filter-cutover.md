# Flag-don't-filter: minimal plumbing

## Goal

Stage 3 (`screening-abstracts`) currently writes Work records split
into `accepted.jsonl` + `rejected.jsonl`, alongside a verdict file.
Stage 4 (`screening-adjudication`) copies accepted Works into
`included.jsonl`. Neither treats the verdict sidecar as the source of
truth, which blocks later work (threshold re-tuning, multi-run
consensus, stage 7+ resolve).

This plan is the minimum change that (a) makes stage 3 emit a
complete `ScreeningVerdict` sidecar covering every work in the
deduplicated catalogue with no Work-record splits, and (b) makes
stage 4 emit an `AdjudicationVerdict` sidecar rather than silently
copying Works. Stage 5 consumption is unchanged — stage 4 still
writes a convenience `included.jsonl` that stage 5 reads. The
`Filter*` → `Screening*` rename in the stage-3 module is folded in
since we are rewriting these files anyway.

## Non-goals

- Building `laglitsynth.resolve` as a shared cross-stage module.
  Deferred; see the [roadmap](roadmap.md).
- Threshold re-tuning at read time across stages. Deferred.
- Stage 5 CLI shape change (`--input` → `--data-dir`). Stage 5 is
  untouched by this plan.
- Reproducibility meta refactor (`_RunMeta`, git SHA, input paths,
  prompt digests). See the roadmap.
- `_Base` deletion + per-model `ConfigDict` split. See the roadmap.
- `Work` model additions (`source_catalogues`, `is_peer_reviewed`).
  See the roadmap.
- Documentation filename renames (`docs/filter-abstracts.md` →
  `docs/screening-abstracts.md` etc.). Separate doc-cleanup task;
  this plan only updates doc *content* where it contradicts the new
  code contract.
- [pipeline.md](../docs/pipeline.md) heading + mermaid rename,
  [README.md](../README.md) rewrite. Separate doc-cleanup task.
- [Multi-run consensus](multi-run-consensus.md) — builds on this
  plan's contract but is a separate plan.

## Target state

Stage 3 output:

- `data/screening-abstracts/verdicts.jsonl` — one `ScreeningVerdict`
  per work in the input catalogue.
- `data/screening-abstracts/screening-meta.json` — run metadata.
- No timestamped files. No `accepted.jsonl` or `rejected.jsonl`.

Stage 4 output:

- `data/screening-adjudication/verdicts.jsonl` — one
  `AdjudicationVerdict` per work above threshold. In the pass-through
  MVP every such record has `decision="accept"`.
- `data/screening-adjudication/adjudication-meta.json` — run
  metadata including the threshold applied.
- `data/screening-adjudication/included.jsonl` — Work records whose
  adjudication decision is `accept`. This is a convenience for stage
  5 and will be removed when the full resolve module lands.

Stage 5: unchanged. Still reads
`data/screening-adjudication/included.jsonl`.

Code renames in [src/laglitsynth/screening_abstracts/](../src/laglitsynth/screening_abstracts/):

- `FilterVerdict` → `ScreeningVerdict`
- `FilterMeta` → `ScreeningMeta`
- `filter_works` → `screen_works`
- `filtered_at` → `screened_at`
- Module docstring, subparser help text, log messages updated from
  "filter" to "screen".

Doc updates that the code rewrite forces:

- [docs/interfaces.md](../docs/interfaces.md) lines 70–73 and
  152–157 planning statements removed (they now describe reality).
- [docs/interfaces.md](../docs/interfaces.md) stage 3 and stage 4
  artifact/CLI blocks updated.
- Broken link at [docs/interfaces.md:20](../docs/interfaces.md)
  fixed (`../plans/retuning-propagation.md` →
  `../plans/done/retuning-propagation.md`).
- [docs/filter-abstracts.md](../docs/filter-abstracts.md) content
  updated: "filter" → "screen" vocabulary, correct default paths, no
  `_filtered_` references, no `accepted.jsonl`. File is *not*
  renamed.
- [docs/adjudication-screening.md](../docs/adjudication-screening.md)
  CLI example updated to the new input (`verdicts.jsonl`, not
  `screened.jsonl`). `tool` string fixed. File is *not* renamed.

## Design decisions

Each of these is defaulted in the plan; override by editing the
corresponding implementation step before an agent runs.

### `ScreeningVerdict.accepted` field is removed

Acceptance is derived at read time from `relevance_score` and the
consumer's threshold. The verdict sidecar becomes a pure record of
LLM output, not a threshold-dependent classification. Only
`work_id`, `relevance_score: int | None`, `reason: str | None`
remain on the verdict.

### Fixed `reason` vocabulary

Two sentinel strings for the two currently-known non-LLM verdict
cases:

- `reason="no-abstract"` when the Work has no abstract and the LLM is
  skipped. `relevance_score=None`.
- `reason="llm-parse-failure"` when the LLM returns a response that
  does not parse into the expected schema. `relevance_score=None`.

For normal verdicts the `reason` field carries the LLM's free-text
justification. Locking these two sentinels lets humans scanning
verdicts tell "skipped because no abstract" apart from "LLM returned
junk".

### Threshold flag name

`--threshold`. Stage 4 is the only consumer today. If later stages
need their own thresholds they can use `--eligibility-threshold` etc.
Plain name where ambiguity is low.

### Stage 4 reruns overwrite

Pass-through MVP writes fresh verdicts each run. Preserving prior
human decisions across reruns is a real adjudication-UI concern, not
part of plumbing. If a future run changes threshold, the new
`verdicts.jsonl` reflects the new active set.

### Legacy data files deleted

During migration we delete
`data/screening-abstracts/{accepted,rejected}.jsonl`,
`data/screening-abstracts/accepted.verdicts.jsonl`, any timestamped
`_screened_*` files, and the old
`data/screening-adjudication/included.jsonl` (the new code writes a
fresh one on next run). Green field, no backwards compat.

### `write_jsonl` mode changes from `"x"` to `"w"`

Mode `"x"` (exclusive create) makes every rerun crash with a bare
`FileExistsError`. Overwrite is the right default for idempotent
data directories. `append_jsonl` stays at `"a"` for stages that
genuinely append (retrieval, extraction).

### `AdjudicationVerdict` model shape

Fields: `work_id: str`, `decision: Literal["accept", "reject",
"skip"]`, `reviewer: str`, `adjudicated_at: str`, `reason: str |
None`.

Pass-through MVP: `decision="accept"`, `reviewer="pass-through"`,
`reason=None`. Real human adjudication populates these for real
later.

### `ScreeningMeta.threshold` is informational

Stage 3 writes every verdict regardless of threshold; the threshold
is recorded in the meta so a reader can see what value was in
effect. Stage 4 reads its own threshold from its own CLI flag — the
two can diverge, and that is fine.

## Implementation sequence

Each step is a self-contained commit. Run `pixi run test` and
`pixi run typecheck` before each commit.

### 1. `io.py` mode flip

Change `write_jsonl` in [src/laglitsynth/io.py](../src/laglitsynth/io.py)
from `open(path, "x")` to `open(path, "w")`. Update the docstring.

Impact: stages 2 and 4 currently get accidental-overwrite protection
from the failure mode. Explicit overwrite is the desired behaviour
going forward.

### 2. Rename `Filter*` → `Screening*`

Scope: find-and-replace across
[src/laglitsynth/screening_abstracts/](../src/laglitsynth/screening_abstracts/)
and [tests/test_screening_abstracts.py](../tests/test_screening_abstracts.py):

- `FilterVerdict` → `ScreeningVerdict`
- `FilterMeta` → `ScreeningMeta`
- `filter_works` → `screen_works`
- `filtered_at` → `screened_at`

Plus free-text updates:

- Module docstring: `"""LLM-based abstract filtering via Ollama."""`
  → `"""LLM-based abstract screening via Ollama."""`
- Subparser help: `"Filter JSONL works by abstract relevance ..."`
  → `"Screen JSONL works by abstract relevance ..."`
- Argument help: `"Relevance filter prompt string"` → `"Relevance
  screening prompt string"`.
- Runtime log: `"Filtering {total} works with model {model}"` →
  `"Screening {total} works with model {model}"`.

No behaviour change. Test suite still passes.

### 3. Stage 3 refactor — verdicts-only output

In [src/laglitsynth/screening_abstracts/models.py](../src/laglitsynth/screening_abstracts/models.py):

- Drop `ScreeningVerdict.accepted`.
- Keep `work_id`, `relevance_score: int | None`, `reason: str |
  None`.
- `ScreeningMeta` fields: `tool`, `tool_version`, `screened_at`,
  `prompt`, `model`, `threshold`, `input_path`, `input_count`,
  `above_threshold_count`, `below_threshold_count`, `skipped_count`.

In [src/laglitsynth/screening_abstracts/screen.py](../src/laglitsynth/screening_abstracts/screen.py):

- `screen_works()` yields one `ScreeningVerdict` per input Work
  regardless of score or abstract presence.
- For works with no abstract: emit `ScreeningVerdict(work_id=...,
  relevance_score=None, reason="no-abstract")` and skip the LLM.
- For works where the LLM response does not parse: emit
  `ScreeningVerdict(work_id=..., relevance_score=None,
  reason="llm-parse-failure")`. Log a warning.
- `run()` writes `data/screening-abstracts/verdicts.jsonl` and
  `data/screening-abstracts/screening-meta.json`. No other files.
- CLI: drop `-o`/`--output`, drop `--reject-file`. Add
  `--output-dir` (default `data/screening-abstracts/`). Keep
  positional `input prompt`; keep `--threshold`, `--model`,
  `--base-url`, `--max-records`, `--dry-run`.
- Move the `OpenAI(...)` client instantiation out of
  `classify_abstract` into `screen_works` (per [code
  review](review-2026-04-16-code.md) finding on TCP-per-work
  latency).

### 4. Stage 4 refactor — AdjudicationVerdict sidecar

In [src/laglitsynth/screening_adjudication/models.py](../src/laglitsynth/screening_adjudication/models.py):

- Add `AdjudicationVerdict` with the fields specified under Design
  decisions.
- `AdjudicationMeta` gains `threshold: int`, `input_count: int`,
  `accepted_count: int`, `rejected_count: int`.

In [src/laglitsynth/screening_adjudication/adjudicate.py](../src/laglitsynth/screening_adjudication/adjudicate.py):

- CLI: `--input` points at stage 3's verdicts file, `--catalogue` at
  the dedup catalogue, `--threshold` (int), `--output-dir` (default
  `data/screening-adjudication/`).
- `run()` iterates stage 3 verdicts, joins against the catalogue by
  `work_id`, and for each work with `relevance_score >= threshold`
  emits (a) an `AdjudicationVerdict(decision="accept",
  reviewer="pass-through", reason=None)` and (b) the Work record
  into `included.jsonl`.
- Works with `relevance_score < threshold` or `None` are not
  represented in either output file. Full adjudication later will
  emit `reject`/`skip` verdicts explicitly.

### 5. Test migration

In [tests/test_screening_abstracts.py](../tests/test_screening_abstracts.py):

- Remove `test_run_writes_output_files` assertions about
  `accepted.jsonl` and `rejected.jsonl`; replace with "only
  `verdicts.jsonl` and `screening-meta.json` exist; every input
  work has a verdict".
- Update every `FilterVerdict`/`FilterMeta`/`filter_works`
  reference to the new names.
- Add `test_verdict_reason_no_abstract`: a Work with
  `abstract=None` produces a verdict with `relevance_score=None`
  and `reason="no-abstract"`; the LLM is not called.
- Add `test_verdict_reason_llm_parse_failure`: LLM returns
  malformed JSON; verdict has `relevance_score=None`,
  `reason="llm-parse-failure"`.

In [tests/test_screening_adjudication.py](../tests/test_screening_adjudication.py):

- Rewrite end-to-end around the new input shape (stage-3 verdicts +
  catalogue).
- Add `test_included_jsonl_matches_threshold`: two works, scores 70
  and 30, threshold 50 — assert `included.jsonl` contains only the
  first, and `verdicts.jsonl` contains exactly one
  `AdjudicationVerdict(decision="accept")` for the first.
- Remove any assertion that relied on `write_jsonl` raising
  `FileExistsError` on rerun.

### 6. Docs

[docs/interfaces.md](../docs/interfaces.md):

- Line 20: fix broken link → `../plans/done/retuning-propagation.md`.
- Lines 58–68 (stage 3 artifact table): describe the two artifacts
  as current state — `verdicts.jsonl`, `screening-meta.json`.
- Lines 70–73: delete the "existing code uses timestamped filenames
  and splits accepted/rejected" planning note.
- Lines 75–83 (stage 4 artifact table): describe `verdicts.jsonl`,
  `adjudication-meta.json`, `included.jsonl` as current state.
- Lines 152–157: delete the duplicate "Stage 3 implementation
  (code change needed)" note.
- Lines 174, 265–266, 269–270 (CLI contract + end-to-end sequence):
  update stage 3 and stage 4 commands to the new flag shapes.

[docs/filter-abstracts.md](../docs/filter-abstracts.md):

- Vocabulary "filter" → "screen" throughout.
- Default output path updated to
  `data/screening-abstracts/verdicts.jsonl`.
- Remove `_filtered_` / `accepted.jsonl` references.

[docs/adjudication-screening.md](../docs/adjudication-screening.md):

- CLI example: `--input data/screening-abstracts/verdicts.jsonl
  --catalogue data/catalogue-dedup/deduplicated.jsonl --threshold 50`.
- `tool` string: `laglitsynth.screening_adjudication.adjudicate`
  (matches module file).
- Describe the new `AdjudicationVerdict` model.

### 7. Migration cleanup

Delete from [data/screening-abstracts/](../data/screening-abstracts/):

- `accepted.jsonl`, `rejected.jsonl`, any
  `accepted.verdicts.jsonl`, any `*_screened_*` timestamped files.

Delete from [data/screening-adjudication/](../data/screening-adjudication/):

- The pre-refactor `included.jsonl` (the new code writes a fresh one
  on next run).

(Data directories are gitignored so this is a one-time working-tree
clean.)

## Test migration summary

Tests that change:

- `test_screening_abstracts.py::test_run_writes_output_files` →
  asserts new single-verdicts-file contract.
- `test_screening_abstracts.py`: all `Filter*` renames applied.
- `test_screening_adjudication.py`: rewritten end-to-end for
  threshold-applying pass-through.
- Any test relying on `FileExistsError` on rerun: updated for
  overwrite.

Tests that are new:

- `test_verdict_reason_no_abstract`
- `test_verdict_reason_llm_parse_failure`
- `test_included_jsonl_matches_threshold`

## Risks and rollback

- Stage 5 is untouched but reads
  `data/screening-adjudication/included.jsonl`. After the refactor
  this file still exists and has the same shape (Work records for
  accepted works). If the thresholds used by stages 3 and 4
  diverge, `included.jsonl` reflects stage 4's threshold — this is
  working as designed (stage 3's threshold is informational only).
- Tests inspecting specific accepted/rejected file shapes fail fast
  when run against the new code — that is the signal the cutover
  landed correctly.
- Rollback: `git revert` the single cutover commit. Repo is
  green-field per [AGENTS.md](../AGENTS.md) — no consumers to
  coordinate with.

## Verification

Automated:

- `pixi run test` passes with the rewritten tests asserting the new
  contract.
- `pixi run typecheck` passes with zero issues.

Manual end-to-end dry-run after a small real fetch into
`data/catalogue-fetch/`:

```sh
laglitsynth catalogue-dedup \
    --input data/catalogue-fetch/combined.jsonl \
    --output-dir data/catalogue-dedup/

laglitsynth screening-abstracts \
    data/catalogue-dedup/deduplicated.jsonl \
    "Is this about computational Lagrangian methods in oceanography?" \
    --threshold 50 \
    --output-dir data/screening-abstracts/
# Expect: data/screening-abstracts/verdicts.jsonl,
#         data/screening-abstracts/screening-meta.json.

laglitsynth screening-adjudication \
    --input data/screening-abstracts/verdicts.jsonl \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --threshold 50 \
    --output-dir data/screening-adjudication/
# Expect: data/screening-adjudication/verdicts.jsonl,
#         data/screening-adjudication/adjudication-meta.json,
#         data/screening-adjudication/included.jsonl.

laglitsynth fulltext-retrieval \
    --input data/screening-adjudication/included.jsonl \
    --output-dir data/fulltext-retrieval/ \
    --email user@example.com \
    --dry-run
# Unchanged from current behaviour.
```

## Open questions

All three are defaulted in the plan above; override by editing the
design-decisions section before the implementation agent runs.

1. Lock the two sentinel `reason` strings `"no-abstract"` and
   `"llm-parse-failure"` as the only non-LLM verdict reasons for
   now? Plan default: yes.
2. Threshold flag named `--threshold` (plan default) or
   `--screening-threshold`?
3. Delete legacy data files during migration (plan default) or
   leave them on disk for archival?
