# Usability — documentation fixes

## Goal

Close the documentation gap a manual user hits when running stages 1–8
without [`scripts/run-pipeline.sh`](../scripts/run-pipeline.sh) or
[`scripts/nesh-pipeline.sbatch`](../scripts/nesh-pipeline.sbatch). The
[usability review](../docs/explorations/usability-review.md) catalogued
twenty-five findings; this plan addresses every doc-only one. Code-side
fixes (`--num-ctx`, `--concurrency` on stages 7/8, preflight error
split, `.env` fallback, stage 8 default model, `bake-model`,
`pipeline-up`) live in
[`usability-cli.md`](usability-cli.md).

The centrepiece is one new file:
[`docs/external-services.md`](../docs/external-services.md). It
consolidates the Ollama and GROBID setup story that's currently
scattered across four docs and one source file. Per-stage docs reduce
their prerequisites blocks to a single link; the README's `## Setup`
section gains the missing install hints; investigative docs under
`docs/explorations/` stay where they are.

## Non-goals

- Anything that changes a CLI flag or error message — code work, in
  [`usability-cli.md`](usability-cli.md).
- The architectural shifts from
  [`usability-review.md` section 4](../docs/explorations/usability-review.md)
  (pipeline-level config, run-id elevation, `pipeline-up` subcommand,
  `examples/` rename) — separate plans when each is decided.
- Path-bug fixes in per-stage docs that
  [`verdicts-only-cutover.md`](verdicts-only-cutover.md) already
  rewrites in its step 6 doc sweep — overlap is intentional but not
  duplicated work.
- README cleanup beyond the four hygiene items below
  ([`README.md`](../README.md) section reordering, prose tightening,
  reviewer-export schema details). The README rewrite that the cutover
  plan does in step 6 covers the structural shape; this plan adds the
  external-deps content gap.

## Target state

### New file: `docs/external-services.md`

Single runbook a manual user reads before stages 3, 6, 7, 8. Sequence:

1. **Ollama install** — link to <https://ollama.com/download>; Linux
   `curl -fsSL https://ollama.com/install.sh | sh` (full installer,
   not just the binary, per the lessons in
   [`nesh-ollama.md`](../docs/explorations/nesh-ollama.md)); macOS
   `brew install --cask ollama`.
2. **Start Ollama with `OLLAMA_NUM_PARALLEL` set** — explicit warning
   that the env var is read at `ollama serve` start time and exporting
   it later is a silent no-op (per
   [`llm-concurrency.md`](../docs/llm-concurrency.md) L25–32).
3. **Pull the models** — `gemma3:4b` for stages 3 and 7,
   `llama3.1:8b` (or larger) for stage 8 with the empty-JSON warning
   from
   [`extraction-codebook.md`](../docs/extraction-codebook.md)
   L251–256. Single-line quick-start: `ollama pull gemma3:4b && ollama
   pull llama3.1:8b`.
4. **Bake Modelfiles for `num_ctx`** — the
   [`scripts/nesh-pipeline.sbatch`](../scripts/nesh-pipeline.sbatch)
   L175–192 heredoc lifted into a doc-side recipe with `ollama create
   laglit-extract -f Modelfile`. Explain that the OpenAI `extra_body`
   `num_ctx` hint is not reliable; only the bake works. Cross-link to
   [`usability-cli.md`](usability-cli.md) for the future
   `bake-model` subcommand.
5. **Remote Ollama via SSH tunnel** — concrete `ssh -L
   11434:localhost:11434 user@host`, plus the two-hop
   `ssh -A -L … && ssh -R …` pattern from
   [`nesh-ollama.md`](../docs/explorations/nesh-ollama.md) L33–55.
6. **GROBID install** — `docker run --rm -p 8070:8070
   lfoppiano/grobid:0.8.0`; Apple Silicon Rosetta caveat
   (`softwareupdate --install-rosetta` first; image is amd64-only);
   30–60 s startup; health check `curl http://localhost:8070/api/isalive`.
7. **GROBID memory tuning** — Docker Desktop default is 2 GB; GROBID
   wants ~2 GB itself; recommend 6–8 GB to give the JVM headroom (per
   [`nesh-grobid.md`](../docs/explorations/nesh-grobid.md) L125).
8. **macOS Docker `localhost:8070` workaround** — promote the
   [`grobid-lessons.md`](../docs/grobid-lessons.md) L54–69
   network-namespace trick into canonical guidance under a clearly
   labelled "macOS troubleshooting" subsection.
9. **`consolidateCitations=0` rationale** — one short subsection
   explaining the default-off and pointing at
   [`grobid-lessons.md`](../docs/grobid-lessons.md) for the cost
   reasoning. Don't expose the flag here; that's a CLI decision in
   [`usability-cli.md`](usability-cli.md).
10. **GROBID mid-corpus crash recovery** — name `--skip-existing` as
    the recovery path explicitly. "If GROBID crashed at PDF 47/200,
    re-run with `--skip-existing` after restarting the container."
11. **Manual NESH section** — promote the working invocations from
    [`nesh-ollama.md`](../docs/explorations/nesh-ollama.md) and
    [`nesh-grobid.md`](../docs/explorations/nesh-grobid.md) (SIF path,
    bind mounts, `--pwd`, login-node proxy `10.0.7.235:3128`) so a
    user writing their own sbatch script has an authoritative
    reference. The exploration docs stay where they are; this is a
    canonical mirror, not a move.

The doc lives in [`docs/`](../docs/), not under `docs/explorations/`,
because per [AGENTS.md](../AGENTS.md) it's descriptive (states facts
about how to set the dependencies up), not investigative.

### Per-stage doc updates

Each stage doc that today opens with "ollama serve" or "expects GROBID
at port 8070" gets its prerequisites block replaced with a one-line
link to [`external-services.md`](../docs/external-services.md). The
in-line snippets (e.g. the `ollama pull gemma3:4b` line in
[`screening-abstracts.md`](../docs/screening-abstracts.md) L13) shrink
to a quick-start example with the link for full setup.

Specific edits:

- [`docs/screening-abstracts.md`](../docs/screening-abstracts.md)
  L8–22 — replace the "Prerequisites" block with a link; add a
  "Reproducibility" subsection (~one paragraph) explaining
  `temperature=0.8` + per-call `seed` + `prompt_sha256` doesn't
  include the seed (closes [O7](../docs/explorations/usability-review.md)).
- [`docs/eligibility.md`](../docs/eligibility.md) — add a
  "Prerequisites" block linking to `external-services.md`; add a
  one-liner "**No `--concurrency` flag.** Stage 7 calls Ollama
  sequentially regardless of `OLLAMA_NUM_PARALLEL`. See
  [llm-concurrency.md](../docs/llm-concurrency.md)." (closes
  [O5](../docs/explorations/usability-review.md) doc-side).
- [`docs/extraction-codebook.md`](../docs/extraction-codebook.md) —
  same prerequisites + concurrency note as eligibility; add the
  Reproducibility cross-reference; in the "Pass a bigger model"
  sentence at L256, append `pull it first with ollama pull
  llama3.1:8b (or gemma3:12b, qwen2.5:14b)` (closes
  [O4](../docs/explorations/usability-review.md)).
- [`docs/fulltext-extraction.md`](../docs/fulltext-extraction.md) —
  add a "Prerequisites" block at the top with the GROBID `docker run`
  command and a link to `external-services.md` for everything else
  (closes [G1](../docs/explorations/usability-review.md));
  pull in a `--skip-existing` recovery one-liner under "If GROBID
  crashes mid-corpus" (closes
  [G5](../docs/explorations/usability-review.md)). The macOS
  workaround stays in `external-services.md`; this doc just links to
  it.

### `interfaces.md` end-to-end caveat

[`docs/interfaces.md`](../docs/interfaces.md) — add a clearly visible
"STOP HERE — stages 9–12 are not yet implemented" block between the
stage 8 and stage 9 commands in the End-to-end sequence (currently at
L338–358). Keeps the canonical sequence honest.

### README hygiene

[`README.md`](../README.md) edits, all in `## Tools`, `## Setup`,
`## Ollama (for LLM stages)`, and `## GROBID (for full-text
extraction)`:

- **Number the eight implemented stages 1.–8.** in `## Tools`. Match
  the `Stage 1 — …` / `Stage 8 — …` form used in
  [`docs/pipeline.md`](../docs/pipeline.md) and
  [`docs/interfaces.md`](../docs/interfaces.md).
  (P10)
- **Fold the trailing "Stage 9+ are specified in pipeline.md but not
  yet implemented" paragraph** into the section's lead sentence —
  one statement of the implemented/not-yet-implemented split, naming
  stages 9–12 explicitly. (P10)
- **Setup block: add `set -a; source .env; set +a` example** —
  one-liner under
  [`## Setup`](../README.md) saying ".env is read by the wrappers; if
  you invoke stages by hand, source it first." (P6 doc-side, closes
  the manual-user trap.)
- **Ollama section: install hint, `OLLAMA_NUM_PARALLEL` warning, link
  to `external-services.md`** — three additions:
  - Before the `ollama serve` line at L215, three install lines for
    Linux / macOS / link to download. (O1)
  - Change `ollama serve` to
    `OLLAMA_NUM_PARALLEL=2 ollama serve  # set before serve; export after start is silently ignored`
    with the explanation as a code-block comment. (O2)
  - Append a "See [`external-services.md`](docs/external-services.md)
    for the full setup runbook" line at the end of the section.
- **GROBID section: Apple Silicon caveat + link** — append a comment
  to the docker run line at L237: `# Apple Silicon: image is
  amd64-only; install Rosetta for Linux first (softwareupdate
  --install-rosetta).` (G2). Add the same trailing
  `external-services.md` cross-link.

## Implementation sequence

Three commits. `pixi run typecheck` and `pixi run test` are unaffected
(documentation only); CI passes through.

### 1. New: `docs/external-services.md`

Write the runbook from scratch following the eleven-section structure
above. Cross-link from each section back to the original
`docs/explorations/` doc that the content was derived from, so a
reader who wants the full investigation can find it. Keep prose
tight; the doc is for setup, not for explanation. Target length
roughly 150–250 lines. Add the file to
[`plans/roadmap.md`](roadmap.md) as a documentation-stage artefact.

### 2. Per-stage doc updates + `interfaces.md` STOP HERE

One commit covering:

- [`docs/screening-abstracts.md`](../docs/screening-abstracts.md) —
  prereq block link; new Reproducibility subsection.
- [`docs/eligibility.md`](../docs/eligibility.md) — prereq block link;
  no-concurrency one-liner.
- [`docs/extraction-codebook.md`](../docs/extraction-codebook.md) —
  prereq block link; no-concurrency one-liner; pull-the-model hint;
  Reproducibility cross-reference.
- [`docs/fulltext-extraction.md`](../docs/fulltext-extraction.md) —
  GROBID prereq block; crash-recovery note.
- [`docs/interfaces.md`](../docs/interfaces.md) — STOP HERE block
  between stages 8 and 9 in the end-to-end sequence.

The
[`verdicts-only-cutover.md`](verdicts-only-cutover.md) plan's step 6
doc sweep happens later. If that plan lands first, this commit
rebases against the rewritten path examples; the prereq blocks and
Reproducibility subsections are additive in either order.

### 3. README hygiene

One commit touching only [`README.md`](../README.md):

- Stage list numbered, trailing paragraph folded.
- Setup block: `set -a; source .env; set +a` line.
- Ollama section: install hints + `OLLAMA_NUM_PARALLEL` warning +
  external-services link.
- GROBID section: Apple Silicon caveat + external-services link.

Keep the rest of the README untouched. The architectural rewrite (run
by hand subsection, "Running the pipeline" non-wrapper variant)
belongs to a follow-up plan once `external-services.md` exists and a
"run by hand" reference path is concrete.

## Follow-ups

- README "Run by hand" subsection — add after
  `external-services.md` and the cutover land. Pulls the manual
  end-to-end sequence from [`interfaces.md`](../docs/interfaces.md)
  and links each step into the right per-stage doc.
- [`examples/`](../examples/) framing fix
  ([A5](../docs/explorations/usability-review.md)) —
  rename to `defaults/` or add an `examples/README.md` saying "these
  are the shipped defaults, not optional sample data." Tiny
  commit, defer until the cutover docs are settled.
- Pipeline-level config consolidation
  ([A1](../docs/explorations/usability-review.md)) — bigger
  architectural plan. Out of scope for any usability doc.

## Risks

**Doc drift between `external-services.md` and the exploration
docs.** The promotion of `nesh-ollama.md` and `nesh-grobid.md`
content into a canonical doc creates two copies of the same setup
text. Mitigation: the canonical doc states facts; the exploration
docs keep the *why* and the investigative log. Cross-link both
directions. Future operational changes go to the canonical doc;
exploration docs are append-only history.

**Cutover-plan rebase.** If the
[`verdicts-only-cutover.md`](verdicts-only-cutover.md) plan lands
between commit 1 and commit 2 here, the per-stage doc edits in step 2
need to rebase against the rewritten CLI examples. Low cost —
the prereq-block additions and Reproducibility subsections are
orthogonal to the CLI shape. Worst case is a five-minute rebase.

**Apple Silicon GROBID guidance is approximate.** The Rosetta install
recipe (`softwareupdate --install-rosetta`) is the standard
Apple-recommended path, but the experience varies with Docker Desktop
versions. The doc should say "if this doesn't work, see
[grobid-lessons.md](../docs/grobid-lessons.md) for the
network-namespace workaround that's known to work."

## Critical files

- New: [`docs/external-services.md`](../docs/external-services.md).
- Edited: [`docs/screening-abstracts.md`](../docs/screening-abstracts.md),
  [`docs/eligibility.md`](../docs/eligibility.md),
  [`docs/extraction-codebook.md`](../docs/extraction-codebook.md),
  [`docs/fulltext-extraction.md`](../docs/fulltext-extraction.md),
  [`docs/interfaces.md`](../docs/interfaces.md),
  [`README.md`](../README.md).
- Read for content (not edited):
  [`docs/explorations/nesh-ollama.md`](../docs/explorations/nesh-ollama.md),
  [`docs/explorations/nesh-grobid.md`](../docs/explorations/nesh-grobid.md),
  [`docs/grobid-lessons.md`](../docs/grobid-lessons.md),
  [`docs/llm-concurrency.md`](../docs/llm-concurrency.md),
  [`scripts/nesh-pipeline.sbatch`](../scripts/nesh-pipeline.sbatch).
- Roadmap: [`plans/roadmap.md`](roadmap.md) — add this plan as
  in-flight; archive when each commit lands.
