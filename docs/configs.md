# Configs and run dirs

The LLM stages — [screening-abstracts](screening-abstracts.md),
[fulltext-eligibility](eligibility.md),
[extraction-codebook](extraction-codebook.md) — share a sweep-friendly
config and run-directory model. Each invocation produces a fresh
self-contained run directory; every parameter that drives the run is
either a CLI flag, a key in a YAML config file, or both.

## Run directories

Resolved output dir is `<data-dir>/<stage-subdir>/<run-id>/`. `--data-dir`
(default: `data/`) is the bucket root, swapped wholesale to repoint a
sweep at a sibling tree. `<stage-subdir>` is hardcoded per stage:
`screening-abstracts`, `fulltext-eligibility`, `extraction-codebook`.
`--run-id` (default: a generated `<iso-1s>_<12hex>` in UTC) names the
leaf — the ISO timestamp is human-readable and the 12-hex suffix
removes concurrent-job collision risk on NESH or local sweeps. PDFs
and TEIs (under `<data-dir>/fulltext-extraction/tei/` and so on) are
not run-namespaced; they are a shared input cache populated
incrementally via the existing `--skip-existing` paths.

## Two roles for `config.yaml`

A YAML file in this project plays one of two roles depending on where
it lives.

**Input configs** are version-controlled or sweep-generated YAMLs that
drive an invocation. They reference the codebook and
eligibility-criteria YAMLs by path. Use cases: assembling NESH job
submissions without 200-character `sbatch` strings; running a sweep
where the only difference between 12 invocations is one parameter;
shipping a paper's supplementary materials as one config file.

**Run snapshots** are written automatically into every LLM-stage run
directory. The codebook and eligibility-criteria contents are inlined
as embedded mappings, so a year from now you can still tell what a
specific run was configured with — even if the upstream YAMLs have
moved or been edited.

The two share a file format and may even round-trip: feed a run
snapshot back as `--config <run-dir>/config.yaml` and the inlined
codebook is consumed directly. That isn't replay (LLM stochasticity
makes record-level reproduction impossible at `temperature=0.8`), but
it is "rerun with the same configuration," and for that purpose the
shapes are interchangeable.

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
`eligibility_criteria`, `codebook`), and nested per-stage groups for
`thresholds.{retrieval,eligibility}`, `models.{screening,eligibility,extraction}`,
`num_ctx.{eligibility,extraction}`, and `concurrency.{llm,extraction}`.
Only `query` is required; every other field is optional. An absent
optional field means "fall back to the runner's built-in default" — it
is not invented at validation time (None means None). The committed
default is [examples/reviews/lagrangian-oceanography.yaml](../examples/reviews/lagrangian-oceanography.yaml),
which reproduces the runner's historical smoke-run behaviour.

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
| `CFG_RETRIEVAL_THRESHOLD` / `CFG_ELIGIBILITY_THRESHOLD` | `thresholds.{retrieval,eligibility}` |
| `CFG_SCREENING_MODEL` / `CFG_ELIGIBILITY_MODEL` / `CFG_EXTRACTION_MODEL` | `models.{screening,eligibility,extraction}` |
| `CFG_ELIGIBILITY_NUM_CTX` / `CFG_EXTRACTION_NUM_CTX` | `num_ctx.{eligibility,extraction}` |
| `CFG_LLM_CONCURRENCY` / `CFG_EXTRACTION_CONCURRENCY` | `concurrency.{llm,extraction}` |

The three criteria/codebook paths are resolved against the config file's
own directory before emission (mirroring the
[path-resolution rule](#path-resolution-inside-input-configs) for
per-stage input configs), so a review config can sit alongside the YAMLs
it references regardless of the invocation CWD.

### Runner precedence: env > config > default

The runner `eval`s the emitted block, then resolves every knob as
`VAR="${VAR:-${CFG_VAR:-default}}"`. So an environment variable (or a
`sbatch --export=`) wins over the config file, which wins over the
runner's built-in default; an absent config field falls through to that
default. The historical `QUERY` and `N` env overrides keep working
through this same mechanism (`N` maps to `CFG_MAX_RECORDS`). The two
corpus thresholds (`RETRIEVAL_THRESHOLD`, `ELIGIBILITY_THRESHOLD`) stay
independently settable but share a common fallback `SCREENING_THRESHOLD`
when neither the specific env var nor the config pins them. See
[README.md](../README.md#running-the-pipeline) for the invocation.

## Quick recipes

```bash
# fresh run with defaults (auto run-id, default codebook YAML, etc.)
laglitsynth extraction-codebook \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --eligibility-verdicts data/fulltext-eligibility/<run-id>/verdicts.jsonl \
    --extractions data/fulltext-extraction/extraction.jsonl

# pin the run-id for reproducible directory placement
laglitsynth extraction-codebook ... --run-id 2026-05-03T14-30-00_abc123def456

# rerun a saved snapshot from its config (fresh run-id; same config)
laglitsynth extraction-codebook --config data/extraction-codebook/<run-id>/config.yaml \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --eligibility-verdicts data/fulltext-eligibility/<run-id>/verdicts.jsonl \
    --extractions data/fulltext-extraction/extraction.jsonl

# alternative codebook
laglitsynth extraction-codebook ... --codebook examples/codebooks/my-other-domain.yaml

# resume an existing run dir (the only case where --skip-existing matters
# under the run-id'd default)
laglitsynth extraction-codebook ... --run-id <existing> --skip-existing
```
