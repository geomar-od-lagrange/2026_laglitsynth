# Run manifest

## Goal

Stop carrying paths and the run-id between stage invocations by hand. Before
this plan, no stage knew where its upstream wrote, and the run-id leaf for
stages 3/7/8 lived in whoever's shell started the run
([running-the-pipeline.md](../../docs/explorations/running-the-pipeline.md)
section 1).

## Target state

One file, `data/manifest.json`, holding a typed `RunManifest {run: RunMeta,
run_id, queries}`, written once by a new `manifest-init` subcommand and never
rewritten. Every stage that writes or reads a `<run-id>/` leaf resolves
`--run-id` as flag, else the manifest's `run_id`, else exit — no stage mints
its own. Every other input flag defaults to the stage's conventional path
under `--data-dir`, spelled out once in `laglitsynth.layout`, and a missing
default exits naming the upstream stage to run. The runner creates the
manifest when absent and passes `--run-id` only when `RUN_ID` is set.

## Design decisions

An earlier draft of this plan had the manifest carry an append-only log of
each stage's resolved inputs and output, with stages resolving omitted flags
from the last entry. Review before landing found it re-solved what the fixed
`data/` layout already settles, and that last-entry-wins routes by invocation
order: a smoke run under an explicit `--run-id` would silently redirect every
later flagless stage. The log was dropped; the layout plus one run-id covers
every case it did. Because the file is now immutable after init, it has no
sync direction to get wrong across machines.

## Implementation sequence

Landed as one squash. `manifest.py` and `layout.py`; stages 1–8 and the
handoff subcommands defaulting their flags; the runner; tests per stage that
omitted flags resolve to the layout, explicit flags win, a missing default
names the upstream stage, and no run-id from flag or manifest exits.

## Critical files

- [src/laglitsynth/manifest.py](../../src/laglitsynth/manifest.py)
- [src/laglitsynth/layout.py](../../src/laglitsynth/layout.py)
- [scripts/run-pipeline.sh](../../scripts/run-pipeline.sh)
- [docs/run-manifest.md](../../docs/run-manifest.md)
