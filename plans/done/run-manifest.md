# Run manifest

## Goal

Give a review one file that pins what it is — its queries, the
shared run-id, the thresholds and model tags, and the resolved
input→output path chain per stage — written by each stage and read by
the next. Today driving the pipeline is a human-carried path relay
([running-the-pipeline.md](../../docs/explorations/running-the-pipeline.md)
section 1): no stage discovers its own inputs, the run-id leaf has to
be threaded by hand across stages 3/7/8, and if you lose the shell
that held `$RUN_ID` you recover it from stderr scrollback. The
manifest replaces that relay with "stage N reads the manifest, finds
stage N−1's output, appends its own entry," travels with `data/` so
the run-id crosses machines with the data instead of in someone's
shell history, and is the natural home for the project boundary the
[diversified-retrieval](fulltext-retrieval-diversified.md) plan is
blocked on. It is the A1/A2 ideas from the
[usability review](../../docs/explorations/usability-review.md) fused and
viewed as *state* rather than *flags*.

## Scope of the first landing

The chain from `catalogue-dedup` to `fulltext-retrieval-export` lands
first, because that is the run of stages a review completes before any
PDF exists. Screening outcomes have to survive on disk, and the export
that hands DOIs to a collaborator has to find them without a run-id
carried in someone's shell. Stages 7 and 8 gain the same wiring in a
later commit of this plan; nothing about their design changes, and the
helpers are written once for both.

## Non-goals

This is not pipeline orchestration: the manifest records and resolves
paths, it does not run stages, schedule them, or decide which stage
comes next — the user still invokes each subcommand. It is not a
config file either; the per-run config.yaml snapshot
([configs.md](../../docs/configs.md)) stays exactly as it is, and
`--config` still seeds argparse defaults via
[config.py](../../src/laglitsynth/config.py). The manifest records the
*resolved* configuration after those defaults are applied, so the two
never compete. It does not change where any stage writes its data —
the per-stage subdirs under `data/` are unchanged. And it does not
merge work across machines: superposing two machines' verdicts is the
separate append-only-log generalisation flagged in
[running-the-pipeline.md](../../docs/explorations/running-the-pipeline.md)
section 4; the manifest carries the shared run-id that makes such a
merge *addressable*, nothing more.

## Target state

A review is a working directory with a `data/` tree and exactly one
`data/manifest.json`. That file is the project boundary the
exploration calls de-facto today ("a clone with a `data/` dir") and
the retrieval plan's open question — *where the persistent store
lives relative to per-search runs* — answers to: the store lives under
`data/`, and the manifest is the index over it. The file travels with
`data/` under rsync, so a collaborator who receives the catalogue and
verdicts receives the run-id and lineage with them.

### The manifest file

One JSON file, `data/manifest.json`, holding a typed `RunManifest`
written through the existing [io.write_meta](../../src/laglitsynth/io.py)
(pretty-printed, trailing newline) — same writer every `*-meta.json`
sidecar already uses, so there is no new I/O path. A new module
[src/laglitsynth/manifest.py](../../src/laglitsynth/manifest.py) holds
the models and the read/append helpers:

```python
TOOL_NAME = "laglitsynth.manifest"


class StageEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str                  # CLI subcommand name, e.g. "screening-abstracts"
    run_at: str                 # ISO-8601 UTC
    inputs: dict[str, str]      # logical name → resolved path, e.g. {"catalogue": "data/catalogue-dedup/deduplicated.jsonl"}
    output: str                 # the path/dir this stage wrote
    meta_path: str | None       # the stage's own *-meta.json, when it writes one


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    run_id: str                 # the shared run-id leaf for stages 3/7/8
    queries: list[str]          # the search term(s) this review ran
    data_dir: str               # repo-relative, the project root marker ("data")
    stages: list[StageEntry]    # append-only; one entry per stage invocation
```

All paths are stored repo-relative — the same contract the retrieval
plan already states for `pdf_path` — so the file survives the move
between a laptop's checkout and a NESH checkout as long as both run
from the project root. `queries` is a list because a review runs many
search terms; the manifest is the one place that records *which*
queries fed the shared deduplicated catalogue. `run_id` is the leaf
that stages 3/7/8 thread today — written once, read by every
run-id-aware stage thereafter. `stages` is append-only: a re-run of a
stage appends a new `StageEntry` rather than rewriting the old one,
so the manifest is a lineage log, not a current-state snapshot, and
last-entry-wins gives "the current output of stage X."

The nested `run: RunMeta` carries `tool`, `run_at`, and
`validation_skipped` for the mint event — the same
[RunMeta](../../src/laglitsynth/models.py) every sidecar nests. At
`manifest-init` time no records are read, so `validation_skipped` is
`0` honestly (nothing was dropped), not a placeholder standing in for
an unknown.

None means None: `meta_path` is `None` for stages that write no meta
sidecar; a stage that has not run yet simply has no entry, never a
placeholder.

### Reading and writing

Two helpers in [manifest.py](../../src/laglitsynth/manifest.py) carry the
relay:

```python
def load_manifest(data_dir: Path) -> RunManifest | None
def append_stage(data_dir: Path, entry: StageEntry) -> RunManifest
def latest_output(manifest: RunManifest, stage: str) -> str | None
```

`load_manifest` returns `None` when `data/manifest.json` is absent —
the un-initialised case. `append_stage` reads, appends, rewrites via
`write_meta`, and returns the updated manifest. `latest_output` walks
`stages` backward for the last entry whose `stage` matches, giving
stage N a one-call answer to "where did stage N−1 write?"

The relay then works like this. A stage resolves its `--input` /
`--catalogue` the way it does today *if the flag is given* — explicit
flags always win, no magic override. But when the input flag is
omitted and a manifest exists, the stage calls `latest_output(...)`
for the upstream stage it consumes and uses that path. After it
finishes, it calls `append_stage` with its own `inputs`, `output`,
and `meta_path`. So the hand-carried relay collapses: stage 7 with no
`--catalogue` reads the manifest, finds dedup's `deduplicated.jsonl`
and stage 3's verdicts dir, runs, and records its own eligibility
output for stage 8 to find. The user types paths only to override.

### The shared run-id

The run-id stops being a string the human re-types per stage. A new
subcommand `laglitsynth manifest-init "<query>" ["<query>" ...]
[--data-dir data]` mints one run-id via
[generate_run_id](../../src/laglitsynth/ids.py), records the queries, and
writes the initial `RunManifest` with an empty `stages` list. The
existing `generate-run-id` subcommand stays for ad-hoc use, but the
blessed path for a review is `manifest-init`. From then on the
run-id-aware stages (3, 7, 8) resolve `--run-id` as: explicit flag
wins; else the manifest's `run_id`; else — no manifest — the current
`generate_run_id()` fallback, unchanged. A stage that mints a fresh
run-id because no manifest exists is the un-initialised path, exactly
today's behaviour.

This is what carries the run-id across machines: NESH runs eligibility
reading `run_id` from the manifest that arrived with `data/`, the
laptop later runs extraction reading the *same* `run_id` from the
same file — they agree without anyone typing the string twice.

### The handoff subcommands

`fulltext-retrieval-export` and `fulltext-retrieval-import` postdate the
first draft of this plan and resolve inputs the same way. Export takes
`--catalogue`, `--screening-verdicts`, `--screening-threshold`, and
`--data-dir` today, and the first two are exactly the paths the relay
already resolves for `fulltext-retrieval`. With a manifest present,
`fulltext-retrieval-export --data-dir data` is the whole invocation: the
catalogue comes from `latest_output(manifest, "catalogue-dedup")` and the
verdicts from `latest_output(manifest, "screening-abstracts")`.

Both subcommands append a `StageEntry`. Export records the bundle
directory as its `output`, and import records the PDF store. The
collaborator round-trip then reads off the manifest: which selection was
sent, when, and which import filled it.

## Design decisions

The manifest is append-only and last-entry-wins rather than a
mutable current-state map because lineage is the point: "screening run
X used threshold 50 and feeds eligibility run Y" is exactly the
question section 2 of the exploration says you reconstruct from
operator memory today, and an append log answers it directly while a
mutable map would erase it. It is one file at `data/manifest.json`,
not per-stage manifest fragments, because the whole value is having
*one* artifact to reopen for "where was I"; fragments would recreate
the scattered-meta problem the plan exists to dissolve. It reuses
`write_meta` and nests `RunMeta` rather than inventing a writer or a
provenance block, matching every existing sidecar. And resolution is
flag-wins-else-manifest-else-generate so the manifest is *additive*:
every existing invocation that passes explicit paths and `--run-id`
behaves identically, and the manifest only fills gaps — no stage
breaks if `data/manifest.json` is absent.

## Implementation sequence

Five commits. `pixi run typecheck` and `pixi run test` pass between
each. Commits 1 to 4 are the pre-retrieval chain; commit 5 extends the
same wiring to the full-text stages.

### 1. Models and helpers

Create [src/laglitsynth/manifest.py](../../src/laglitsynth/manifest.py)
with `RunManifest`, `StageEntry`, `TOOL_NAME`, and `load_manifest`,
`append_stage`, `latest_output`. Tests in `tests/test_manifest.py`:
round-trip a manifest through `write_meta` / `load_manifest`;
`load_manifest` returns `None` when the file is absent; `append_stage`
appends without dropping prior entries; `latest_output` returns the
last matching stage's output and `None` for a stage never run.

### 2. `manifest-init` subcommand

Add `build_subparser` to [manifest.py](../../src/laglitsynth/manifest.py)
registering `manifest-init` (positional queries, `--data-dir`),
minting the run-id and writing the initial manifest; register it in
[cli.py](../../src/laglitsynth/cli.py) ahead of `generate-run-id`. Tests:
`manifest-init` writes a valid `data/manifest.json` with the given
queries, a run-id matching `RUN_ID_RE`, and an empty `stages` list;
re-running refuses to clobber an existing manifest (a review is
initialised once).

### 3. Wire dedup and screening

Give [dedup.py](../../src/laglitsynth/catalogue_dedup/dedup.py) the
omitted-input resolution and the `append_stage` call. In
[screen.py](../../src/laglitsynth/screening_abstracts/screen.py), resolve
`--run-id` as flag-wins-else-`manifest.run_id`-else-`generate_run_id()`,
resolve the omitted catalogue via `latest_output`, and append the entry.
Tests: explicit `--run-id` still wins over the manifest; an omitted
`--run-id` adopts the manifest's; an omitted input adopts
`latest_output`; each stage appends a `StageEntry` with the resolved
paths; behaviour is unchanged when no manifest exists.

### 4. Wire retrieval, export, and import

Give [retrieve.py](../../src/laglitsynth/fulltext_retrieval/retrieve.py),
[export.py](../../src/laglitsynth/fulltext_retrieval/export.py), and
[import_.py](../../src/laglitsynth/fulltext_retrieval/import_.py) the same
resolution and `append_stage` call. `--catalogue` and
`--screening-verdicts` stop being required on all three: they are
resolved from the manifest when omitted, and the existing
required-flag error remains for the no-manifest case. Tests:
`fulltext-retrieval-export --data-dir` alone produces the same bundle as
the fully-flagged invocation; each of the three appends its entry; a
manifest naming a missing file raises the operator-visible error from
the Risks section.

### 5. Wire the full-text stages and write the docs

Extend the same wiring to
[eligibility.py](../../src/laglitsynth/fulltext_eligibility/eligibility.py)
and [extract.py](../../src/laglitsynth/extraction_codebook/extract.py), with
the per-stage tests from commit 3. Then write
[docs/run-manifest.md](../../docs/run-manifest.md) describing the file, the
relay, and the cross-machine run-id story, and link it from
[README.md](../../README.md) and [interfaces.md](../../docs/interfaces.md);
move this plan to [plans/done/]() and update
[roadmap.md](../roadmap.md).

## Follow-ups

Stages 1 (catalogue-fetch) and 6 (fulltext-extraction) get manifest
entries when their place in the chain is settled — fetch is
many-files-in so its `output` is a glob, extraction's input is the
retrieval PDF store; both resolve cleanly but are lower-leverage than
the verdict-stage relay and can land once the core chain proves out.
Reproducibility stays partial and honestly so: the manifest records
seeds-and-config lineage, not bit-identical verdicts — LLM stages
sample at `temperature=0.8`, unchanged here. And the manifest is the
address space the cross-machine verdict merge will write into, but
that merge — append-only log + derived view + idempotent superposing
import for the verdict stages — is its own plan.

## Risks

A stale `data/manifest.json` carried across machines could point
stage N at an upstream output that does not exist on the receiving
disk (rsync brought the manifest but not the file). Mitigation:
`latest_output` returns a path, and the stage validates it exists
before use, falling back to the required-flag error with a message
naming the manifest path — a missing resolved input is operator-
visible, never silently skipped.

## Critical files

- [src/laglitsynth/io.py](../../src/laglitsynth/io.py)
- [src/laglitsynth/ids.py](../../src/laglitsynth/ids.py)
- [src/laglitsynth/models.py](../../src/laglitsynth/models.py)
- [src/laglitsynth/config.py](../../src/laglitsynth/config.py)
- [src/laglitsynth/cli.py](../../src/laglitsynth/cli.py)
- [src/laglitsynth/screening_abstracts/screen.py](../../src/laglitsynth/screening_abstracts/screen.py)
- [src/laglitsynth/fulltext_eligibility/eligibility.py](../../src/laglitsynth/fulltext_eligibility/eligibility.py)
- [src/laglitsynth/extraction_codebook/extract.py](../../src/laglitsynth/extraction_codebook/extract.py)
- [src/laglitsynth/catalogue_dedup/dedup.py](../../src/laglitsynth/catalogue_dedup/dedup.py)
- [src/laglitsynth/fulltext_retrieval/retrieve.py](../../src/laglitsynth/fulltext_retrieval/retrieve.py)
- [src/laglitsynth/fulltext_retrieval/export.py](../../src/laglitsynth/fulltext_retrieval/export.py)
- [src/laglitsynth/fulltext_retrieval/import_.py](../../src/laglitsynth/fulltext_retrieval/import_.py)
- [docs/explorations/running-the-pipeline.md](../../docs/explorations/running-the-pipeline.md)
- [plans/fulltext-retrieval-diversified.md](fulltext-retrieval-diversified.md)
