# Configs and run dirs

The LLM stages — [screening-abstracts](screening-abstracts.md),
[fulltext-eligibility](eligibility.md),
[extraction-codebook](extraction-codebook.md) — share a sweep-friendly
config and run-directory model. Each invocation writes one
self-contained run directory, named by its run-id; every parameter that drives the run is
either a CLI flag, a key in a YAML config file, or both.

## Run directories

Resolved output dir is `<data-dir>/<stage-subdir>/<run-id>/`. `--data-dir`
(default: `data/`) is the bucket root, swapped wholesale to repoint a
sweep at a sibling tree. `<stage-subdir>` is hardcoded per stage:
`screening-abstracts`, `fulltext-eligibility`, `extraction-codebook`.
`--run-id` names the leaf. It defaults to the `run_id` in
`<data-dir>/manifest.json` for screening and to `analysis_run_id` in
`<data-dir>/corpus/corpus-meta.json` for eligibility and extraction-codebook
(see [run-manifest.md](run-manifest.md)); a minted id is a
`<iso-1s>_<12hex>` in UTC — the ISO timestamp is human-readable and the
12-hex suffix removes collision risk between concurrent runs in a
sweep. The PDF store, the [corpus](corpus.md) and the TEIs (under
`<data-dir>/fulltext-extraction/tei/`) are not run-namespaced. The
corpus is rewritten whole by each `corpus-assemble`; the TEIs are a
shared cache filled incrementally through stage 6's `--skip-existing`.
Every analysis meta records the `corpus_id` it ran on, so output from
another corpus is refused rather than mixed in.

## Two roles for `config.yaml`

A YAML file in this project plays one of two roles depending on where
it lives.

**Input configs** are version-controlled or sweep-generated YAMLs that
drive an invocation. They reference the criteria and codebook YAMLs by
path. Use cases: pinning a run's parameters in one file instead of a
long string of env overrides; running a sweep
where the only difference between 12 invocations is one parameter;
shipping a paper's supplementary materials as one config file.

**Run snapshots** are written automatically into every LLM-stage run
directory. The criteria and codebook contents the stage read (screening
criteria for stage 3, eligibility criteria for stage 7, codebook and
eligibility criteria for stage 8) are inlined as embedded mappings, so a year from now you can still tell what a
specific run was configured with — even if the upstream YAMLs have
moved or been edited.

The two share a file format and may even round-trip: feed a run
snapshot back as `--config <run-dir>/config.yaml` and the inlined
criteria and codebook are consumed directly. That isn't replay (the endpoint's
sampling is not pinned, so record-level reproduction is not
guaranteed), but it is "rerun with the same configuration," and for that
purpose the shapes are interchangeable. The API key is never part of a
snapshot: `api_key` is dropped like `run_id` and `config`. Because
`run_id` is dropped, a rerun resolves it as any run does — the
manifest's `run_id` for screening, the corpus's `analysis_run_id` for
eligibility and extraction-codebook — so a rerun without `--run-id`
writes into the original run directory: it replaces that run's output,
or, for eligibility and extraction-codebook with `skip_existing: true`
in the snapshot, continues it. Pass
`--run-id "$(laglitsynth generate-run-id)"` to keep the original run.

## CLI-config precedence

Argument resolution order, highest first: explicit CLI flag,
`--config` file value (applied via `parser.set_defaults`), and the
hardcoded `add_argument` default. So `--config foo.yaml --model bar`
always uses `bar` for the model, regardless of what `foo.yaml` says.

## Path resolution inside input configs

YAML-valued paths inside an input config (any string ending in
`.yaml` / `.yml`) are resolved against the config file's own
directory, not the invocation's CWD. This lets a config sit alongside
the YAMLs it references — `sweeps/run-A.yaml` referencing
`codebook: ../codebooks/my.yaml` works from any CWD.

## Per-review config (the runner's config)

The two roles above are *per-stage* configs — they describe one
invocation of one LLM stage. A **per-review config** is a third,
distinct shape: one handwritten YAML that captures the review-defining
knobs the end-to-end runner ([scripts/run-pipeline.sh](../scripts/run-pipeline.sh))
needs, across all stages. It does **not** replace the per-stage
`config.yaml` run-snapshots — those are still written automatically into
each LLM stage's run directory. The per-review config sits one level up:
it drives the runner, which in turn invokes the stages (each of which
writes its own snapshot).

A per-review config is validated against the typed `ReviewConfig`
(Pydantic, `extra="forbid"`) in
[src/laglitsynth/review.py](../src/laglitsynth/review.py). Its fields are
the query, the year window (`from_year` / `to_year`) and record cap
(`max_records`), the three criteria/codebook paths (`screening_criteria`,
`eligibility_criteria`, `codebook`), the `screening_threshold`, the
endpoint URLs (`llm_base_url`, `llm_decisions_url`), and nested per-stage
groups for `models.{screening,eligibility,extraction}` plus
`models.extraction_reasoning_effort` (`low`, `medium` or `high`) and
`concurrency.{llm,extraction,grobid}`.
Only `query` is required; every other field is optional. An absent
optional field means "fall back to the runner's built-in default" — it
is not invented at validation time (None means None). The committed
default is [examples/reviews/lagrangian-oceanography.yaml](../examples/reviews/lagrangian-oceanography.yaml),
the smoke-run config; it leaves `screening_threshold`, the LLM endpoint
URLs and the models unset and names no provider. The API key is not a
review-config field: it is `LLM_API_KEY` in the environment or `.env`.

### The `review-config` subcommand

`laglitsynth review-config <path>` loads a review YAML, validates it, and
prints one `CFG_<NAME>=<value>` line per knob to stdout — shell-quoted
(via `shlex.quote`) so a query with spaces or quotes survives `eval`. An
absent optional knob emits an empty value (`''`) for the runner to fill.
The stable, documented name set is:

| CFG name | Source field |
|---|---|
| `CFG_QUERY` | `query` |
| `CFG_FROM_YEAR` / `CFG_TO_YEAR` | `from_year` / `to_year` |
| `CFG_MAX_RECORDS` | `max_records` |
| `CFG_SCREENING_CRITERIA` | `screening_criteria` |
| `CFG_ELIGIBILITY_CRITERIA` | `eligibility_criteria` |
| `CFG_CODEBOOK` | `codebook` |
| `CFG_SCREENING_THRESHOLD` | `screening_threshold` |
| `CFG_LLM_BASE_URL` | `llm_base_url` |
| `CFG_LLM_DECISIONS_URL` | `llm_decisions_url` |
| `CFG_SCREENING_MODEL` / `CFG_ELIGIBILITY_MODEL` / `CFG_EXTRACTION_MODEL` | `models.{screening,eligibility,extraction}` |
| `CFG_EXTRACTION_REASONING_EFFORT` | `models.extraction_reasoning_effort` |
| `CFG_LLM_CONCURRENCY` / `CFG_EXTRACTION_CONCURRENCY` / `CFG_GROBID_CONCURRENCY` | `concurrency.{llm,extraction,grobid}` |

The three criteria/codebook paths are resolved against the config file's
own directory before emission (mirroring the
[path-resolution rule](#path-resolution-inside-input-configs) for
per-stage input configs), so a review config can sit alongside the YAMLs
it references regardless of the invocation CWD.

### Runner precedence: env > config > default

The runner `eval`s the emitted block, then resolves every knob as
`VAR="${VAR:-${CFG_VAR:-default}}"`. So an environment variable wins
over the config file, which wins over the
runner's built-in default; an absent config field falls through to that
default. The historical `QUERY` and `N` env overrides keep working
through this same mechanism (`N` maps to `CFG_MAX_RECORDS`). The
screening threshold has no built-in default: when the run reaches stage
5, where `corpus-assemble` applies it, the runner exits unless
`SCREENING_THRESHOLD` is set in the environment or `screening_threshold`
in the config. The LLM endpoint has no default either: from stage 3 on the
runner requires `LLM_BASE_URL` (env or `llm_base_url`), `LLM_API_KEY`
(env, else `.env`) and the model of every stage it reaches, and
exits naming whichever is missing. `LLM_DECISIONS_URL` is passed to stages
3 and 7 as `--decisions-url` when set, and `EXTRACTION_REASONING_EFFORT`
to stage 8 as `--reasoning-effort` when set; the key is never passed as a
flag. `RUN_ID` names the screening run (stage 3, `corpus-assemble`, the
export) and `ANALYSIS_RUN_ID`, unset by default, is passed as `--run-id`
to stages 7 and 8, which otherwise take the corpus's `analysis_run_id`. See
[README.md](../README.md#running-the-pipeline) for the invocation.

## Quick recipes

```bash
# run with defaults: inputs at their conventional paths under data/,
# run-id from data/corpus/corpus-meta.json, default codebook YAML
laglitsynth extraction-codebook

# write under a run-id other than the corpus's
laglitsynth extraction-codebook --run-id 2026-05-03T14-30-00_abc123def456

# rerun a saved snapshot from its config
laglitsynth extraction-codebook --config data/extraction-codebook/<run-id>/config.yaml

# alternative codebook
laglitsynth extraction-codebook ... --codebook examples/codebooks/my-other-domain.yaml

# resume an existing run dir (the only case where --skip-existing matters
# under the run-id'd default)
laglitsynth extraction-codebook ... --run-id <existing> --skip-existing
```
