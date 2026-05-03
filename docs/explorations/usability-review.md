# Usability review — running the pipeline without the wrappers

A simulated first-time onboarding read of the repo. The reviewer is a
postdoc who just cloned the project and wants to deploy stages 1..8
end-to-end **without** invoking [`scripts/run-pipeline.sh`](../../scripts/run-pipeline.sh)
or [`scripts/nesh-pipeline.sbatch`](../../scripts/nesh-pipeline.sbatch).
They will read [`README.md`](../../README.md), follow its links, and
construct each stage invocation themselves.

This document is a single merged record. Section 1 is the original
walkthrough; section 2 ranks pain points across stage flow, Ollama
setup, and GROBID setup; sections 3–5 cover what the wrappers hide,
the architectural implications, and the recommended next concrete
commit. Findings ending in a P-number are about pipeline / stage
flow; O-numbers are Ollama-specific; G-numbers are GROBID-specific;
A-numbers are architectural takeaways.

## 1. Walkthrough log

### Open the README

I land on [`README.md`](../../README.md). The Setup block (L6–13) tells
me to `pixi install` and copy `.env.example` to `.env`. So far so good —
I open [`.env.example`](../../.env.example): two variables,
`OPENALEX_API_KEY` and `UNPAYWALL_EMAIL`, both with a one-line
description of where to register / what to put. Fine.

### "Tools" section reads like a CLI menu, not a runbook

L15–53 enumerates the eight subcommands and links to per-stage docs.
The sentence "Stages 1–8 are implemented" implies each link below is
self-contained. There's **no introductory sentence** that says "the
canonical way to run all eight in order is documented in
[`docs/interfaces.md`](../interfaces.md) under End-to-end sequence."
A reader who, like me, declines the wrapper has to back-walk into that
file later. The list also reads as bullet points rather than numbered
stages — every other doc in the repo (`docs/pipeline.md`,
`docs/interfaces.md`) refers to stages by number (`Stage 1 — …`,
`Stage 8 — …`) but the README's `## Tools` section does not, so a
reader cross-referencing has to do the renumbering themselves.

### "Running the pipeline" assumes I want the wrapper

L55–161 is dominated by the two scripts. The "Locally" subsection
(L73–86) has three lines of `ollama serve` / `docker run` /
`scripts/run-pipeline.sh`. There is no equivalent block for "run the
stages by hand." If I want to deviate from the wrapper, I get nothing
here — I have to skip ahead.

### Hunting the manual sequence

I open [`docs/pipeline.md`](../pipeline.md) hoping for a runbook.
That doc is purely conceptual: stage definitions, mermaid flowchart,
domain rationale. No commands. So I scan for a CLI sequence and end up
at [`docs/interfaces.md`](../interfaces.md). The "End-to-end sequence"
block (L273–358) is what I want — but I had to discover it. The README
never mentions `interfaces.md` (one cross-link, in the dataflow context,
nothing in setup or running).

### Stage 1: catalogue-fetch

[`docs/catalogue-fetch.md`](../catalogue-fetch.md) is clean. CLI
arguments table at L16–24 is unambiguous. I run:

```bash
laglitsynth catalogue-fetch "lagrangian particle tracking" \
    --api-key "$OPENALEX_API_KEY"
```

I now have `data/catalogue-fetch/<slug>_<timestamp>.jsonl`.

### Stage 2: catalogue-dedup — first dissonance

[`docs/catalogue-dedup.md`](../catalogue-dedup.md) L78 says:

```
laglitsynth catalogue-dedup \
    --input data/catalogue-fetch/combined.jsonl \
    --output-dir data/catalogue-dedup/
```

But stage 1 wrote `<slug>_<timestamp>.jsonl`, not `combined.jsonl`. The
note at L82–84 ("If multiple search result files need to be combined
first, concatenate them ... that is a trivial `cat` operation") only
mentions the multi-file case. A single-file user must either rename,
symlink, or `cat` their one file. The wrapper at
[`scripts/run-pipeline.sh#L76`](../../scripts/run-pipeline.sh) sidesteps
this by writing directly to `$ROOT/catalogue-fetch/catalogue.jsonl` —
i.e. by **not using the documented filename pattern at all**.

### Stage 3: screening-abstracts

[`docs/screening-abstracts.md`](../screening-abstracts.md) is the most
complete per-stage doc. Prerequisites (L8–22) include `ollama serve` and
`ollama pull gemma3:4b`. Good — Ollama setup is covered locally.

But: the doc says output lands in
`<data-dir>/screening-abstracts/<run-id>/`. The default `--data-dir` is
`data/`, and `--run-id` defaults to "a generated `<iso>_<12hex>`."
So I will get a path like
`data/screening-abstracts/2026-05-03T17-21-00_abc123def456/verdicts.jsonl`.

Stage 4 takes `--input data/screening-abstracts/verdicts.jsonl` per
[`docs/interfaces.md` L302](../interfaces.md). **That path will not
exist** — there is a run-id leaf in between. The interfaces doc itself
contradicts the run-id convention: L62 says output lives at the bare
`data/screening-abstracts/verdicts.jsonl`, while L228–229 acknowledges
the run-id directory model. Two parts of the same file disagree.

The wrapper handles this by generating a single `RUN_ID` once via
`laglitsynth generate-run-id` (`run-pipeline.sh#L54`) and threading it
through stages 3, 7, 8. **`generate-run-id` is not mentioned anywhere
in [`docs/`](.) or [`README.md`](../../README.md).** A user mimicking
the wrapper has to either grep the source or pass `--run-id` themselves.

### Stage 4: screening-adjudication

I dutifully copy the example from
[`docs/screening-adjudication.md` L26](../screening-adjudication.md):

```
laglitsynth screening-adjudication \
    --input data/screening-abstracts/verdicts.jsonl \
    ...
```

Same broken path as above. I have to fish for the run-id leaf, then
construct
`--input data/screening-abstracts/<run-id>/verdicts.jsonl`. The doc
never says this. (The wrapper just plugs in `$RUN_ID`.)

Note also: the doc shows `--screening-threshold 50`, and the wrapper at
`run-pipeline.sh#L97` **omits** this flag, relying on the default. Both
are valid, but a user reading the doc and the script in parallel will
wonder which is canonical.

### Stage 5: fulltext-retrieval

[`docs/fulltext-retrieval.md`](../fulltext-retrieval.md) L141–148 is
the cleanest stage doc. The `--manual-dir` and `--skip-existing`
semantics are explained; the unretrieved-DOI workflow (L193) is clear.
The Unpaywall email requirement is documented at L154 — but a user who
hasn't yet found `.env.example` would only learn about it here, two
stages too late.

### Stage 6: fulltext-extraction — GROBID surprise

[`docs/fulltext-extraction.md`](../fulltext-extraction.md) L195 expects
GROBID at `http://localhost:8070`. The README L75–76 mentions a
`docker run` line for GROBID, but the per-stage doc never tells me
**how to start it from scratch** — only "the CLI checks the GROBID
health endpoint before starting" (L223). I find the actual `docker run`
command at [`README.md` L236](../../README.md) and at
[`docs/grobid-lessons.md`](../grobid-lessons.md). On NESH, GROBID lives
in a Singularity container that the sbatch wrapper pulls — but for a
user wanting to run on NESH manually (sbatch but with a hand-coded
script), the SIF path, the bind mounts (`/opt/grobid/grobid-home/tmp`),
and the proxy address (`10.0.7.235:3128`) are all in the wrapper, not
in any doc. [`docs/explorations/nesh-grobid.md`](nesh-grobid.md) might
have it — but this lives under `explorations/`, branded as
"investigative write-ups, not authoritative references" by
[`AGENTS.md`](../../AGENTS.md). A reader has no signal that they
should read it as canon.

### Stage 7: fulltext-eligibility

[`docs/eligibility.md`](../eligibility.md) shows the canonical
invocation at L168–179. **The `--catalogue` argument is supposed to be
the included catalogue from stage 4.** The CLI sketch in
`interfaces.md` L207 says
`--catalogue data/screening-adjudication/included.jsonl` — and so does
the wrapper. Consistent here.

But the run-id resurfaces: stage 7's output lands at
`<data-dir>/fulltext-eligibility/<run-id>/eligible.jsonl`. To run stage
8 manually I need that run-id. The doc tells me at
L181 ("resolved output directory is …") but the example invocations at
L170 (and below) don't show how the run-id is surfaced. Looking at the
wrapper, the answer is: **only by reusing the same `RUN_ID`**.
No CLI subcommand prints "your run id is X" upon completion (or if
there is one, it's undocumented).

### Stage 8: extraction-codebook — most expert-only

[`docs/extraction-codebook.md`](../extraction-codebook.md) L196–206:

```
laglitsynth extraction-codebook \
    --eligible data/fulltext-eligibility/eligible.jsonl \
    ...
```

But the **actual** path is
`data/fulltext-eligibility/<run-id>/eligible.jsonl`
(per the same doc L208 and L114, and per
[`docs/configs.md` L68](../configs.md)). The example invocation in
extraction-codebook.md is wrong. interfaces.md L219 has the correct
form with `<run-id>`.

The "model sizing" warning at L251–256 is critical: gemma3:4b returns
empty JSON on full-text inputs, so the user *must* override `--model`.
Yet the wrapper sets `EXTRACTION_MODEL=llama3.1:8b` by default and the
README L70 says so — the per-stage doc doesn't promise this default and
shows `--model gemma3:4b` in the CLI sketch (L205). A user following
only the per-stage doc will run an extraction that silently produces
empty records.

The codebook YAML and eligibility-criteria YAML defaults
(`examples/...`) are documented, and they exist — good.

### Hidden infrastructure

After putting all of this together, I notice things the wrapper hides
that no doc surfaces if I bypass it:

- The single-`RUN_ID`-threaded-through-stages-3/7/8 trick.
- The `OLLAMA_NUM_PARALLEL=2` plus `LLM_CONCURRENCY=4` defaults (only
  in the sbatch script and in
  [`docs/llm-concurrency.md`](../llm-concurrency.md)).
- The Modelfile-`PARAMETER num_ctx` synthesis of `laglit-screen` /
  `laglit-eligibility` / `laglit-extract` tags (sbatch script
  L166–192). The README L107–116 mentions this in passing. Per-stage
  docs do not. A user running locally with long full-text prompts will
  silently get the upstream model's default `num_ctx` (often 2048),
  truncating most papers.
- `OLLAMA_NUM_PARALLEL` must be set **before** `ollama serve` starts;
  exporting it later is silently a no-op
  ([llm-concurrency.md L26–28](../llm-concurrency.md)). README L137
  references this only obliquely.

## 2. Pain points, ranked

Ordered by how badly each issue blocks a new user. Stage-flow findings
(P-numbers) come first, then dependency-specific findings under
`### Ollama setup` and `### GROBID setup` H3s. Where O-/G- findings
extend a P-finding, the cross-reference is called out.

### Stage flow and CLI examples

#### P1 — The run-id leaf is undocumented for manual users

Where it bites: stages 4, 7, 8 inputs all reference upstream stage-3 /
stage-7 outputs with a run-id leaf the user has no easy way to recover.
Why it bites: the per-stage CLI doesn't print the resolved output
directory at run completion (or if it does, the docs don't say so), and
the `--run-id` flag's default ("a generated `<iso>_<12hex>`") is
mentioned but never shown in any example output. The wrapper hides it
by generating one upfront.
Fix sketch: print `Run dir: <data-dir>/<stage>/<run-id>/` to stderr at
the end of every run-id'd stage; document it at the top of
[`docs/configs.md`](../configs.md) and in each stage's "Output format"
section. Bonus: document `laglitsynth generate-run-id` in
[`docs/interfaces.md`](../interfaces.md).

#### P2 — Stage 8's per-stage doc shows a wrong path and a bad default

Where it bites: a user copy-pasting from
[`extraction-codebook.md`](../extraction-codebook.md) L196–206 gets a
`--eligible` path that does not exist (no run-id leaf) and a default
`--model gemma3:4b` that the doc itself documents as broken on
full-text inputs (L251–256).
Why it bites: doc and reality diverge. The default in the CLI is
`gemma3:4b`; the wrapper overrides to `llama3.1:8b` so users running
the wrapper never see the failure mode. A user bypassing the wrapper
will silently get empty extractions.
Fix sketch: change the per-stage example to `--eligible
data/fulltext-eligibility/<run-id>/eligible.jsonl --model llama3.1:8b`
(or whatever model the wrapper actually uses), with a note that the
default is gemma3:4b for symmetry with stages 3 and 7 but **must** be
overridden. Better: change the CLI default to `llama3.1:8b` (or accept
no default and require `--model`).

#### P3 — There is no manual runbook; users have to assemble one from interfaces.md plus per-stage docs

Where it bites: README's "Running the pipeline" section is wrapper-only;
[`docs/pipeline.md`](../pipeline.md) is conceptual; the actual
end-to-end command sequence is buried mid-`interfaces.md` L273–358.
Why it bites: a postdoc who skips the wrapper has to discover
`interfaces.md` themselves (it's not linked from the README under
"Running"), and then cross-reference its commands against per-stage
docs because of the path inconsistencies.
Fix sketch: add a "Run by hand" subsection to README pointing at
`interfaces.md` L273–358, with a one-paragraph "and here's why you'd
want to" rationale. Or: lift the End-to-end sequence into its own doc,
e.g. `docs/run-by-hand.md`, and link it from README and from each
per-stage doc.

#### P4 — Per-stage docs disagree on input paths, especially around dedup output and run-id leaves

Where it bites: catalogue-dedup.md says `--input
data/catalogue-fetch/combined.jsonl`, but stage 1 writes
`<slug>_<timestamp>.jsonl`. screening-adjudication.md says `--input
data/screening-abstracts/verdicts.jsonl`, but stage 3 writes that file
under a `<run-id>/` leaf. extraction-codebook.md says `--eligible
data/fulltext-eligibility/eligible.jsonl`; the actual path has a
`<run-id>/` leaf. interfaces.md is internally inconsistent on the same
question (L62 vs L228 on the run-id leaf). The README's reviewer-export
examples ([`README.md` L165–185](../../README.md)) show the same bare
path bug — they reference `data/run/screening-abstracts/verdicts.jsonl`
without the run-id leaf, even though stage 3 writes under one.
Why it bites: every stage example a copy-paster hits is wrong by one
component, and the README's reviewer-export examples land them in the
same hole one hop later.
Fix sketch: either every example shows the placeholder leaf
(`<run-id>/`) explicitly, or every example uses the resolved-with-leaf
form. Pick one and apply consistently across `interfaces.md`,
`screening-abstracts.md`, `screening-adjudication.md`, `eligibility.md`,
`extraction-codebook.md`, and the README's reviewer-export block. For
stage 1 → 2: have stage 1's default output be
`data/catalogue-fetch/catalogue.jsonl` (matching the wrapper) or have
catalogue-dedup.md show `cat data/catalogue-fetch/*.jsonl >
combined.jsonl` as a literal preliminary step in the example.

#### P5 — Ollama `num_ctx` story is split across three docs and is silent on local runs

> Extends [O3](#o3----nbsp_num_ctx--32768-is-hardcoded-with-no-cli-flag) below — the source-level inventory and the proposed CLI fix.

Where it bites: a local user pulls `gemma3:4b` per
[`screening-abstracts.md` L13](../screening-abstracts.md), runs stage 7
or 8 with the upstream model's small default `num_ctx`, and silently
gets truncated prompts.
Why it bites: the `Modelfile` `PARAMETER num_ctx` requirement is only
in [`scripts/nesh-pipeline.sbatch`](../../scripts/nesh-pipeline.sbatch)
and (briefly) in README L107–119 under "On NESH". The per-stage docs do
not mention `num_ctx` at all. Local users have no analogous setup
described.
Fix sketch: add a short "Sizing the Ollama runner context window"
subsection to [`docs/eligibility.md`](../eligibility.md) and
[`docs/extraction-codebook.md`](../extraction-codebook.md) showing the
two-line `Modelfile` and `ollama create laglit-extract -f Modelfile`.
Or: have the stage tool itself emit a Modelfile at startup with the
required `num_ctx`, removing the user-side ceremony.

#### P6 — `OPENALEX_API_KEY` and `UNPAYWALL_EMAIL` are env-var-then-flag pass-through, but the runtime doesn't enforce a clear failure mode

Where it bites: the docs say "scripts source `.env` and pass `--api-key
$OPENALEX_API_KEY`" (e.g.
[`catalogue-fetch.md` L42](../catalogue-fetch.md)). For a user running
manually, this means they have to either source `.env` themselves or
substitute the literal key on the command line. README L11–12 says "fill
in OPENALEX_API_KEY and UNPAYWALL_EMAIL in `.env`" — but for a manual
user, putting them in `.env` does nothing unless they `set -a; source
.env; set +a` themselves. README does not show this.
Why it bites: a user who fills in `.env`, runs `laglitsynth
catalogue-fetch …` directly, and gets `argparse: --api-key required`
will be confused — the README told them to fill in `.env`, and now the
CLI is asking for a flag.
Fix sketch: add a one-line note under README's Setup ("`.env` is read
by the wrappers; if you invoke stages by hand, run `set -a; source
.env; set +a` first").

#### P7 — `laglit-{screen,eligibility,extract}` model tags exist only on NESH; locally the user is on their own to bake `num_ctx`

> Extends [O3](#o3----nbsp_num_ctx--32768-is-hardcoded-with-no-cli-flag) and [A6](#a6----ship-laglitsynth-bake-model-or-equivalent-so-locals-get-parity).

Where it bites: continues from P5. The wrapper synthesises three
runtime-only Ollama tags (`laglit-screen`, `laglit-eligibility`,
`laglit-extract`) that exist only inside that sbatch run. A user running
locally and pointing the CLI at `--model gemma3:4b` gets no `num_ctx`
sizing.
Fix sketch: ship a `scripts/local-bake-model.sh` (or
`laglitsynth bake-model --tag laglit-extract --base llama3.1:8b
--num-ctx 32768`) so the local manual workflow has a documented
analog.

#### P8 — `--screening-threshold` lives on multiple stages but isn't a single concept

Stage 3 takes it (and writes the threshold into
`screening-meta.json`); stage 4 also takes it. Per the
"flag, don't filter" design, the threshold is meant to be a stage-4
concern (since stage 3 just emits scores). But the stage-3 CLI takes
`--screening-threshold` too, and the README and wrapper both pass it
to stage 3 (or skip it entirely on stage 4 and rely on default). A
manual user has to choose where to set it.
Fix sketch: clarify in `screening-abstracts.md` what stage 3 does with
the threshold (purely informational? counts above/below?), and what the
authoritative downstream gate is (stage 4).

#### P9 — `interfaces.md`'s "End-to-end sequence" includes stages 9–12 that don't exist

Where it bites: a manual user reading L338–358 will run those commands
and see "unknown subcommand." The doc says at L162 "planned subcommands"
but the End-to-end block doesn't re-flag them.
Fix sketch: add a "STOP HERE for now — stages 9–12 are not yet
implemented" comment between stages 8 and 9 in the canonical sequence.

#### P10 — README's `## Tools` section lacks stage numbers and trails a stale "Stage 9+ specified…" paragraph

Where it bites: a reader cross-referencing the README against
[`docs/pipeline.md`](../pipeline.md) and
[`docs/interfaces.md`](../interfaces.md) — both of which use
`Stage 1 — …` … `Stage 12 — …` form throughout — has to do the
renumbering by hand. The README enumerates the eight implemented tools
without a leading `1.` … `8.`, so a section anchor like "Stage 4" in
the per-stage docs has no visual landing point in the README. The
trailing "Stage 9+ are specified in pipeline.md but not yet
implemented" sentence is a separate paragraph below the list, which
visually reads as a footnote rather than the boundary it actually is.
Why it bites: low blocker, but every cross-doc reference forces a
mental conversion. Combined with P3 (no manual runbook in README),
this is the second cue that the README isn't keeping pace with the
docs.
Fix sketch: number the eight implemented tools `1.` through `8.` and
fold the "Stages 9–12" sentence into the section's lead paragraph
(one statement of the implemented / not-yet-implemented split,
mentioning all twelve stages by name). This pairs naturally with the
P3 fix, since both are about the README's `## Tools` and `## Running`
sections.

### Ollama setup

> The findings below complement P5 and P7. Where there's overlap, the
> P-finding is the high-level pain point; the O-finding gives the
> concrete file/line evidence and the proposed CLI/config fix.

#### O1 — "Install Ollama" is not documented anywhere

What a user sees: [`README.md` L208–230](../../README.md) has an
"Ollama (for LLM stages)" section that opens with `ollama serve` /
`ollama pull gemma3:4b` as if the binary is already on their PATH.
[`docs/screening-abstracts.md` L8–22](../screening-abstracts.md) does
the same. There is no link to <https://ollama.com/download>, no
`brew install ollama` line, no apt-get-style hint for Linux, no
mention of the bundled CUDA tarball requirement that
[`nesh-ollama.md` L67–90](nesh-ollama.md) explains for NESH.

Why it bites: the assumption "the reader has already installed Ollama"
is silently load-bearing. A first-time user follows the `ollama serve`
line, gets `command not found`, and has to leave the project docs to
find install instructions. Worse on a Linux GPU box: the
[lessons learned in nesh-ollama.md L78–90](nesh-ollama.md) — that
extracting only `bin/ollama` makes Ollama silently fall back to CPU —
apply equally to a non-NESH manual install, but they live in an
explorations file the user has no reason to read.

One-line fix: add three lines to [`README.md` L215](../../README.md)
right before `ollama serve`:
`# Linux: curl -fsSL https://ollama.com/install.sh | sh  (use the full installer, not just the binary)` /
`# macOS: brew install --cask ollama  (or download the .app from ollama.com/download)`.

#### O2 — `OLLAMA_NUM_PARALLEL` "must be set before serve" is fatal-on-misuse but only stated in one doc

What a user sees: a manual user wanting throughput reads
[`README.md` L136–140](../../README.md), which says
"`OLLAMA_NUM_PARALLEL=2` (and the derived `LLM_CONCURRENCY=4`) is the
documented production default — see [llm-concurrency.md]." They click
through to [`docs/llm-concurrency.md` L25–32](../llm-concurrency.md)
to find: *"Read by Ollama at `ollama serve` start time (it is **not**
honoured if exported afterwards)."* That's the only mention of the
ordering rule in the entire docs tree.

Why it bites: the natural pattern for someone debugging throughput is
to leave `ollama serve` running in another terminal and `export
OLLAMA_NUM_PARALLEL=4` in the shell where they kick off the stage —
which silently keeps the server's `=1` default. They will see no
warning, no error, just unchanged throughput. The README's "Setup"
block (L208–230) and screening-abstracts.md's "Prerequisites" block
(L8–22) — the two places a manual user actually starts Ollama — say
nothing about ordering.

One-line fix: in [`README.md` L215](../../README.md), change the
`ollama serve` line to
`OLLAMA_NUM_PARALLEL=2 ollama serve   # set before serve; export after start is silently ignored`.
Same change in [`docs/screening-abstracts.md` L13](../screening-abstracts.md).

#### O3 — `_NUM_CTX = 32768` is hardcoded with no CLI flag

What a user sees: [`docs/eligibility.md` L178](../eligibility.md) and
[`docs/extraction-codebook.md` L205](../extraction-codebook.md) show
`[--model gemma3:4b] [--base-url http://localhost:11434]` as the only
LLM-related flags. There is no `--num-ctx` and no `--concurrency`.
Reading the source confirms this:
[`src/laglitsynth/fulltext_eligibility/eligibility.py` L54](../../src/laglitsynth/fulltext_eligibility/eligibility.py)
and
[`src/laglitsynth/extraction_codebook/extract.py` L61](../../src/laglitsynth/extraction_codebook/extract.py)
both set `_NUM_CTX = 32768` as a module constant; the value is sent
via `extra_body={"options": {"num_ctx": 32768}}` per call. The README
table at L110–114 documents `SCREENING_NUM_CTX=8192`,
`ELIGIBILITY_NUM_CTX=32768`, `EXTRACTION_NUM_CTX=32768` as overridable
**only via the sbatch wrapper**, because the wrapper bakes them into
synthesised model tags via `Modelfile` `PARAMETER num_ctx`. A manual
user has no analogous knob.

Why it bites: an `extra_body` `num_ctx` hint is documented (in
[`README.md` L106–107](../../README.md)) as not reliable — only the
Modelfile bake works. A user on a 24 GB consumer GPU running stage 8
on `llama3.1:8b` will hit a KV-cache OOM at 32 k context and have no
easy way to drop to 16 k or 8 k without editing source. The source's
own [comment at extraction_codebook/prompts.py L18](../../src/laglitsynth/extraction_codebook/prompts.py)
("Sized to fit the run-time num_ctx — default EXTRACTION_NUM_CTX=32768")
implies an env var that doesn't exist outside the wrapper.

One-line fix: add `--num-ctx N` to stages 7 and 8 (with `_NUM_CTX` as
default), and document it as the local-equivalent of NESH's
`ELIGIBILITY_NUM_CTX` / `EXTRACTION_NUM_CTX`. Stop-gap if that's
out of scope: add a "VRAM tuning for local users" subsection to
[`docs/eligibility.md`](../eligibility.md) and
[`docs/extraction-codebook.md`](../extraction-codebook.md) showing the
same Modelfile bake + `--model laglit-extract` recipe the sbatch uses
(this also resolves P5/P7).

#### O4 — Stage 8 needs `gemma3:12b` or larger, but no doc tells a local user how to pull it before running

What a user sees: [`docs/extraction-codebook.md` L251–256](../extraction-codebook.md)
admits gemma3:4b returns `{}` on full-text inputs and says "Pass a
bigger model via `--model`." [`README.md` L220–229](../../README.md)
notes the same and shows `ollama pull llama3.1:8b`.
[`docs/screening-abstracts.md` L17–18](../screening-abstracts.md)
suggests "`gemma3:12b` or `llama3.2`" as alternatives to gemma3:4b
without mentioning `ollama pull <name>` for those tags — the user has
to extrapolate from the gemma3:4b pull line.

Why it bites: a postdoc following [`docs/extraction-codebook.md`](../extraction-codebook.md)
linearly — the per-stage doc is supposed to be self-contained per the
project's "tools each do one thing" principle — never lands on the
README's pull line. They run `--model llama3.1:8b` blind, get
"model not found," and have to grep for the pull command.

One-line fix: in [`docs/extraction-codebook.md` L256](../extraction-codebook.md),
extend the "Pass a bigger model" sentence to
"Pass a bigger model via `--model`; pull it first with
`ollama pull llama3.1:8b` (or `gemma3:12b`, `qwen2.5:14b`)."

#### O5 — `--concurrency` is silently absent on stages 7 and 8

What a user sees: a manual user reading
[`docs/llm-concurrency.md` L84–98](../llm-concurrency.md) learns that
"only `screening-abstracts` (stage 3) currently has a `--concurrency`
flag." The per-stage docs for 7 and 8
([`docs/eligibility.md` L168–179](../eligibility.md),
[`docs/extraction-codebook.md` L196–206](../extraction-codebook.md))
do not mention this — their CLI sketches just show `[--model] [--base-url]`.
A user copying the wrapper's `LLM_CONCURRENCY=4` pattern will find no
flag to pass it to.

Why it bites: it's not a runtime error — the flag just doesn't exist.
A user who paid for `OLLAMA_NUM_PARALLEL=4` worth of GPU expects
client-side parallelism on stages 7 and 8 too and has no in-doc
signal that those stages are sequential.

One-line fix: in
[`docs/eligibility.md` L168–179](../eligibility.md) and
[`docs/extraction-codebook.md` L196–206](../extraction-codebook.md),
add a one-liner under "Arguments": "**No `--concurrency` flag.**
Stage 7/8 calls Ollama sequentially regardless of
`OLLAMA_NUM_PARALLEL`. See [llm-concurrency.md](llm-concurrency.md)."

#### O6 — SSH-tunnel guidance for remote Ollama is one sentence with no command

What a user sees: [`docs/screening-abstracts.md` L20–21](../screening-abstracts.md)
says: *"If tunneling to a remote GPU machine, expose the port locally
and pass `--base-url http://localhost:<port>`."* No `ssh -L` example,
no link to [`nesh-ollama.md`](nesh-ollama.md) where the chained
`ssh -A -L 11434:localhost:11434 user@host` and reverse-tunnel
patterns are worked out. The eligibility and extraction-codebook docs
don't even have this much.

Why it bites: a user who has read about NESH in the README will
naturally try `--base-url` against a tunnel. The two-hop / reverse-tunnel
trick from [`nesh-ollama.md` L33–55](nesh-ollama.md) is the only
working pattern for NESH login → compute, but a user reading
[`docs/screening-abstracts.md`](../screening-abstracts.md) gets none
of that and won't know to look in `explorations/`.

One-line fix: replace the L20–21 sentence with: *"For a remote Ollama,
forward the port and point `--base-url http://localhost:<port>` at it.
For NESH specifically, see
[`nesh-ollama.md`](explorations/nesh-ollama.md) for the
ssh-L + reverse-ssh-R pattern that works through the login-node load
balancer."*

#### O7 — Determinism story (`temperature=0.8`, per-call `seed`, `prompt_sha256`) lives in the data-model docs, not next to the user setting up the LLM

What a user sees: a user staring at
[`docs/screening-abstracts.md` L131–133](../screening-abstracts.md)
sees `LlmMeta`'s `temperature: 0.8` and `prompt_sha256` documented as
output fields. Nothing connects: "we sample at temperature 0.8 with a
random per-call seed, so two runs of the same prompt over the same
input produce different verdicts; `prompt_sha256` does not include
the seed because the seed is per-call, not per-run; if you want
'rerun with the same configuration' that means same prompt, not same
verdicts." The configs.md hint at L43–46 ("LLM stochasticity makes
record-level reproduction impossible at temperature=0.8") is the only
explicit statement and lives in a doc primarily about config files.

Why it bites: a user comparing two `verdicts.jsonl` files and seeing
different `relevance_score`s on the same `work_id` will assume
something is broken. They won't naturally read `configs.md` for that
question.

One-line fix: add a "Reproducibility" subsection to
[`docs/screening-abstracts.md`](../screening-abstracts.md) (one
paragraph) and reference it from
[`docs/eligibility.md`](../eligibility.md) and
[`docs/extraction-codebook.md`](../extraction-codebook.md). Make the
non-determinism explicit and pin the rationale (the LLM is doing free-text
justification; we want some sampling diversity).

#### O8 — Preflight error message points at one fix only

What a user sees: when Ollama isn't reachable,
[`src/laglitsynth/screening_abstracts/screen.py` L199–206](../../src/laglitsynth/screening_abstracts/screen.py)
raises *"Cannot reach Ollama at {base_url}. Is `ollama serve` running?"*
That's the entire diagnostic. No mention of `--base-url`, of the
tunnel, of the model not being pulled (`models.retrieve(args.model)`
also fails with the same message — see L201–203). Compare to
[`fulltext_extraction/extract.py` L117–121](../../src/laglitsynth/fulltext_extraction/extract.py),
which prints the literal `docker run` line.

Why it bites: a user with the wrong tunnel port or a model they
forgot to pull gets the same error as a user who didn't start Ollama.

One-line fix: split the preflight checks: report "Ollama responds but
model `{args.model}` is not pulled — run `ollama pull {args.model}`"
distinctly from "Ollama unreachable at {base_url} — start it with
`ollama serve` or check your `--base-url` / SSH tunnel."

### GROBID setup

#### G1 — The canonical `docker run` is in the README footer, not in the per-stage doc

What a user sees: a user who navigates straight from
[`README.md` L42–44](../../README.md) ("`fulltext-extraction` — parse
retrieved PDFs into structured section text via GROBID. See
[docs/fulltext-extraction.md](docs/fulltext-extraction.md)") to the
per-stage doc lands on
[`docs/fulltext-extraction.md`](../fulltext-extraction.md). That doc
expects GROBID at `http://localhost:8070` (L195) and the operational
details at L29–39 list image, port, memory — but **never the actual
`docker run` command**. The `--grobid-url` argument description at
L204 says *"GROBID API endpoint. Required — there is no non-GROBID
path for now"* with no command. The user has to flip back to
[`README.md` L237`](../../README.md) (or into
[`docs/grobid-lessons.md`](../grobid-lessons.md)) to find the
invocation.

Why it bites: per-stage docs are supposed to be self-contained. A
user reading "fulltext-extraction.md" linearly will know GROBID needs
to be at port 8070 and be a Docker container, but won't have the
exact `docker run` syntax.

One-line fix: add a "Prerequisites" block at the top of
[`docs/fulltext-extraction.md`](../fulltext-extraction.md) (mirroring
the screening-abstracts.md pattern at L8–22) with:
`docker run --rm -p 8070:8070 lfoppiano/grobid:0.8.0` and a one-line
"wait 30–60 s for startup."

#### G2 — Apple Silicon / Rosetta caveat is only in `grobid-lessons.md`

What a user sees: a Mac user reading
[`README.md` L237`](../../README.md) ("`docker run --rm -p 8070:8070
lfoppiano/grobid:0.8.0`") gets no warning. The Rosetta caveat lives at
[`docs/grobid-lessons.md` L46`](../grobid-lessons.md) ("The Docker
image is amd64-only. On Apple Silicon it runs under Rosetta emulation
— functional but slower") and is repeated in the operational details
of [`docs/fulltext-extraction.md` L31`](../fulltext-extraction.md).
But the README — the place where a Mac user copy-pastes the docker
run command — never mentions it.

Why it bites: Docker Desktop on Apple Silicon will print an arch-mismatch
warning and may refuse to run depending on Rosetta-for-Linux setup.
A user without Rosetta-for-Linux enabled will see a cryptic
`exec format error` or extremely slow extraction (hours instead of
minutes per paper).

One-line fix: in [`README.md` L237`](../../README.md), append a
trailing comment to the docker run line:
`# Apple Silicon: image is amd64-only; install Rosetta for Linux first (`softwareupdate --install-rosetta`).`

#### G3 — macOS Docker `localhost:8070` blocked workaround is investigative-only

What a user sees: [`docs/grobid-lessons.md` L54–69](../grobid-lessons.md)
documents that on macOS, "direct host calls to `localhost:8070` were
blocked (likely sandboxing on macOS)" and gives a `docker run --network
container:litrag-grobid …` workaround. This is the only mention. It
lives in a file branded "Notes from a prior prototype" — the project's
own [AGENTS.md](../../AGENTS.md) classifies `grobid-lessons.md`-style
docs as "investigative write-ups, not authoritative references."

Why it bites: a Mac user runs the README's docker invocation, the
container says "GROBID is running," the per-stage CLI's preflight
fails with "GROBID is not running at http://localhost:8070" (per
[`fulltext_extraction/extract.py` L117–121](../../src/laglitsynth/fulltext_extraction/extract.py)).
They have no signal that the issue is host-to-container connectivity
on macOS Docker, not GROBID itself. They debug for an hour.

One-line fix: pull the workaround into
[`docs/fulltext-extraction.md`](../fulltext-extraction.md) under a
"macOS troubleshooting" subsection. The signal must be visible
**before** the user hits the symptom — even if just a one-line
"on macOS, if `curl localhost:8070/api/isalive` from the host hangs,
see [grobid-lessons.md](grobid-lessons.md) for the network-namespace
workaround."

#### G4 — `consolidateCitations=0` rationale is buried; user has no reason to know it's settable

What a user sees: [`docs/fulltext-extraction.md` L37–39](../fulltext-extraction.md)
says: *"`consolidateCitations=0` (default off). We already have references
from OpenAlex."* Period. No CLI flag, no env var, no link.
[`docs/grobid-lessons.md` L73–76](../grobid-lessons.md) explains it
calls Crossref/Semantic Scholar APIs and slows ingest. A user looking
to see "are my GROBID extractions missing DOIs" might naturally try
to flip this on.

Why it bites: this is low severity — the default is correct and the
user usually doesn't need to know — but the asymmetry with how other
flags are documented is a small surprise, and the wording ("default
off") implies it's controllable from somewhere.

One-line fix: in
[`docs/fulltext-extraction.md` L37–39](../fulltext-extraction.md),
either say "hardcoded off; flip in `extract.py` if you want it on"
or actually expose `--consolidate-citations` as a flag.

#### G5 — Memory footprint, mid-corpus crash recovery: no runbook

What a user sees: [`docs/fulltext-extraction.md` L34–35](../fulltext-extraction.md)
says "Memory footprint: ~2 GB. Timeout: 120 seconds per paper." Nothing
about what to do if GROBID OOMs or crashes mid-corpus. The
`--skip-existing` flag exists (L206–207) and would let the user
resume after a restart — but the doc doesn't connect those dots:
"if GROBID crashed at PDF 47 of 200, re-run with `--skip-existing`
after restarting the container."

Why it bites: GROBID under Rosetta on a memory-constrained machine
**will** OOM eventually. A user without an obvious recovery path will
re-run from scratch and pay the time penalty.

One-line fix: add an "If GROBID crashes mid-corpus" block to
[`docs/fulltext-extraction.md`](../fulltext-extraction.md) explicitly
naming `--skip-existing` as the recovery path, and bumping the memory
recommendation (Docker Desktop default is 2 GB; GROBID itself wants
~2 GB; recommend 6–8 GB to allow OS overhead and the JVM heap headroom
documented in [`nesh-grobid.md` L125](nesh-grobid.md): "GROBID wants
~4–8 GB JVM heap").

#### G6 — Manual NESH user has nowhere to go

What a user sees: a postdoc who wants to run on a NESH compute node
manually (i.e. write their own sbatch script, not use
`nesh-pipeline.sbatch`) needs the SIF path, bind mounts, `--pwd`
flag, and login-node proxy address. All of that lives in
[`docs/explorations/nesh-grobid.md`](nesh-grobid.md) (an
exploration doc) and in [`scripts/nesh-pipeline.sbatch`](../../scripts/nesh-pipeline.sbatch)
itself. There is no "how to run GROBID on NESH manually" page in
`docs/`.

Why it bites: AGENTS.md de-canonicalises explorations
("investigative write-ups, not authoritative references"). A reader
following the project's own doc-classification rules has no
authoritative reference to follow.

One-line fix: promote the working invocation block from
[`docs/explorations/nesh-grobid.md` L97–121](nesh-grobid.md) into
[`docs/fulltext-extraction.md`](../fulltext-extraction.md) under a
"Running on NESH (manually)" subsection. Same for
[`docs/explorations/nesh-ollama.md`](nesh-ollama.md) into
[`docs/screening-abstracts.md`](../screening-abstracts.md).

## 3. What `scripts/` is hiding

Each line marked with one of:

- **CLI/docs** — should not require a script; belongs in the CLI or
  docs.
- **Wrapper** — legitimately a wrapper concern, ergonomics or
  cluster-specific.

| Hidden by wrapper | Where | Verdict | Note |
|---|---|---|---|
| Source `.env` and pass values as `--flag` | `run-pipeline.sh` L33–37 | **CLI/docs** | At minimum, README must show users how to source `.env`. Better: have the CLI accept env-var fallback for `--api-key`/`--email` (or a `--env-file` flag) so manual users get the same thing as wrapper users. |
| Generate one `RUN_ID` and thread through stages 3, 7, 8 | `run-pipeline.sh` L54 | **CLI/docs** | The `generate-run-id` subcommand exists but is undocumented. Document it. Also: print the run-id to stderr at end of each stage so a user can pick it up for the next. |
| Hard-coded screening prompt | `run-pipeline.sh` L88 | **CLI/docs** | This is a CLI arg; the example in `screening-abstracts.md` should match. |
| Concatenate per-search files into `combined.jsonl` | (implicit; wrapper writes `catalogue.jsonl` directly) | **CLI/docs** | Either dedup should accept multiple `--input` flags / globs, or the catalogue-dedup doc needs a literal `cat ... > combined.jsonl` step. |
| Skip stages by `STOP_AFTER_STAGE` | `run-pipeline.sh` L49, L66–72 | **Wrapper** | Genuinely a multi-stage concern; the CLI is per-stage. |
| Validate `OPENALEX_API_KEY` / `UNPAYWALL_EMAIL` exist in env | `run-pipeline.sh` L61–62 | **Wrapper** | Reasonable preflight. |
| Choose default models per stage | `run-pipeline.sh` L45–47 | **CLI/docs** | The CLI defaults disagree with the wrapper defaults (gemma3:4b vs llama3.1:8b for stage 8). Fix the CLI default. |
| Default `LLM_CONCURRENCY=1` (local) vs `OLLAMA_NUM_PARALLEL+2` (NESH) | `run-pipeline.sh` L48; sbatch L117–118 | **Wrapper** | Cluster-vs-local tuning; legitimate wrapper concern. |
| Pull and start a Singularity GROBID container, bind-mount writable scratch dirs | `nesh-pipeline.sbatch` L88–110 | **Wrapper (cluster-specific)** | Mostly belongs in the wrapper, BUT the bind-mount workaround for read-only `/opt/grobid/grobid-home/tmp` should be documented as canon in `docs/explorations/nesh-grobid.md` or promoted into `docs/fulltext-extraction.md`. |
| Set NESH compute-node proxy `http_proxy=http://10.0.7.235:3128` | `nesh-pipeline.sbatch` L51–53 | **Wrapper (cluster-specific)** | Documented in [`nesh-ollama.md`](nesh-ollama.md). Fine. |
| Module-load `gcc12-env`, `singularity` | `nesh-pipeline.sbatch` L85–86 | **Wrapper (cluster-specific)** | Fine. |
| Start bundled Ollama at `$OLLAMA_DIR/bin/ollama serve` | `nesh-pipeline.sbatch` L121–122 | **Wrapper (cluster-specific)** | Fine, but the per-user prereq (full tarball under `$HOME/.local/share/ollama-install`) must be documented before the user opens the script. README L121–127 does this; OK. |
| Set `OLLAMA_NUM_PARALLEL` **before** starting Ollama | sbatch L117–122 | **CLI/docs** | The "must be before serve" rule is in `llm-concurrency.md` L26–28 but not in any "running locally" doc. A local user trying overbooking will silently get the default `1`. |
| Wait-for-ready loops (Ollama 30 s, GROBID 90 s) | sbatch L130–156 | **Wrapper** | Fine. |
| Pull upstream models via proxy | sbatch L161–164 | **Wrapper (cluster-specific)** | Fine. |
| Synthesise per-stage Ollama tags (`laglit-screen` etc.) with baked `num_ctx` | sbatch L175–197 | **CLI/docs** | This is the biggest hidden land mine. A local user gets the upstream model's default `num_ctx` (often 2048) and silently truncates full-text prompts. Either make stage 7 / 8 do this themselves at startup, or document the local equivalent in their per-stage docs. |
| Flip `*_MODEL` exports from upstream tags to local synthesised tags | sbatch L194–197 | **Wrapper** | Plumbing for the above. |

## 4. Architecture implications

### A1 — The flag-pattern repeated five times argues for a config file

Every stage from 3 onward takes some subset of:
`--data-dir`, `--run-id`, `--config`, `--model`, `--base-url`,
`--max-records`, `--dry-run`. The wrapper builds these strings up
manually. A user running by hand has to reconstruct the same set of
flags for each of three (or six) stages. The codebase already has the
infrastructure (`--config foo.yaml` per
[configs.md](../configs.md)) but the existing config files are
**per-stage**. A pipeline-level config (`laglitsynth pipeline-config.yaml`
with `[stage-1]`, `[stage-3]`, `[stage-7]` sections, each holding the
same kind of YAML each stage's per-stage config holds) would let a user
write one file and run

```
for s in catalogue-fetch catalogue-dedup screening-abstracts ...; do
    laglitsynth $s --config pipeline-config.yaml
done
```

without re-typing flags. The wrapper would become a 10-line driver
over that config.

### A2 — Run-id propagation is a pipeline-level concern, not a per-stage one

The single biggest source of manual-run pain (P1, P4) is that stages
3, 7, 8 each generate their own run-id leaf, but every downstream
consumer needs the one upstream chose. The wrapper hides this by
generating `RUN_ID` once and threading it through. Architectural fix:
either (a) elevate the run-id to a pipeline-level concept (a single
`pipeline-run-id` shared across all run-id-aware stages, materialised
by `laglitsynth pipeline-init` or similar), or (b) drop run-id leaves
entirely and treat the latest run as the default (with `--run-id` as
opt-in for sweeps).

### A3 — A `laglitsynth pipeline-up` command for the LLM/GROBID preflight

Three of the eight stages (3, 7, 8) require Ollama at a specific
`base-url`; one (6) requires GROBID at a specific URL. Both have
non-trivial setup: pull a model, optionally bake a Modelfile with
`num_ctx`, wait for readiness. The wrapper does exactly this on NESH
(sbatch L88–164). A user running locally gets only `ollama serve` and
`docker run lfoppiano/grobid:0.8.0` from the README — no `num_ctx`
baking, no readiness wait. A `laglitsynth pipeline-up
[--screening-num-ctx N] [--extraction-model M] [--grobid-image IMG]`
command would (a) start the right things, (b) bake the right
Modelfiles, (c) block until both are ready, and (d) print the URLs to
plug into the stage flags. Local users get parity with the wrapper;
the wrapper itself becomes a slurm-aware variant.

### A4 — `.env` is a wrapper-only convention; the CLI ignores it

Per [`docs/interfaces.md` L236–246](../interfaces.md), this is by
design: "tools do not read `.env` … [the wrapper] passes the values as
`--flag "$VAR"` … there is no env-var fallback path that could silently
override what the user typed." That's a defensible call against silent
override. But it leaves manual users with two choices: source `.env`
themselves before each stage, or read keys out of `.env` and paste
into `--api-key` flags. Compromise: read `.env` only when the user
hasn't passed the flag, and emit `loaded OPENALEX_API_KEY from .env`
to stderr. The "no silent override" property is preserved (an explicit
flag still wins) and the wrapper ceremony is no longer load-bearing.

### A5 — `examples/` is canonical input data but isn't framed as such

The codebook YAML and eligibility-criteria YAML under
[`examples/`](../../examples/) are the **only** copies; they aren't
"examples" in the "here's how you'd write your own" sense, they are
the production defaults. A user reading "examples/codebooks/..." in
README L46 might assume it's optional sample data and that they should
write their own. Either rename to something like `defaults/codebooks/`
and `defaults/eligibility-criteria/`, or add a top-of-`examples/README.md`
that says "these are the shipped defaults; modifying them is the
documented way to retarget the pipeline."

### A6 — Ship `laglitsynth bake-model` (or equivalent) so locals get parity

> Extends [A3](#a3----a-laglitsynth-pipeline-up-command-for-the-llmgrobid-preflight); subsumes [P7](#p7----laglit-screeneligibilityextract-model-tags-exist-only-on-nesh-locally-the-user-is-on-their-own-to-bake-num_ctx).

The NESH wrapper synthesises `laglit-screen` / `laglit-eligibility` /
`laglit-extract` tags via Modelfile heredocs ([nesh-pipeline.sbatch
L175–192](../../scripts/nesh-pipeline.sbatch)) so each stage gets the
right `num_ctx` baked in. A local manual user has no analog: there is
no CLI subcommand to do the same thing, and the per-stage docs do not
explain the Modelfile dance (per O3 above). Fix: add a
`laglitsynth bake-model --tag laglit-extract --base llama3.1:8b
--num-ctx 32768` subcommand that emits the Modelfile, runs
`ollama create`, and prints the resulting tag. Document it in the
per-stage doc that needs it. The NESH wrapper becomes a 3-line caller
of that subcommand.

### A7 — Centralise external-service troubleshooting

> Extends [A3](#a3----a-laglitsynth-pipeline-up-command-for-the-llmgrobid-preflight).

Right now Ollama troubleshooting is split across
[`screening-abstracts.md`](../screening-abstracts.md) (one sentence
on tunnels, no errors documented),
[`llm-concurrency.md`](../llm-concurrency.md) (the
`OLLAMA_NUM_PARALLEL` ordering rule and the VRAM/KV-cache trade-off
buried mid-doc), [`nesh-ollama.md`](nesh-ollama.md) (install lessons,
GPU-discovery failure, proxy address, reverse-tunnel pattern), and
the source's preflight error string (single-cause). GROBID is split
across [`fulltext-extraction.md`](../fulltext-extraction.md) (some
operational details, no `docker run`), [`grobid-lessons.md`](../grobid-lessons.md)
(the macOS workaround, the 30-60 s startup, the
`consolidateCitations` rationale), [`nesh-grobid.md`](nesh-grobid.md)
(Singularity, bind mounts, `--pwd`), and the README footer (the
canonical `docker run`). A single
[`docs/external-services.md`](../external-services.md) — "everything
you need to stand up Ollama and GROBID, with the troubleshooting
runbook" — would let each per-stage doc reduce its prerequisites
block to one link instead of one inconsistent prose paragraph. The
exploration docs stay where they are (investigative); the canonical
runbook lives in `docs/`.

## 5. Recommended next concrete commit

**Write [`docs/external-services.md`](../external-services.md) — the
single Ollama + GROBID setup runbook — alongside the three per-stage
path-bug fixes.**

The new doc covers, in order, as a single sequenceable runbook: install
Ollama and GROBID, start both, set `OLLAMA_NUM_PARALLEL` before
`ollama serve`, pull each LLM model the pipeline needs, optionally bake
Modelfiles for the right `num_ctx`, verify both are healthy with
`curl`, and the macOS GROBID `localhost` workaround inline. Each
per-stage prerequisites block in stages 3, 6, 7, 8 reduces to "see
[external-services.md](external-services.md)."

The same commit fixes the path bugs identified above:

- [`docs/extraction-codebook.md` L196–206](../extraction-codebook.md)
  — show `--eligible data/fulltext-eligibility/<run-id>/eligible.jsonl
  --model llama3.1:8b` (P2).
- [`docs/screening-adjudication.md` L26–31](../screening-adjudication.md)
  — show `--input data/screening-abstracts/<run-id>/verdicts.jsonl`
  (P4).
- [`docs/catalogue-dedup.md` L78](../catalogue-dedup.md) — drop
  `combined.jsonl` (use `<slug>_<timestamp>.jsonl` to match stage 1's
  actual output) or add a one-line `cat ... > combined.jsonl`
  precondition above the example (P4).

This commit attacks P1 (partially, via the runbook), P2, P3 (the
runbook is the manual sequence reference), P4, P5/P7 (subsumed via
the Modelfile section of the runbook), P10 (the runbook defines the
canonical stage-numbered flow the README will eventually mirror), O1,
O2, O4, O6, O8, G1, G2, G3, G5, G6 — fifteen of the twenty-five
findings — without any code changes. The architectural shifts
in section 4 (especially A2, A3, A6) are the right next round; this
commit is the cheapest dent in onboarding pain.

A subsequent commit should pick one of A2 / A3 to start the
code-side fix: A2 (run-id elevation) is the highest-leverage
single change because it dissolves P1 and P4 in one move; A3 (a
`pipeline-up` subcommand) is the more strategic one because it
also subsumes A6, but is a larger change.
