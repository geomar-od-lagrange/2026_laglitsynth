# Plan: per-review config, externalized screening prompt, threshold + knob cleanups

## Goal

Act on four of the five improvement directions from the UI-knobs
investigation (direction 4, a sweep/matrix driver, is deferred). Make
the surface a reviewer actually touches — the runner — speak a single
per-review config file instead of scattered positionals, baked literals,
and inline prompts; externalize the one un-externalized LLM system prompt
(stage 3's screening prompt) to a YAML, matching stages 7/8; unify how
the corpus thresholds are *handled* without forcing them to one value;
and close a handful of exposure/doc-truth gaps. No new orchestration
mechanism — this wires the runner to the config/criteria machinery the
codebase already has.

## Design decisions

The two corpus thresholds stay independently settable. `--screening-threshold`
gates two different commitments — whether to spend a scarce PDF fetch
(stage 5) and whether to spend an LLM eligibility pass (stage 7) — so a
reviewer may legitimately want different cutoffs even though both read
the same stage-3 scores. We do not collapse them to one value. We unify
only the *handling*: the flag becomes `float` on stage 3 too (it is
`int` there today, `float` on 5/7), and the runner exposes each as a
named variable (`RETRIEVAL_THRESHOLD`, `ELIGIBILITY_THRESHOLD`, each
defaulting to a shared `SCREENING_THRESHOLD`) instead of two hardcoded
`50` literals.

The screening prompt moves into a YAML, mirroring stages 7/8. Stage 3
today carries a fixed `SYSTEM_PROMPT` (the 0–100 + reason JSON contract,
`screen.py:36-43`) as the system message and takes the per-review
criterion as a positional `PROMPT`, sent as `Criterion: {prompt}` in the
user message. After this change a `--screening-criteria FILE` YAML
carries a single `system_prompt` field (the contract *and* the criterion,
authored by the reviewer — exactly the shape stage 7's eligibility-criteria
YAML uses), loaded via the same `load_system_prompt` helper pattern. The
system message is that loaded prompt; the user message becomes just the
work listing (`format_screening_input`, title/authors/year/abstract — the
`Criterion:` prefix is dropped). The positional `PROMPT` is removed.
`ScreeningMeta` gains `criterion: str` (the loaded system prompt, for the
reviewer export) replacing its current `prompt` field, mirroring
`EligibilityMeta.criterion`; `prompt_sha256` hashes the loaded
`system_prompt` plus the user-render template, mirroring stage 7. A
default `examples/screening-criteria/lagrangian-oceanography.yaml`
reproduces today's behaviour (current contract text + the Lagrangian
relevance question).

The runner is driven by one per-review YAML. `scripts/run-pipeline.sh
<config.yaml>` reads a typed `ReviewConfig` (new Pydantic model) holding
the review-defining knobs: `query`, `from_year`/`to_year`, `max_records`,
`screening_criteria` / `eligibility_criteria` / `codebook` paths,
`thresholds.{retrieval,eligibility}`, `models.{screening,eligibility,extraction}`,
`num_ctx.{eligibility,extraction}`, `concurrency.{llm,extraction}`. YAML
is parsed and validated on the Python side: a new `laglitsynth
review-config <path>` subcommand reads + validates the `ReviewConfig` and
prints `KEY=value` lines the runner `eval`s. Precedence stays
env > config > built-in default, expressed in the runner as
`VAR="${VAR:-${CFG_VAR:-default}}"`, so existing env / `sbatch --export`
overrides keep working and an absent config field falls back to today's
default. Invocation with no argument keeps the current smoke run by
pointing at a default review config. This does **not** introduce
`data/manifest.json` or the typed `RunManifest` — that remains the queued
[run-manifest](../run-manifest.md) plan.

Abstract-lookup runner wiring is out of scope here. Threading stage 2b
into the runner is only useful once screening can read its sidecar via a
`--abstracts` overlay, which is tracked for PR #19 (the
[abstract-lookup-wiring](../abstract-lookup-wiring.md) plan) and is not on
this branch. We still add `ABSTRACT_LOOKUP_EMAIL` and
`SEMANTIC_SCHOLAR_API_KEY` to `.env.example` so the credentials knob is
discoverable.

## Implementation sequence

`pixi run typecheck` and `pixi run test` pass between commits. The work
splits into three independent-then-converging verticals (suitable for
parallel agents A and B, then C):

1. **(A) Externalized screening prompt + threshold type.** In
   [screening_abstracts](../../src/laglitsynth/screening_abstracts/):
   replace the positional `PROMPT` with `--screening-criteria` (default
   `examples/screening-criteria/lagrangian-oceanography.yaml`); load its
   `system_prompt` via a `load_system_prompt`-style loader (reuse stage
   7's helper by lifting it to a shared module if clean, else mirror it);
   send the loaded prompt as the system message and drop the `Criterion:`
   user prefix; change `--screening-threshold` to `float`; rename
   `ScreeningMeta.prompt` → `criterion` (= loaded system prompt) and
   repoint the export; update `prompt_sha256` to hash the loaded prompt +
   user-render template. Add the default YAML under
   `examples/screening-criteria/`. Update
   [test_screening_abstracts*.py](../../tests/) (new flag, criterion meta,
   float threshold) and the stage-3 export test. Docs:
   [screening-abstracts.md](../../docs/screening-abstracts.md) (new
   `--screening-criteria`, the YAML, criterion meta, float threshold) and
   the stage-3 CLI + threshold-type note in
   [interfaces.md](../../docs/interfaces.md). Do **not** touch
   `scripts/run-pipeline.sh`, `fulltext_extraction`, or
   [configs.md](../../docs/configs.md).

2. **(B) consolidateCitations flag + `.env.example`.** In
   [fulltext_extraction/extract.py](../../src/laglitsynth/fulltext_extraction/extract.py):
   expose the hardcoded `consolidateCitations="0"` as a
   `--consolidate-citations {0,1}` flag (default `0`, current behaviour).
   Add a test asserting the flag reaches the GROBID form field. Add
   `ABSTRACT_LOOKUP_EMAIL` and `SEMANTIC_SCHOLAR_API_KEY` (commented) to
   [.env.example](../../.env.example). Docs: the now-a-flag note in
   [fulltext-extraction.md](../../docs/fulltext-extraction.md) and
   [external-services.md](../../docs/external-services.md). Do **not** touch
   `scripts/run-pipeline.sh`, `screening_abstracts`, or `interfaces.md`.

3. **(C, after A+B) Per-review config + runner.** Add a typed
   `ReviewConfig` Pydantic model and a `laglitsynth review-config <path>`
   subcommand that validates it and emits `KEY=value` lines. Rewrite
   [run-pipeline.sh](../../scripts/run-pipeline.sh) to take an optional
   `<config.yaml>` (default keeps the smoke run), `eval` the emitted
   vars, and apply env > config > default precedence; thread
   `--from-year`/`--to-year` into stage 1, point stage 3 at
   `--screening-criteria`, replace the two `50` literals with
   `RETRIEVAL_THRESHOLD` / `ELIGIBILITY_THRESHOLD`. Adapt
   [nesh-pipeline.sbatch](../../scripts/nesh-pipeline.sbatch) to the new
   invocation. Add a default `examples/reviews/lagrangian-oceanography.yaml`
   reproducing the current smoke/real defaults. Tests for `ReviewConfig`
   + the emitter. Docs: [configs.md](../../docs/configs.md) (the per-review
   config and its precedence), [README.md](../../README.md) §Running the
   pipeline (new invocation), and [cross-machine.md](../../docs/cross-machine.md)
   if the run-from-root contract needs a note.

## Follow-ups

Abstract-lookup runner wiring waits on the screening `--abstracts`
overlay ([abstract-lookup-wiring](../abstract-lookup-wiring.md), PR #19).
A first-class sweep/matrix driver over the fan-out knobs (prompt × model
× threshold × seed → a set of run-ids + a consensus reader) is direction
4 from the investigation, deferred; it builds naturally on this config
once present and on [multi-run-consensus](../multi-run-consensus.md).

## Critical files

- [src/laglitsynth/screening_abstracts/screen.py](../../src/laglitsynth/screening_abstracts/screen.py),
  [models.py](../../src/laglitsynth/screening_abstracts/models.py),
  [export.py](../../src/laglitsynth/screening_abstracts/export.py),
  and a new `examples/screening-criteria/` YAML
- [src/laglitsynth/fulltext_extraction/extract.py](../../src/laglitsynth/fulltext_extraction/extract.py),
  [.env.example](../../.env.example)
- [scripts/run-pipeline.sh](../../scripts/run-pipeline.sh),
  [scripts/nesh-pipeline.sbatch](../../scripts/nesh-pipeline.sbatch),
  a new `ReviewConfig` model + `review-config` subcommand, and a new
  `examples/reviews/` YAML
- Docs: [screening-abstracts.md](../../docs/screening-abstracts.md),
  [interfaces.md](../../docs/interfaces.md),
  [fulltext-extraction.md](../../docs/fulltext-extraction.md),
  [external-services.md](../../docs/external-services.md),
  [configs.md](../../docs/configs.md), [README.md](../../README.md)
