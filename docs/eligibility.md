# Eligibility

Full-text assessment of whether each included work meets the review's
inclusion criteria. Distinct from [screening](screening-abstracts.md),
which uses only title and abstract. Reading the full text may reveal
that a paper is not actually about computational Lagrangian methods, or
that it is a review article rather than primary research.

## Prerequisites

See [external-services.md](external-services.md) for Ollama setup.

## Prototype scope

A single LLM pass over the extracted full text. Same flag-don't-filter
pattern as [`screening-abstracts`](screening-abstracts.md): structured
JSON output, Pydantic validation, verdicts stored in a sidecar keyed by
`work_id`. This stage is **full-text-only** — a work without a usable
full-text extraction is not assessed at all (no verdict row, no count);
the criteria here turn on numerical-method detail that abstracts do not
carry, so an abstract is not a valid basis for an eligibility decision.

The eligibility criteria below are provisional placeholders for the
prototype. They are deliberately broad — designed to let papers through,
not to be the final filter. The human turns knobs once the pipeline runs
and produces reviewable output. False positives are acceptable in the
prototype; false negatives are not.

1. The paper describes a computation that tracks particles, tracers, or
   objects in an ocean flow field.
2. The paper is primary research (not a review, editorial, or commentary).
3. The paper contains at least some description of the numerical methods
   used. (Under discussion: lack of method description is poor practice
   but may not disqualify a paper from relevance. This criterion may be
   relaxed in a later iteration.)

## Input

The stage consumes three artifacts:

- The deduplicated catalogue ([`Work`](../src/laglitsynth/catalogue_fetch/models.py)
  records from [`catalogue-dedup`](catalogue-dedup.md)).
- The stage 3 screening verdict sidecar ([`ScreeningVerdict`](../src/laglitsynth/screening_abstracts/models.py)
  records), plus a `--screening-threshold` cutoff. The stage joins these at
  read time to determine which works to assess.
- The extraction JSONL ([`ExtractedDocument`](../src/laglitsynth/fulltext_extraction/models.py)
  records from [`fulltext-extraction`](fulltext-extraction.md)).

Only works that have an `ExtractedDocument` are assessed: the active set
fed to the LLM is gated on extraction presence, so a work without a
matching extraction never enters the loop, produces no verdict row, and
is not counted. (The "what is still missing" gap is already visible
upstream in the stage 5 `missing` provenance records — flagging absent
full text again here would be redundant.) A work whose extraction parses
but renders empty TEI is recorded with a `tei-parse-failure` sentinel,
since a paper we hold a PDF for but cannot read is a broken extraction
worth surfacing.

## Data model

### EligibilityVerdict

One per catalogue work that reached this stage (i.e. that had a
full-text extraction).

```python
class EligibilityVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    eligible: bool | None              # None for sentinel-reason skips
    reason: str | None                 # LLM free-text or sentinel
    seed: int | None                   # Ollama seed used; None for sentinels
    raw_response: str | None           # LLM's raw message content; None when no call was made
```

Every verdict is full-text-based, so there is no `source_basis` field —
the basis is implicit. `eligible` is tri-state. `True` and `False` are
real LLM verdicts; `None` indicates a sentinel skip — the LLM was not
called or its output could not be parsed. See
[Sentinel reasons](#sentinel-reasons) below.

`raw_response` captures the LLM's message text before parsing. Present
on successful verdicts and on `llm-parse-failure` sentinels (so a
reviewer can see what the model actually said); `None` on the
`tei-parse-failure` sentinel that did not call the LLM.

The LLM's response is validated through a private
[`_EligibilityPayload`](../src/laglitsynth/fulltext_eligibility/models.py)
pydantic submodel. A `mode="before"` validator drops unknown keys
(LLMs at `temperature > 0` sprinkle extras) and coerces a `reason`
returned as a list into a `" / "`-joined string.

### EligibilityMeta

```python
class EligibilityMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    llm: LlmMeta
    input_catalogue: str
    input_screening_verdicts: str
    input_extractions: str
    input_count: int
    eligible_count: int
    excluded_count: int
    tei_parse_failure_count: int
    llm_parse_failure_count: int
    llm_timeout_count: int
    criterion: str        # the eligibility-criteria system prompt, verbatim
```

`run` and `llm` are the shared reproducibility nests from
[`src/laglitsynth/models.py`](../src/laglitsynth/models.py) — they
carry `tool`, `tool_version`, `run_at`, `validation_skipped`, `model`,
`temperature`, and `prompt_sha256`. `prompt_sha256` covers the loaded
eligibility-criteria system prompt, `USER_TEMPLATE`, and the Ollama
`num_ctx` setting, so any prompt- or context-window change shifts the
hash.

`input_count` is the number of works that reached the stage (had a
full-text extraction) — works skipped for lack of full text are neither
counted nor flagged. Per-sentinel counts (`tei_parse_failure_count`,
`llm_parse_failure_count`, `llm_timeout_count`) let operators diagnose a
run without re-reading `verdicts.jsonl`. There is no `no_source_count`
or `by_source_basis` — with full-text the only basis, a per-basis
breakdown carries no information. `criterion` records the criteria
system prompt verbatim so the review export can embed the same question
the LLM saw without re-loading the criteria YAML.

## Storage layout

```
<data-dir>/fulltext-eligibility/<run-id>/
  verdicts.jsonl              # one EligibilityVerdict per input work
  eligibility-meta.json       # EligibilityMeta
  config.yaml                 # resolved CLI+config, criteria inlined
```

See [configs.md](configs.md) for the run-id directory model and
`config.yaml` semantics. `verdicts.jsonl` is the source of truth and the
sole output of this stage. Downstream stages that need the eligible work
set join the catalogue against this sidecar at their own read time.

## Per-work flow

The active set is gated on extraction presence before the loop, so each
work reaching `_assess_one` has an `ExtractedDocument`. For each such
work:

1. Render the TEI. If it raises `lxml.etree.XMLSyntaxError`, record a
   `tei-parse-failure` sentinel (no LLM call).
2. If the render is empty (valid XML, no body content), record a
   `tei-parse-failure` sentinel (no LLM call) — a paper we hold a PDF
   for but cannot read is a broken extraction.
3. Otherwise call the LLM and record its verdict.

## Sentinel reasons

All sentinels set `eligible=None` and `seed=None`. Downstream consumers
join the catalogue against `verdicts.jsonl` filtering for `eligible is True`,
so sentinel records are naturally excluded from the active work set.

| Reason | Trigger |
|---|---|
| `tei-parse-failure` | `sections()` raises `lxml.etree.XMLSyntaxError`, or renders empty. A paper with a PDF but unreadable TEI is an operator-visible bug, unlike a plain absence of full text (which is silently skipped). |
| `llm-parse-failure` | The LLM returned output that could not be parsed into `{"eligible": bool, "reason": str}`. |
| `llm-timeout` | The OpenAI client raised `APITimeoutError` / `APIConnectionError` after all retries exhausted. The OpenAI client is constructed with `timeout=300s` and `max_retries=3` so a single hang on a long full-text prompt does not kill the stage. |

There is no `no-source` sentinel: a work without full text is simply not
processed, not flagged.

## Surfacing TEI to the LLM

[`laglitsynth.fulltext_eligibility.prompts.render_fulltext`](../src/laglitsynth/fulltext_eligibility/prompts.py)
walks `tei.sections()` depth-first. Each section contributes a block
whose first line is the title (when present) followed by its paragraphs
(one per line); nested children contribute further blocks. Blocks are
joined by blank lines. Figures and bibliography are dropped — the three
criteria are answered from body text. Empty `sections()` returns the
empty string, which the caller records as a `tei-parse-failure`
sentinel (there is no abstract fallback).

## CLI interface

```
laglitsynth fulltext-eligibility \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --screening-verdicts data/screening-abstracts/<run-id>/verdicts.jsonl \
    --screening-threshold 50 \
    --extractions data/fulltext-extraction/extraction.jsonl \
    [--extraction-output-dir data/fulltext-extraction/] \
    [--data-dir data/] [--run-id <iso>_<12hex>] \
    [--eligibility-criteria examples/eligibility-criteria/lagrangian-oceanography.yaml] \
    [--config <run-dir>/config.yaml] \
    [--skip-existing] [--max-records N] [--dry-run] \
    [--model gemma3:4b] [--base-url http://localhost:11434] \
    [--concurrency 1] [--num-ctx 32768]
```

The resolved output directory is `<data-dir>/fulltext-eligibility/<run-id>/`.

### Arguments

- `--catalogue`: the deduplicated catalogue (`Work` records from stage 2).
- `--screening-verdicts`: the stage 3 verdict sidecar
  (`ScreeningVerdict` records). The stage joins this against the catalogue
  at the `--screening-threshold` cutoff to determine which works to assess.
- `--screening-threshold`: relevance score cutoff 0–100 (default: 50).
  Works whose stage 3 score is at or above this threshold are assessed.
- `--extractions`: the extraction JSONL (`ExtractedDocument` records).
  Works without a matching record are skipped (not assessed, not counted).
- `--extraction-output-dir`: directory that
  `ExtractedDocument.tei_path` is relative to. Defaults to the parent
  of `--extractions`. See
  [`tei-wrapper`](../plans/done/tei-wrapper.md) for why the path is
  stored relative.
- `--data-dir`: bucket root for stage outputs (default: `data/`).
- `--run-id`: run identifier. Default: a generated `<iso>_<12hex>`.
- `--eligibility-criteria`: path to the YAML carrying the LLM system
  prompt (default:
  `examples/eligibility-criteria/lagrangian-oceanography.yaml`). The
  file's contents are inlined into `config.yaml` on save so a run dir
  is a self-contained run snapshot. See [configs.md](configs.md).
- `--config`: optional YAML config file whose values seed argparse
  defaults; explicit CLI flags still win. See [configs.md](configs.md).
- `--skip-existing`: load any prior `verdicts.jsonl` (in the run dir
  pointed at by `--run-id`) and skip already-assessed `work_id`s. The
  verdict sidecar is appended to on completion. If `eligibility-meta.json`
  already exists and its recorded `prompt_sha256` differs from the hash the
  current invocation would produce, the run aborts with an error — mixing
  verdicts from different prompt versions in one file would silently corrupt
  any downstream analysis. Run-id'd dirs are fresh by default, so this flag
  is meaningful only when paired with an explicit `--run-id <existing>`.
- `--max-records`: process only the first N works from the catalogue.
- `--dry-run`: print verdicts to stderr without writing any output.
- `--model`, `--base-url`: Ollama configuration. `--base-url` is checked
  at startup with the same preflight pattern as
  [`screening-abstracts`](screening-abstracts.md).
- `--num-ctx`: context window size passed to the model via `extra_body`
  (default: `32768`). For a guaranteed context window, bake the model
  first with [`bake-model`](bake-model.md) and pass the baked tag via
  `--model`. See [external-services.md](external-services.md) for the
  bake-vs-flag tradeoff.
- `--concurrency`: number of in-flight LLM requests (default: `1`).
  Stage 7's prompts are less prefill-heavy than stage 8's, so it can
  tolerate higher concurrency than stage 8 on the same GPU.
  See [llm-concurrency.md](llm-concurrency.md).

## LLM prompt

Loaded at runtime from the YAML pointed at by `--eligibility-criteria`
(default: the shipped Lagrangian-oceanography criterion). The file's
`system_prompt` field is sent verbatim as the system message; the
digest is recorded as `meta.llm.prompt_sha256`.

```
System: You are assessing whether a scientific paper meets the inclusion
criteria for a systematic review of numerical methods in Lagrangian
oceanography.

Criteria:
1. The paper describes a computation that tracks particles, tracers, or
   objects in an ocean flow field.
2. The paper is primary research (not a review, editorial, or commentary).
3. The paper contains at least some description of the numerical methods
   used.

Respond with JSON: {"eligible": true|false, "reason": "<one sentence>"}.

User: full_text:
<rendered text>
```

`response_format={"type": "json_object"}`, `temperature=0.8`, per-call
random seed recorded on the verdict. Same shape as
[`screening-abstracts`](screening-abstracts.md).

## Review export

`fulltext-eligibility-export` writes an XLSX review workbook
([`export.py`](../src/laglitsynth/fulltext_eligibility/export.py)) for
human spot-checking the LLM stage and tuning its prompt. It is XLSX-only
— the JSONL sidecar is the machine-readable form. The workbook has an
`Index` sheet (one row per sampled work, with the `eligible` /
`llm_reason` verdict inline and a hyperlink to the per-work tab, as a
light navigation aid) and one tab per sampled work. The per-work tab is
the working surface: a bibliographic block, the eligibility criterion
pulled verbatim from `eligibility-meta.json` so the reviewer sees the
same question the LLM saw, reviewer placeholders (`reviewer_eligible` /
`reviewer_reason`), and a collapsed LLM-verdict block.

```
laglitsynth fulltext-eligibility-export \
    --verdicts data/fulltext-eligibility/<run-id>/verdicts.jsonl \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    [--meta data/fulltext-eligibility/<run-id>/eligibility-meta.json] \
    [--output <verdicts parent>/review.xlsx] \
    [--n-subset N] [--subset-seed 0]
```

`--n-subset` / `--subset-seed` draw a reproducible random sample (the
intended workflow is digging into a subset and generalizing, not a wide
all-works scan); unset or `>=` the verdict count emits the full set in
verdict order. A verdict whose `work_id` is absent from the catalogue
raises a `ValueError` naming the offending id.

## What to defer

- Fine-grained exclusion reasons (enum of why a paper was excluded).
- Per-criterion verdicts (three booleans).
- Calibration against human judgments.
- Threshold-based re-eligibility (currently binary).
- Chunking or retrieval over long papers; prompt length policy is
  addressed as a follow-up in the stage 7 plan.
