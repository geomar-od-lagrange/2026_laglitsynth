# Running the pipeline: operations, storage, and many machines

A musing on what it is actually like to *drive* this pipeline — not to set
it up (Ollama, GROBID, `.env`, doc consistency: all covered exhaustively in
[usability-review.md](usability-review.md), which I do not repeat), but to
run stage after stage, decide where things live, and then split the work
across a laptop, an HPC node, and a collaborator's machine. The setup review
asks "can a new postdoc get one stage to run?"; this asks "once they can,
what is the lived experience of a whole review, and what breaks when the
review stops being one person on one disk?"

Sources grounding this: the stage graph in [pipeline.md](../pipeline.md),
the contracts in [interfaces.md](../interfaces.md), the dispatcher in
[cli.py](../../src/laglitsynth/cli.py), the per-stage runners (e.g.
[screen.py](../../src/laglitsynth/screening_abstracts/screen.py),
[eligibility.py](../../src/laglitsynth/fulltext_eligibility/eligibility.py),
[extract.py](../../src/laglitsynth/extraction_codebook/extract.py)), the
shared [io.py](../../src/laglitsynth/io.py) / [ids.py](../../src/laglitsynth/ids.py)
/ [dotenv.py](../../src/laglitsynth/dotenv.py), and the distributable-retrieval
design in [fulltext-retrieval-diversified.md](../../plans/fulltext-retrieval-diversified.md).

## 1. Running all stages: the handoff is hand-carried

The canonical sequence is the one in [interfaces.md](../interfaces.md)
"End-to-end sequence". Reading the runners, the load-bearing fact about
*driving* it is this: **no stage discovers its own inputs.** Every input is
an explicit, required path. Dedup requires `--input` and `--output-dir`
([dedup.py](../../src/laglitsynth/catalogue_dedup/dedup.py) — both
`required=True`); retrieval requires `--catalogue`, `--screening-verdicts`,
`--output-dir`; eligibility and extraction require the catalogue plus the
upstream verdict sidecar. Nothing reads "the latest dedup output" or "the
most recent screening run." The user is the dataflow engine — they carry
each path forward by hand.

That is a clean, debuggable design (explicit beats magic, and it matches
"separate concerns"), but it has two specific friction points when you
actually run it rather than read it:

The **run-id leaf has to be threaded by the human.** Stages 3, 7, 8 each
call `generate_run_id()` independently if `--run-id` is omitted
([screen.py](../../src/laglitsynth/screening_abstracts/screen.py) L263–265
and siblings), so without intervention each stage invents a *different*
random leaf and stage 7 has no idea which `screening-abstracts/<run-id>/`
stage 3 wrote. The only way to make a coherent run is the wrapper's trick:
generate one `RUN_ID` up front with `laglitsynth generate-run-id` and pass
`--run-id "$RUN_ID"` to all three. The earlier review files this as P1/A2;
worth adding from the *operational* side is a half-correction — the stages
**do** print `Output dir: <path>` to stderr at start
([screen.py](../../src/laglitsynth/screening_abstracts/screen.py) L282,
[eligibility.py](../../src/laglitsynth/fulltext_eligibility/eligibility.py)
L430), so the run-id is not invisible. But it is printed to *stderr at
start*, interleaved with progress logging, not emitted as a machine-readable
last line. You can read it; you cannot easily `RUN_ID=$(laglitsynth
screening-abstracts ... )` it. So the recover-the-run-id path exists for a
human staring at a terminal and is absent for a script. That asymmetry is
the actual footgun: the interactive user thinks "fine, I'll copy it," the
scripting user reaches for a wrapper, and the two never converge on one
pattern.

The **stage-1 → stage-2 filename mismatch** is real but mild operationally:
fetch writes `data/catalogue-fetch/<slug>_<ts>.jsonl`
([fetch.py](../../src/laglitsynth/catalogue_fetch/fetch.py) `_default_output_path`),
dedup's `--input` takes a glob, and `interfaces.md` correctly shows
`--input "data/catalogue-fetch/*.jsonl"`. So the *contract* is fine; only
some per-stage docs show a stale `combined.jsonl`. The footgun here is
quoting: an unquoted glob that the shell expands to multiple args versus the
quoted glob the tool expands itself — easy to get a "only the first file was
read" surprise and not notice.

Net: running the pipeline today is a copy-paste relay. It works, it is
auditable, and it is exactly as ergonomic as the discipline of the person at
the keyboard. There is no single "advance the pipeline" affordance.

## 2. Where to store things: clear per stage, unclear per project

Per-stage storage is unambiguous and consistent: each stage owns a subdir
under `data/` (`data/catalogue-fetch/`, `data/catalogue-dedup/`,
`data/fulltext-retrieval/`, …), writes JSONL records plus a `.meta.json` /
`*-meta.json` sidecar carrying [`RunMeta`](../../src/laglitsynth/models.py),
and the LLM stages add a `<run-id>/` leaf with a self-contained
`config.yaml` snapshot. [io.py](../../src/laglitsynth/io.py) is the whole
storage layer — `write_jsonl`, `append_jsonl`, `write_meta`. There is one
obvious place per stage. Good.

What is *not* clear is the layer above the stage. There is **no notion of a
"project" or a "review run" that groups a whole pipeline execution.** `data/`
is a flat set of stage subdirs, and a second query, a second screening
threshold, or a second reviewer's pass all land in the *same* stage subdirs.
The only grouping primitive is the run-id leaf — and it exists for exactly
three stages (3, 7, 8). Stages 1, 2, 5, 6 have no run-id at all: dedup
overwrites `data/catalogue-dedup/deduplicated.jsonl`, retrieval overwrites
`data/fulltext-retrieval/`. So:

- Two different *queries* (a Lagrangian-particle search and a tracer search)
  cannot coexist in one `data/` without either manual subdir juggling or
  clobbering. The "flag, don't filter" design assumes one canonical
  deduplicated catalogue as the spine of the whole pipeline
  ([interfaces.md](../interfaces.md) "flag, don't filter"); a second
  independent review has to live in a second working directory entirely.
- Multiple *screening* runs over one catalogue coexist fine (distinct
  run-ids), but nothing records *which* run-id is "the current one." The
  config.yaml snapshot per run is excellent provenance, but there is no
  index — no `runs.json` saying "screening run X used threshold 50 and feeds
  eligibility run Y." You reconstruct lineage by reading meta files and
  remembering which run-id you typed.

So the honest answer to "is it clear where to store stuff?" is: *within a
stage, yes; across a whole review, you are expected to use the working
directory itself as the project boundary, and that convention is nowhere
stated.* The de-facto project = "a clone with a `data/` dir." That is a fine
practical answer (it is git-clone-shaped and rsync-shaped, which matters for
section 4), but it is implicit, and it interacts badly with the lack of
run-grouping the moment you want two queries or want to remember which
run-id was the good one.

Candidate for a future plan, not designed here: a lightweight **run
manifest** — one file per pipeline execution recording the query/queries,
the chosen run-id, thresholds, model tags, and the resolved input→output
path chain — written by each stage and read by the next so the relay in
section 1 becomes "stage N reads the manifest, finds stage N−1's output,
appends its own entry." That single artifact would dissolve both the
run-id-threading friction (section 1) and the no-project-grouping gap
(section 2), and it is the natural home for the cross-machine merge state in
section 4.

## 3. Defining a review: what state has to be tracked

A researcher setting up a *new* review is choosing and pinning a set of
decisions that are currently scattered across CLI flags, example YAML files,
and the run-id snapshots:

- One or more **queries** (stage 1, positional) — possibly several, covering
  sub-disciplines, per [pipeline.md](../pipeline.md) stage 1.
- A **screening prompt** (stage 3, positional) and a **screening threshold**
  (a flag that reappears on stages 5/7 because they re-join at read time —
  see "flag, don't filter").
- An **eligibility-criteria** YAML and a **codebook** YAML — shipped under
  [`examples/`](../../examples/) but functionally the production defaults
  (the setup review's A5 makes this point; it bears on reproducibility too).
- **Model tags** and **`num_ctx`** per LLM stage — and on a real machine,
  the baked `laglit-*` Ollama tags that [bake_model](../../src/laglitsynth/bake_model.py)
  now produces.

Reproducibility is **partial and honestly so.** The per-run `config.yaml`
plus the `RunMeta`/`LlmMeta` sidecars capture model, threshold, and
`prompt_sha256` — enough to know *how* a run was configured. But the LLM
stages sample at `temperature=0.8` with per-call seeds, so re-running the
same configuration reproduces the *setup*, not the *verdicts* (the data
model docs say this; it surprises people). "Reproducible" here means
"re-derivable decisions and re-runnable configuration," not "bit-identical
outputs." That is a defensible research-tool stance, but a researcher
*defining* a review should know it up front, because it changes what
"resume" means.

**Resumability** is good where it is implemented and absent where it is not.
`--skip-existing` on retrieval, extraction, eligibility lets a crashed long
run pick up the delta — the load-bearing affordance for the corpus stages.
But there is no resume *across* the pipeline: if you lose the shell that held
`$RUN_ID`, you resume by reading stderr scrollback or `ls data/*/`. A run
manifest (section 2) is again the missing piece — it would be the thing you
re-open to answer "where was I."

So a realistic "define a review" session today: pick a working directory,
write/choose the criteria and codebook YAMLs, decide threshold and models,
generate one run-id, and then *keep that run-id and your flag set
somewhere yourself* — a scratch shell script, effectively. The wrappers
([scripts/run-pipeline.sh](../../scripts/run-pipeline.sh)) are that
scratch script, promoted. The gap is that the project offers no first-class
"this is my review's configuration" object short of the wrapper; the
pipeline-level config sketched as A1 in the setup review is the same idea
viewed from the config angle that the run-manifest is viewed from the state
angle. They probably want to be one thing.

## 4. Many machines: the part the architecture half-anticipates

This is where it gets interesting, because the data model is *almost* built
for it and the new retrieval plan makes the intent explicit.

What has to travel between machines is small and well-bounded, precisely
because storage is plain JSONL + sidecars under `data/`:

- The **deduplicated catalogue** (`data/catalogue-dedup/deduplicated.jsonl`)
  is the spine and is machine-independent — rsync it once.
- **Verdict sidecars** are tiny and self-describing (JSONL + meta). They move
  trivially.
- **PDFs and TEI** are the bulk and the reason NESH exists as a target
  (storage as much as compute — the project's own framing). These are the
  thing you do *not* want to move twice.
- **`.env`** must be re-created per machine, never synced (it is read from
  the *cwd* by [dotenv.py](../../src/laglitsynth/dotenv.py) `Path(".env")`,
  and only as a fallback for `--api-key` / `--email`). A collaborator needs
  their *own* Unpaywall email and OpenAlex key, not yours.

The natural division of labour falls out of the stage graph:

- **Laptop**: catalogue-fetch, dedup, screening (or screening on a tunneled
  GPU). Light data, interactive.
- **NESH**: the GPU/storage-heavy stages — eligibility, extraction-codebook,
  and ideally the whole corpus so the PDFs never have to come home. The
  memory context's "NESH as target for the whole pipeline" is exactly this:
  not just inference, but keeping the bulk where the disk is.
- **Collaborators**: full-text *retrieval* of paywalled PDFs via their own
  institutional access — the one stage that fundamentally cannot be
  centralised, because access is per-person.

The retrieval plan
([fulltext-retrieval-diversified.md](../../plans/fulltext-retrieval-diversified.md))
is the first place the codebase treats multi-machine as a first-class
concern rather than an rsync afterthought. Its model is the right shape for
*all* the gate stages, not just retrieval: an append-only `attempts.jsonl`
that is the source of truth, a derived `coverage.jsonl` rewritten each run,
idempotent import keyed by `(work_id, resolver, collaborator,
content_sha256)`, and explicit export→retrieve→import subcommands so a
collaborator's partial coverage *superposes* onto the corpus without
clobbering. That is a real distributed-merge design. It pairs naturally with
the WoS/OpenAlex "second catalogue" idea
([wos-starter-api.md](wos-starter-api.md)): multiple metadata sources merged
on a normalised DOI key is the same union-by-stable-key pattern as
multiple retrieval sources merged per work, and the
[zotero-retrieval.md](zotero-retrieval.md) recommendation (thin REST
wrappers, no sidecar service) keeps each machine's footprint a single pixi
env rather than a Docker zoo — which is exactly what you want when "each
machine" includes a collaborator who is not a developer.

But the gaps that make cross-machine runs painful *today*, before that plan
lands, are concrete:

- **Only retrieval gets the merge model.** Screening, eligibility, and
  extraction still write whole-file outputs under a run-id. If two machines
  each screen *half* the catalogue (a natural split for a slow local LLM),
  there is no `import`/superpose path — you would manually concatenate two
  `verdicts.jsonl` files and hope the run-ids and metas reconcile. The
  attempts/coverage pattern is general; only retrieval is slated to get it.
  A candidate future plan: generalise append-only-log + derived-view +
  idempotent-import to the verdict stages, so "machine A screened works
  1–500, machine B screened 501–1000" merges as cleanly as two retrieval
  collaborators.
- **No sync convention.** "rsync `data/` between laptop and NESH" is the
  implied mechanism and it is never written down — which direction, which
  subdirs are safe to overwrite (PDFs: yes; verdicts: depends), what is
  authoritative when both sides changed. With whole-file outputs and no
  manifest, a careless `rsync --delete` the wrong way loses a run. The
  append-only model in the retrieval plan is partly a *defense* against
  exactly this (you can only ever union attempts), which is another argument
  for generalising it.
- **Path portability.** Outputs store repo-relative paths (the retrieval
  plan is careful to say `pdf_path` is repo-relative), but `--data-dir`
  defaults to `Path("data")` relative to cwd, and `.env` is read from cwd.
  So the cross-machine contract is implicitly "always run from the project
  root, keep `data/` at a fixed relative location." That is fine and
  rsync-friendly *if stated*; today it is load-bearing and unstated.
- **Run-id coordination across machines.** If NESH runs eligibility and the
  laptop later runs extraction, they must agree on the run-id — and the only
  way to share it is to type the same string on both. A manifest that
  travels with `data/` would carry the run-id with the data instead of in
  someone's shell history.

## Synthesis

The pipeline is *operationally* sound at the per-stage level — explicit
inputs, plain JSONL, meta sidecars, `--skip-existing` resume, per-run
config snapshots — and the new retrieval plan shows the maintainers already
reaching toward proper distributed semantics. The friction is concentrated
in three seams, all above the individual stage:

1. The stage-to-stage handoff is a human-carried path relay, worst at the
   run-id leaf, which is printed-to-stderr-readable but not script-capturable
   and not coordinated across stages or machines.
2. There is no project/run grouping: `data/` is per-stage-flat, two queries
   collide, and lineage lives in operator memory plus scattered meta files.
3. Cross-machine work has a clean data substrate but no convention — only
   retrieval gets a real merge model; everything else is whole-file outputs
   and an unwritten rsync ritual.

Top three candidates for a follow-up usability plan, in leverage order:

- **A run manifest / pipeline-config object** (sections 2 + 3) — one file
  that pins queries, thresholds, models, and the run-id, is written by each
  stage and read by the next, and travels with `data/`. Collapses the
  run-id-threading friction, the no-project-grouping gap, the "where was I"
  resume gap, and the cross-machine run-id-coordination gap into one
  artifact. This is the A1/A2 ideas from the setup review, fused and viewed
  as *state* rather than *flags*.
- **Generalise the attempts/coverage merge model** beyond retrieval
  (section 4) — append-only log + derived view + idempotent superposing
  import for the verdict stages, so the pipeline can genuinely fan a stage
  out across machines/collaborators and union the results, not just for
  PDFs.
- **A written cross-machine + storage convention** (sections 2 + 4) — a
  short authoritative doc stating the project = working-directory boundary,
  the run-from-root / fixed-`data/` path contract, per-machine `.env`, and
  the safe sync direction per subdir. Cheap, no code, and the precondition
  for the other two not being misused.
