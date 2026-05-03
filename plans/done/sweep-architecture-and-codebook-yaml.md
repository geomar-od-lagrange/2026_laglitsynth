# Sweep architecture and codebook YAML

## Goal

Make the LLM stages (3, 7, 8) sweep-friendly: every run lives in its own
timestamp+UUID directory with a self-contained `config.yaml` so a parameter
sweep is a directory of replayable runs. Lift the codebook out of pydantic
into a YAML schema loaded at startup, so adding/removing/renaming fields is a
config edit, not a code change. Lift the topic-bound prompts (stage 7
eligibility criteria, stage 8 codebook role) out of code into config files,
so swapping to a different research domain is configuration work, not a
refactor.

## Non-goals

Out of scope: synthesis-side score joining across run-ids (next plan),
[multi-run consensus](multi-run-consensus.md), dedup-rule configurability,
source-priority configurability, typed codebook fields, a sweep-matrix
runner. Stages 1, 2, 4, 5, 6, 9–12 are not touched. PDFs and TEIs already
share via `--skip-existing`; no changes there.

## Target state

### Run directories

LLM-stage outputs land in `<data-dir>/<stage-subdir>/<run-id>/`. Each
stage has a hardcoded `<stage-subdir>` (`screening-abstracts`,
`fulltext-eligibility`, `extraction-codebook`); `--data-dir` (default
`data/`) carries the bucket root and `--run-id` (default = generated)
carries the leaf. The legacy `--output-dir` flag is removed in favour
of this two-flag model so a sweep can repoint the corpus without
having to spell each stage's path out.

```
<data-dir>/screening-abstracts/2026-05-03T14-30-00_a3f8c2d19b04/
    config.yaml             # resolved CLI+config values, replayable
    screening-meta.json     # runtime stats: counts, timings, LLM fingerprint
    verdicts.jsonl          # per-work outputs

<data-dir>/fulltext-eligibility/2026-05-03T14-31-12_b27e91d4e8f1/
    config.yaml
    eligibility-meta.json
    verdicts.jsonl
    eligible.jsonl

<data-dir>/extraction-codebook/2026-05-03T14-32-44_c8a127e90b3d/
    config.yaml
    extraction-codebook-meta.json
    records.jsonl
```

Meta filenames stay stage-specific (not bare `meta.json`) so the files
remain self-identifying when collected across run dirs for an overview.

Run ID format: ISO timestamp at 1-second resolution + `_` + 12 hex chars
from `uuid.uuid4().hex[:12]`. The timestamp is human-readable; the hex
suffix removes any concurrent-job collision concern on NESH or local sweeps.

PDFs and TEIs (under `<data-dir>/fulltext-extraction/tei/` etc.) are not
run-namespaced — they are a shared input cache, populated incrementally
via the existing `--skip-existing` paths.

### Config files: CLI-first with config as defaults

Every stage parameter remains a CLI flag. `--config foo.yaml` loads values
as argparse defaults; explicit CLI flags override config. After argument
resolution, `<run-dir>/config.yaml` is **always written**, fully resolved,
with file-valued parameters (codebook, eligibility criteria) **inlined as
embedded content** rather than referenced by path. This makes the run
directory a self-contained replay artifact; replay is `--config
<run-dir>/config.yaml` with a fresh run-id.

The meta JSON keeps its current role (tool, run timestamp, validation
counts, LLM fingerprint, per-stage stats). The overlap with `config.yaml`
(model, temperature, etc.) is intentional: the two files serve different
audiences — `config.yaml` for "rerun this exactly", the meta file for
"what happened during this run."

### Codebook YAML

Codebooks become data-driven. A new module
`src/laglitsynth/extraction_codebook/codebook.py` exposes a
`CodebookSpec` pydantic meta-schema and a loader that builds the
extraction-payload pydantic class at runtime via `pydantic.create_model()`.
The YAML carries both the role description (system prompt) and the field
list:

```yaml
id: lagrangian-oceanography
description: 28-field codebook for computational Lagrangian methods in oceanography.
system_prompt: |
  You extract structured metadata from scientific papers on computational
  Lagrangian methods in oceanography. ...

fields:
  - name: sub_discipline
    description: |
      Sub-discipline tag. Free text — use the paper's own phrasing ...
  - name: integration_scheme
    description: |
      Numerical integration scheme used to advance particles ...
  # ... remaining 12 paired fields ...
  - name: in_text_locations
    description: |
      Where in THIS PAPER the extracted values were found ...
    context: false
  - name: extraction_notes
    description: ...
    context: false
```

`context: true` is the default. Each named field gets a paired
`<name>_context` (also `str | None`) automatically. All value fields stay
`str | None` — the value+context pairing is a deliberate late-binding
design, and typing the value field would collapse categorisation into
extraction time. The existing `mode="before"` coercion validator moves
to a `_PayloadBase` class and is inherited by the dynamically-built
model via `__base__=_PayloadBase`. The existing `_render_field_list()`
continues to read `model_json_schema()` and stays generic.

The default codebook ships at
`examples/codebooks/lagrangian-oceanography.yaml`, transcribed verbatim
from today's `_ExtractionPayload`. The class `_ExtractionPayload` is
deleted. `ExtractionRecord`'s identification block (work_id, source_basis,
reason, seed, truncated, raw_response) stays in
[extraction_codebook/models.py](../src/laglitsynth/extraction_codebook/models.py);
its payload fields come from the loaded codebook at runtime.

The codebook YAML's content (not its path) is inlined into `config.yaml`
on save, so a run is self-validatable from its own directory.

### Eligibility criteria YAML

[fulltext_eligibility/prompts.py](../src/laglitsynth/fulltext_eligibility/prompts.py)
no longer hardcodes the 3-point Lagrangian-oceanography criterion. A new
`--eligibility-criteria` CLI parameter accepts a file path; the file is a
small YAML carrying the criteria text:

```yaml
id: lagrangian-oceanography
system_prompt: |
  You are assessing whether a scientific paper meets the inclusion criteria
  for a systematic review of numerical methods in Lagrangian oceanography.
  Criteria:
  1. ...
  Respond with JSON: {"eligible": true|false, "reason": "<one sentence>"}.
```

Default at `examples/eligibility-criteria/lagrangian-oceanography.yaml`,
transcribed verbatim from today's `SYSTEM_PROMPT`. Inlined into
`config.yaml` on save.

Stage 3's screening system prompt at
[screen.py:31](../src/laglitsynth/screening_abstracts/screen.py) is
already topic-generic — the user prompt is the criterion. No lift needed
there.

## Design decisions

Three non-obvious calls worth naming. **Inlining vs. path references in
`config.yaml`.** Inlining wins because file edits would silently break
replay: a run dir must be a self-contained unit you can copy or move.
Cost: configs grow to hundreds of lines for a full codebook. Acceptable.
**Codebook YAML carries the role prompt.** Bundling the system prompt
into the codebook YAML means stage 8 has one config-file domain artifact,
not two. The role and field list are tightly coupled (the prompt
references "the fields below"). One file, one mental model. **One
polymorphic key for file-valued args.** `codebook:` and
`eligibility_criteria:` each take a single key whose value is a path
string on the way in and a structured mapping after `save_resolved_config`
inlines it. The loader sniffs the type — `str` → load YAML at that
path; mapping → use directly. Two-key alternatives (`codebook_path:`
vs `codebook:`) introduce a "which one wins" rule with no benefit; the
polymorphic key keeps the input-file and replay-file identically
shaped from the user's perspective.

## Implementation sequence

Each step ends with `pixi run typecheck` and `pixi run test` passing.

1. **Config-loading helpers.** New `src/laglitsynth/config.py` with
   `load_config(path) -> dict[str, Any]`, an `add_config_arg(parser)`
   helper that registers `--config` and applies loaded values via
   `parser.set_defaults(**values)`, and `save_resolved_config(args, run_dir,
   inlines)` that writes `<run-dir>/config.yaml` with the named
   file-valued args expanded to embedded content. Tests: round-trip a
   config; CLI flag overrides config value; inlined content survives
   round-trip; missing keys fall through to argparse defaults.

2. **Run-id helper.** Extend
   [src/laglitsynth/ids.py](../src/laglitsynth/ids.py) with
   `generate_run_id(now: datetime | None = None) -> str` returning
   `<iso-1s>_<12hex>`. Tests: format matches the expected regex; two
   calls produce different IDs; the timestamp portion respects an
   injected `now`.

3. **Stage 3 wired to run-id dirs + config.** Replace `--output-dir`
   with `--data-dir` (default `data/`) and `--run-id` (default =
   `generate_run_id()`). Resolved output dir is
   `<data-dir>/screening-abstracts/<run-id>/`. Always write
   `config.yaml`. Tests: run produces a fresh run-id dir; `config.yaml`
   round-trips through `--config`; CLI flags override config values;
   existing screening tests adapted to the new dir structure.

4. **Eligibility criteria lift + stage 7 wiring.** New CLI flag
   `--eligibility-criteria <path>` defaulting to
   `examples/eligibility-criteria/lagrangian-oceanography.yaml`;
   `prompts.py`'s `SYSTEM_PROMPT` constant becomes a
   `load_system_prompt(spec) -> str` function called from stage 7's
   `run()` and threaded through `classify_*`. Stage 7 follows stage 3's
   `--data-dir` + `--run-id` + config pattern (replacing `--output-dir`).
   Tests: alternate criteria YAML produces a different prompt; default
   criteria reproduces today's prompt byte-for-byte; config round-trip +
   CLI-override coverage.

5. **Codebook YAML loader.** New module
   `src/laglitsynth/extraction_codebook/codebook.py`: `CodebookSpec`
   pydantic meta-schema (id, description, system_prompt, fields with
   name/description/context default-true), `load_codebook(spec) ->
   type[BaseModel]` using `pydantic.create_model(__base__=_PayloadBase,
   ...)`. Move the `mode="before"` coercer onto `_PayloadBase`. Tests:
   load the transcribed v1 YAML, build the model, validate a sample LLM
   response (success), validate a malformed response (fails as today),
   `_render_field_list()` reproduces today's prompt byte-for-byte against
   the v1 YAML.

6. **Stage 8 cut over to YAML codebook + config + run-id.** Add
   `--codebook <path>` defaulting to
   `examples/codebooks/lagrangian-oceanography.yaml`. Replace
   `--output-dir` with `--data-dir` + `--run-id`. Delete
   `_ExtractionPayload` from
   [extraction_codebook/models.py](../src/laglitsynth/extraction_codebook/models.py).
   `extract_codebook()` operates on the dynamically-built payload class.
   `config.yaml` inlines the full codebook content. `--skip-existing`
   keeps working only when `--run-id <existing>` points at a prior run.
   Tests: smoke run on a small input produces records that validate;
   replay from `<run-dir>/config.yaml` produces an equivalent run
   modulo LLM stochasticity; record shape on the v1 codebook matches
   today's.

7. **Docs update.** Update
   [docs/screening-abstracts.md](../docs/screening-abstracts.md),
   [docs/eligibility.md](../docs/eligibility.md), and
   [docs/extraction-codebook.md](../docs/extraction-codebook.md) to
   describe the config-file workflow, run-id directory layout, codebook
   YAML format, and eligibility-criteria YAML format. Add
   [docs/configs.md](../docs/configs.md) covering the CLI-config
   precedence rule and the inlining principle.

## Follow-ups

Synthesis-side multi-run loading and score-cut design — the next plan,
depends on this one. The decomposability assumption (extracting field
subsets gives the same answers as extracting the full codebook) stays
untested for now; we won't merge minimal codebooks across runs.
Backfilling existing runs to the new directory shape is not planned —
green-field per [AGENTS.md](../AGENTS.md), start fresh.

## Risks

LLM stochasticity makes "replay" reproduce the input config, not the
output records (temperature 0.8). State this explicitly in
[docs/configs.md](../docs/configs.md) so reviewers don't expect
bit-identical reruns. Codebook YAML loader correctness is load-bearing
for stage 8: the byte-identical-prompt regression test in step 5 is the
mitigation — without it, a transcription bug silently changes prompts
across the whole pipeline.

## Critical files

- [src/laglitsynth/screening_abstracts/screen.py](../src/laglitsynth/screening_abstracts/screen.py)
- [src/laglitsynth/fulltext_eligibility/eligibility.py](../src/laglitsynth/fulltext_eligibility/eligibility.py)
- [src/laglitsynth/fulltext_eligibility/prompts.py](../src/laglitsynth/fulltext_eligibility/prompts.py)
- [src/laglitsynth/extraction_codebook/extract.py](../src/laglitsynth/extraction_codebook/extract.py)
- [src/laglitsynth/extraction_codebook/models.py](../src/laglitsynth/extraction_codebook/models.py)
- [src/laglitsynth/extraction_codebook/prompts.py](../src/laglitsynth/extraction_codebook/prompts.py)
- [src/laglitsynth/ids.py](../src/laglitsynth/ids.py)
- [src/laglitsynth/io.py](../src/laglitsynth/io.py)
- [src/laglitsynth/models.py](../src/laglitsynth/models.py)
- [plans/multi-run-consensus.md](multi-run-consensus.md) — companion plan that consumes the run-id outputs
