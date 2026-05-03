# Usability — CLI surface improvements

## Goal

Close the code-side gaps a manual user hits running stages 3, 6, 7, 8
without the wrappers. The
[usability review](../docs/explorations/usability-review.md) found that
several knobs the wrapper threads silently are not reachable from the
CLI at all (`--num-ctx`, `--concurrency` on stages 7/8), some defaults
are documented as broken (stage 8's `--model gemma3:4b`), and the
`.env` story leaves manual users guessing. Doc fixes for the same
findings live in [`usability-docs.md`](usability-docs.md); this plan
covers code changes only.

This plan has **open questions** that need answers before commits 4–7
can land. Commits 1–3 are unblocked; the rest require a decision call
on subcommand surface area.

## Non-goals

- Documentation changes — covered by
  [`usability-docs.md`](usability-docs.md). The CLI changes here
  invalidate some doc text; that doc lands its own re-edits.
- Pipeline-level config file
  ([A1](../docs/explorations/usability-review.md)). The threshold-on-
  three-stages problem persists after this plan; A1 is the structural
  fix and is its own plan.
- The XLSX export
  ([P3 in usability-review.md](../docs/explorations/usability-review.md))
  — kept as-is per the project owner's call.
- Reviewer-export workflow rewire — out of scope.
- Anything the [`verdicts-only-cutover.md`](verdicts-only-cutover.md)
  plan already does (stage 4 deletion, stages 5/7/8 input rewire,
  run-id stderr print).

## Target state

### Stage CLI surface

Each numbered item below is one finding. Items 1–3 are unconditional;
4–7 hinge on the open questions in section
[Open questions](#open-questions).

#### 1. `--num-ctx N` on stages 7 and 8

[O3](../docs/explorations/usability-review.md). Both stages currently
hardcode `_NUM_CTX = 32768` as a module constant
([`fulltext_eligibility/eligibility.py:54`](../src/laglitsynth/fulltext_eligibility/eligibility.py),
[`extraction_codebook/extract.py:61`](../src/laglitsynth/extraction_codebook/extract.py))
and pass it via `extra_body={"options": {"num_ctx": N}}`. The
wrapper's `ELIGIBILITY_NUM_CTX` / `EXTRACTION_NUM_CTX` env vars only
work via the Modelfile bake on the NESH side; locally there's no
knob. Add `--num-ctx N` (default `32768`) to both stages; thread
through to the `extra_body` call. The hint isn't fully reliable
without a baked Modelfile, but exposing it gives local users on
smaller GPUs a way to drop to 16k or 8k without editing source.

The hash recipe at
[`eligibility.py:320`](../src/laglitsynth/fulltext_eligibility/eligibility.py)
and
[`extract.py:354`](../src/laglitsynth/extraction_codebook/extract.py)
already includes `_NUM_CTX` in `prompt_sha256`; the new flag's value
goes in the same place so different `--num-ctx` values produce
different hashes (so `--skip-existing` correctly rejects mixing).

#### 2. Preflight error message split

[O8](../docs/explorations/usability-review.md). The current
preflight at
[`screening_abstracts/screen.py:199`](../src/laglitsynth/screening_abstracts/screen.py),
[`fulltext_eligibility/eligibility.py:212`](../src/laglitsynth/fulltext_eligibility/eligibility.py),
and
[`extraction_codebook/extract.py:248`](../src/laglitsynth/extraction_codebook/extract.py)
collapses three failure modes into one error: "Cannot reach Ollama at
{base_url}. Is `ollama serve` running?" The three failure modes are
(a) Ollama unreachable, (b) Ollama up but model not pulled, (c) wrong
`--base-url` (e.g. SSH tunnel port mismatch).

Refactor the preflight to a shared helper in
[`src/laglitsynth/`](../src/laglitsynth/) that splits the checks:

```
def preflight(*, base_url: str, model: str) -> None:
    try:
        client = OpenAI(base_url=f"{base_url}/v1", api_key="ollama")
    except Exception as exc:
        raise SystemExit(f"Ollama URL invalid: {base_url} — {exc}")
    try:
        client.models.list()
    except Exception:
        raise SystemExit(
            f"Ollama unreachable at {base_url}. Start it with "
            f"`ollama serve` or check your --base-url / SSH tunnel."
        )
    try:
        client.models.retrieve(model)
    except Exception:
        raise SystemExit(
            f"Ollama responds at {base_url} but model {model!r} is "
            f"not pulled. Run `ollama pull {model}` first."
        )
```

Stages 3, 7, 8 import `preflight()` from one place. Closes the
finding in
[2.5 of pass1](../docs/explorations/simplification-pass.md) (shared
`_preflight`) at the same time.

#### 3. Stage 8 default model change

[P2](../docs/explorations/usability-review.md). Stage 8's CLI default
is `gemma3:4b` ([`extract.py:308`](../src/laglitsynth/extraction_codebook/extract.py)),
which the same doc ([extraction-codebook.md L251–256](../docs/extraction-codebook.md))
admits returns empty JSON on full-text inputs. The wrapper overrides
to `llama3.1:8b`; manual users get the broken default.

See [Open questions Q1](#q1-stage-8-default-model) for the choice
between "change the default to `llama3.1:8b`" and "remove the default
and require `--model`."

#### 4. `--concurrency N` on stages 7 and 8

[O5](../docs/explorations/usability-review.md). Stage 3 has it
([`screen.py:255`](../src/laglitsynth/screening_abstracts/screen.py));
stages 7 and 8 do not. Adding the flag means lifting the
`ThreadPoolExecutor` pattern from
[`screen.py:177–196`](../src/laglitsynth/screening_abstracts/screen.py)
into a shared helper.

Note from
[`docs/llm-concurrency.md`](../docs/llm-concurrency.md) L84–98: stage
8 in particular is prefill-bound (~90% of wall time), and Ollama
serialises prefill across requests. Real speedup needs vLLM or
SGLang, not more client threads. Stage 7 is less prefill-heavy and
will benefit more.

See [Open questions Q2](#q2-concurrency-on-stages-7-and-8) for
whether this is worth the surface change given the documented
prefill-bound limitation.

#### 5. `.env` fallback for `--api-key` and `--email`

[P6 / A4](../docs/explorations/usability-review.md). The current
design at
[`docs/interfaces.md`](../docs/interfaces.md) L236–246 explicitly
forbids env-var fallback to preserve "no silent override." But
manual users who follow the README's "fill in `.env`" instruction
get an `argparse: --api-key required` error.

The proposed compromise: read `.env` only when the flag isn't
passed, and emit `loaded OPENALEX_API_KEY from .env` to stderr.
Explicit flag still wins.

See [Open questions Q3](#q3-env-fallback) for whether the compromise
is acceptable or whether the doc fix in
[`usability-docs.md`](usability-docs.md) (showing
`set -a; source .env; set +a`) is the right scope.

#### 6. `bake-model` subcommand

[A6](../docs/explorations/usability-review.md). The NESH wrapper
synthesises `laglit-screen` / `laglit-eligibility` / `laglit-extract`
tags with baked `num_ctx` via Modelfile heredocs
([`scripts/nesh-pipeline.sbatch`](../scripts/nesh-pipeline.sbatch)
L175–192). A local manual user has no analog. Two shapes:

- **As a CLI subcommand**: `laglitsynth bake-model --tag laglit-extract
  --base llama3.1:8b --num-ctx 32768`. Emits a Modelfile, runs `ollama
  create`, prints the tag. ~50 LOC of code + ~30 LOC of tests.
- **As a shell script**: `scripts/bake-model.sh laglit-extract
  llama3.1:8b 32768`. ~20 LOC of bash; doesn't grow the CLI surface.

See [Open questions Q4](#q4-bake-model-shape).

#### 7. `pipeline-up` subcommand

[A3](../docs/explorations/usability-review.md). Idea: `laglitsynth
pipeline-up [--screening-num-ctx N] [--extraction-model M]
[--grobid-image IMG]` starts Ollama, bakes Modelfiles, starts GROBID,
waits for both readiness, prints URLs. The local equivalent of the
NESH sbatch's first 100 lines.

This is a much bigger surface addition than `bake-model` and overlaps
substantially with what the wrapper already does. The honest question:
is the wrapper script already the right answer for "start everything"?

See [Open questions Q5](#q5-pipeline-up).

### Run-id elevation

[A2 / P1](../docs/explorations/usability-review.md). The
[`verdicts-only-cutover.md`](verdicts-only-cutover.md) plan adds a
stderr `Run dir:` print as a tactical fix. The strategic options are:

- (a) Elevate run-id to a pipeline-level concept: a single
  `pipeline-run-id` materialised by `laglitsynth pipeline-init` (or
  derived deterministically from the wrapper's session). Every
  run-id-aware stage takes `--pipeline-run-id` and writes under that
  one leaf. The wrapper threads one value; manual users do too.
- (b) Drop run-id leaves entirely for the non-sweep workflow. Treat
  the latest run as default; `--run-id` becomes opt-in for sweeps.

Both are bigger surface changes than this plan covers. See
[Open questions Q6](#q6-run-id-elevation).

## Implementation sequence

Six to nine commits depending on the open-question answers. Items 1–3
are unconditional and can land in any order; 4–7 each become a commit
if the answer is "yes" and are dropped if "no."

### Unblocked commits

#### 1. Shared `preflight()` helper + error split

Lift the duplicated `_preflight()` from stages 3, 7, 8 into one
module-level helper. Split the single error message into three
specific ones (URL invalid / unreachable / model not pulled) per
finding 2. Tests in `tests/test_preflight.py` (new) covering the
three failure modes. Stages 3, 7, 8 import it. Closes
[2.5 of pass1](../docs/explorations/simplification-pass.md) at the
same time.

#### 2. `--num-ctx N` on stages 7 and 8

Add the flag with default `32768`; thread through to `extra_body`;
fold into `prompt_sha256` so a `--skip-existing` rerun with a
different `--num-ctx` correctly aborts.
[`tests/test_fulltext_eligibility.py`](../tests/test_fulltext_eligibility.py)
and
[`tests/test_extraction_codebook.py`](../tests/test_extraction_codebook.py)
gain `test_num_ctx_flag_threads_to_options` and
`test_num_ctx_changes_prompt_hash`.

#### 3. Stage 8 default model

Conditional on Q1; see Open questions. Implementation is a one-line
default change plus a test update.

### Blocked-on-decision commits

#### 4. `--concurrency` on stages 7 and 8

Conditional on Q2. If "yes": lift the `ThreadPoolExecutor` pattern
from
[`screen.py:177–196`](../src/laglitsynth/screening_abstracts/screen.py)
into a shared helper module; stages 7 and 8 use it. Tests as for
stage 3. If "no": the doc-only one-liner in
[`usability-docs.md`](usability-docs.md) is the closing fix.

#### 5. `.env` fallback

Conditional on Q3. If "yes": modify
[`catalogue_fetch/fetch.py`](../src/laglitsynth/catalogue_fetch/fetch.py)
and
[`fulltext_retrieval/retrieve.py`](../src/laglitsynth/fulltext_retrieval/retrieve.py)
to read `.env` when the flag isn't passed and emit a stderr load
notice. Tests assert (a) explicit flag wins over `.env`,
(b) missing flag + `.env` value loads with stderr notice,
(c) missing both fails with current error. If "no": doc-only fix.

#### 6. `bake-model`

Conditional on Q4. If subcommand: ~50 LOC of new code under
`src/laglitsynth/bake_model/` (or as a top-level module); CLI
registration; tests. If shell script: file under
`scripts/bake-model.sh`; no Python changes; doc reference in
[`external-services.md`](../docs/external-services.md). If neither:
no-op.

#### 7. `pipeline-up`

Conditional on Q5. If "yes": new module orchestrating Ollama serve +
model pull + Modelfile bake + GROBID docker run + readiness wait +
URL print. Substantial. Defer; this plan flags it as a follow-up if
the answer is "yes."

### Run-id elevation (separate plan)

Q6 needs its own plan if the answer leans toward (a) or (b). The
stderr print from the cutover is the interim fix; the real change
touches every run-id-aware stage's CLI and the wrapper, so it earns
its own design doc.

## Open questions

### Q1: Stage 8 default model

The CLI default at
[`extract.py:308`](../src/laglitsynth/extraction_codebook/extract.py)
is `gemma3:4b`, documented as broken on full-text inputs. Two shapes:

- **(A) Change default to `llama3.1:8b`.** Manual users get a working
  default. Risk: requires a separate `ollama pull llama3.1:8b` (~5 GB
  download).
- **(B) Remove default; require `--model`.** Forces the user to
  choose. Loses some convenience but eliminates silent failure.

A third option — **(C) keep `gemma3:4b` but emit a startup warning**
("this model is known to fail on full-text; pass `--model
llama3.1:8b` for production") — is the conservative middle ground.

Recommendation: (A). The CLI defaults should produce useful
behaviour; the doc warning at L251–256 is too easy to miss for a
copy-paster. The download cost is paid once per user.

### Q2: Concurrency on stages 7 and 8

Per [`docs/llm-concurrency.md`](../docs/llm-concurrency.md) L84–98,
stage 8 is prefill-bound — client-side concurrency doesn't help much
without a continuous-batching engine like vLLM. Stage 7 likely
benefits more (shorter prompts).

The honest cost-benefit:

- **(A) Add `--concurrency` to both.** Surface parity with stage 3,
  stage 7 gets real speedup, stage 8 gets ~5–10% best case.
- **(B) Add `--concurrency` to stage 7 only.** Honest about the
  prefill bound on stage 8. Asymmetric CLI surface.
- **(C) Don't add anywhere.** Doc says it doesn't help much; close
  the gap with documentation rather than code.

Recommendation: (A). Symmetry beats marginal speedup considerations;
the cost is one shared helper module. The throughput discussion in
the doc is enough caveat for a sophisticated user.

### Q3: `.env` fallback

The
[`docs/interfaces.md`](../docs/interfaces.md) L236–246 design rules
out env-var fallback explicitly. Three shapes:

- **(A) Add fallback** — read `.env` when flag absent, emit stderr
  notice. Closes the README's manual-user trap.
- **(B) No fallback; doc fix** — show
  `set -a; source .env; set +a` in README setup. Preserves the
  no-silent-override design.
- **(C) Add a `--env-file PATH` flag** — explicit opt-in to env
  loading. Compromise.

Recommendation: (B). The
[`usability-docs.md`](usability-docs.md) plan already adds the doc
line; the design rule is there for a reason and the cost of "source
your .env" is tiny. (C) feels over-designed for the problem.

### Q4: `bake-model` shape

- **(A) CLI subcommand.** Discoverable via `laglitsynth --help`.
  Tests integrate with existing CLI test patterns.
- **(B) Shell script** under `scripts/`. Smaller surface; reuses bash
  patterns the NESH wrapper already uses.
- **(C) Neither.** Just document the recipe in
  [`external-services.md`](../docs/external-services.md) and let the
  user write `ollama create` themselves.

Recommendation: (B). The Modelfile bake is a one-line `ollama create`
behind a 2-line heredoc; making it a CLI subcommand pays subcommand
surface for a thin wrapper. A shell script is the honest shape and
matches what the NESH wrapper is already doing inline.

### Q5: `pipeline-up`

- **(A) Build it.** Local users get a one-command "start everything";
  the wrapper becomes a slurm-aware variant of the same.
- **(B) Don't.** The wrapper *is* the pipeline-up command for the
  cluster path; locally a user runs two commands (`ollama serve`,
  `docker run`). Adding a third Python command for the same job is
  surface for surface's sake.

Recommendation: (B). The savings are modest; the maintenance cost
across local + cluster + variations is substantial. Document the
two-command sequence in
[`external-services.md`](../docs/external-services.md) and call it
done.

### Q6: Run-id elevation

- **(A) Pipeline-init.** A `laglitsynth pipeline-init` command emits
  one run-id; every stage takes `--pipeline-run-id`; the wrapper and
  manual users use the same shape.
- **(B) Drop run-id leaves for non-sweep.** Treat the latest run as
  default; `--run-id` becomes opt-in for sweeps. Simpler to use,
  loses sweep traceability for un-pinned runs.
- **(C) Status quo + stderr print.** The cutover plan's tactical fix
  is enough.

Recommendation: (C) for now. (A) and (B) both warrant their own
plans; the cutover fix removes the worst of the surprise. Revisit
when stages 9–12 land and the run-id story has more consumers.

## Follow-ups

- Per Q4 (B): `scripts/bake-model.sh` is a tiny commit that doesn't
  need a plan — land alongside `external-services.md` if Q4
  resolves to (B).
- Per Q6: if (A) or (B) wins, write a separate
  `plans/run-id-elevation.md` with the migration shape.
- The XLSX exporter ([`screening_abstracts/export.py`](../src/laglitsynth/screening_abstracts/export.py))
  data-driven `build_work_sheet` simplification from
  [`simplification-pass.md`](../docs/explorations/simplification-pass.md)
  3.5 — substantial enough to warrant its own commit; the project
  owner has called keeping the export, leaving simplification
  optional.

## Risks

**Surface bloat.** Adding flags to stages 7 and 8 (`--num-ctx`,
optionally `--concurrency`) plus possibly subcommands (`bake-model`,
`pipeline-up`) grows the CLI surface by 4–8 entries. Mitigation: each
addition has a concrete usability-review finding behind it; the
recommendations in section [Open questions](#open-questions) lean
conservative on the bigger surfaces (Q4 → script, Q5 → no, Q6 →
defer).

**`prompt_sha256` recipe drift.** Adding `--num-ctx` to the hash
covers manual users who'd otherwise mix verdicts across context
windows. But the recipe at
[`extract.py:354`](../src/laglitsynth/extraction_codebook/extract.py)
already includes `_NUM_CTX`; the new flag's value just replaces the
constant in the hash input. Confirm the test
`test_num_ctx_changes_prompt_hash` actually exercises this.

**Documentation feedback loop with
[`usability-docs.md`](usability-docs.md).** The doc plan adds
"`--concurrency` is absent on stages 7/8" notes. If Q2 lands as (A),
those notes need to be removed in the same commit that adds the
flag. Coordinate the two plans' commits in roadmap order.

## Critical files

- [`src/laglitsynth/screening_abstracts/screen.py`](../src/laglitsynth/screening_abstracts/screen.py)
  — preflight lift; concurrency reference implementation.
- [`src/laglitsynth/fulltext_eligibility/eligibility.py`](../src/laglitsynth/fulltext_eligibility/eligibility.py)
  — preflight, `--num-ctx`, `--concurrency` (if Q2 = A).
- [`src/laglitsynth/extraction_codebook/extract.py`](../src/laglitsynth/extraction_codebook/extract.py)
  — preflight, `--num-ctx`, default model (Q1), `--concurrency` (if
  Q2 = A).
- [`src/laglitsynth/catalogue_fetch/fetch.py`](../src/laglitsynth/catalogue_fetch/fetch.py),
  [`src/laglitsynth/fulltext_retrieval/retrieve.py`](../src/laglitsynth/fulltext_retrieval/retrieve.py)
  — `.env` fallback (if Q3 = A).
- New: shared preflight module under
  [`src/laglitsynth/`](../src/laglitsynth/).
- New: shared concurrency helper module (if Q2 = A).
- New: [`scripts/bake-model.sh`](../scripts/) (if Q4 = B).
- [`docs/llm-concurrency.md`](../docs/llm-concurrency.md) — update to
  drop the "stages 7/8 do not yet honour `LLM_CONCURRENCY`" caveat
  if Q2 = A.
- [`scripts/run-pipeline.sh`](../scripts/run-pipeline.sh),
  [`scripts/nesh-pipeline.sbatch`](../scripts/nesh-pipeline.sbatch) —
  thread the new flags if/when added.
- [`plans/usability-docs.md`](usability-docs.md) — coordinate
  doc-text changes with the CLI changes here (the
  "--concurrency absent" note in particular).
- [`plans/roadmap.md`](roadmap.md) — index update.
