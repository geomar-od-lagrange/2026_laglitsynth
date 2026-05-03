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

## Quick recipes

```bash
# fresh run with defaults (auto run-id, default codebook YAML, etc.)
laglitsynth extraction-codebook \
    --eligible data/fulltext-eligibility/<run-id>/eligible.jsonl \
    --extractions data/fulltext-extraction/extraction.jsonl

# pin the run-id for reproducible directory placement
laglitsynth extraction-codebook ... --run-id 2026-05-03T14-30-00_abc123def456

# rerun a saved snapshot from its config (fresh run-id; same config)
laglitsynth extraction-codebook --config data/extraction-codebook/<run-id>/config.yaml \
    --eligible data/fulltext-eligibility/<run-id>/eligible.jsonl \
    --extractions data/fulltext-extraction/extraction.jsonl

# alternative codebook
laglitsynth extraction-codebook ... --codebook examples/codebooks/my-other-domain.yaml

# resume an existing run dir (the only case where --skip-existing matters
# under the run-id'd default)
laglitsynth extraction-codebook ... --run-id <existing> --skip-existing
```
