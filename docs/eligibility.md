# Eligibility

Full-text assessment of whether each included work meets the review's
inclusion criteria. Distinct from [screening](screening-abstracts.md),
which uses only title and abstract. Reading the full text may reveal
that a paper is not actually about computational Lagrangian methods, or
that it is a review article rather than primary research.

## Prerequisites

An OpenAI-compatible endpoint and its key as `LLM_API_KEY`; see
[external-services.md](external-services.md#llm-endpoint).

## Prototype scope

One decision per paper on the decisions endpoint, with the rendered full
text as `input`. Same flag-don't-filter pattern as
[`screening-abstracts`](screening-abstracts.md): the probability is
stored, verdicts live in a sidecar keyed by `work_id`. This stage is **full-text-only** — a work without a usable
full-text extraction is not assessed at all (no verdict row, no count);
the full text is the basis of the decision.

The shipped criteria are deliberately broad — designed to let papers
through, not to be the final filter. Both must hold:

1. The paper describes a computation that tracks particles, tracers, or
   objects in an ocean flow field.
2. The paper is primary research (not a review, editorial, or commentary).

## Input

The stage consumes three artifacts:

- The deduplicated catalogue ([`Work`](../src/laglitsynth/catalogue_fetch/models.py)
  records from [`catalogue-dedup`](catalogue-dedup.md)).
- The stage 3 screening verdict sidecar ([`ScreeningVerdict`](../src/laglitsynth/screening_abstracts/models.py)
  records), plus a `--screening-threshold` cutoff. The stage joins these at
  read time to determine which works to assess. The join walks the catalogue
  and **silently drops** any work absent from the screening verdicts file
  (`_active_works` does `if sv is None: continue`): a work with no screening
  verdict is not assessed, produces no verdict row, and is not counted — with
  no warning. Running against a partial or stale verdicts file therefore
  assesses fewer works than the catalogue holds.
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
    probability: float | None          # decisions-endpoint answer; None for sentinels
    eligible: bool | None              # probability >= 0.5; None for sentinels
    reason: str | None                 # sentinel reason; None on a decided verdict
    raw_response: str | None           # response or error body; None when none was received
```

Every verdict is full-text-based, so there is no `source_basis` field —
the basis is implicit. `eligible` is tri-state: `True` and `False` are the
probability cut at a fixed 0.5; `None` marks a sentinel. The probability
is kept, so any other cut is made ad hoc on `verdicts.jsonl` without a
re-run. The endpoint returns no justification, so `reason` is set only
for sentinels. See [Sentinel reasons](#sentinel-reasons) below.

`raw_response` is the endpoint's response body on a decided verdict, the
body on `llm-parse-failure` and the error body on `llm-rejected`; `None`
on `tei-parse-failure` and `llm-timeout`.

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
    llm_rejected_count: int
    criterion: str        # the predicate instructions sent, verbatim
```

`run` and `llm` are the shared reproducibility nests from
[`src/laglitsynth/models.py`](../src/laglitsynth/models.py) — they
carry `tool`, `tool_version`, `run_at`, `validation_skipped`, `model`,
`base_url`, `decisions_url`, and `prompt_sha256`. `prompt_sha256` is
[`criteria_sha256`](../src/laglitsynth/fulltext_eligibility/eligibility.py)
of the criteria: the sha256 of the `decision` block's canonical JSON +
`"\n"` + `USER_TEMPLATE`, so any change to the question shifts the hash.

`input_count` is the number of works that reached the stage (had a
full-text extraction) — works skipped for lack of full text are neither
counted nor flagged. Per-sentinel counts (`tei_parse_failure_count`,
`llm_parse_failure_count`, `llm_timeout_count`, `llm_rejected_count`) let operators diagnose a
run without re-reading `verdicts.jsonl`. There is no `no_source_count`
or `by_source_basis` — with full-text the only basis, a per-basis
breakdown carries no information. `criterion` records the predicate
instructions sent (the `decision` block's `instructions` followed by its
true and false criteria), so the question is readable without re-loading
the criteria YAML.

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
3. Otherwise send the rendered text as one decision and record the verdict.

## Sentinel reasons

All sentinels set `probability=None` and `eligible=None`. Downstream consumers
join the catalogue against `verdicts.jsonl` filtering for `eligible is True`,
so sentinel records are naturally excluded from the active work set.

| Reason | Trigger |
|---|---|
| `tei-parse-failure` | `sections()` raises `lxml.etree.XMLSyntaxError`, or renders empty. A paper with a PDF but unreadable TEI is an operator-visible bug, unlike a plain absence of full text (which is silently skipped). |
| `llm-parse-failure` | A 2xx body without a probability in [0, 1] for the answer named `eligible`. |
| `llm-timeout` | Transport errors, HTTP 5xx or 429 on every attempt (four, with a growing backoff; each request times out after 300 s). |
| `llm-rejected` | The endpoint refused the request with another 4xx, for example a full text longer than the model's context. `raw_response` carries the error body. |

HTTP 401, 402, 403 and 404 fail every work the same way, so the stage
exits naming the decisions URL.

There is no `no-source` sentinel: a work without full text is simply not
processed, not flagged.

## Surfacing TEI to the LLM

[`laglitsynth.fulltext_eligibility.prompts.render_fulltext`](../src/laglitsynth/fulltext_eligibility/prompts.py)
walks `tei.sections()` depth-first. Each section contributes a block
whose first line is the title (when present) followed by its paragraphs
(one per line); nested children contribute further blocks. Blocks are
joined by blank lines. Figures and bibliography are dropped — the
criteria are answered from body text. Empty `sections()` returns the
empty string, which the caller records as a `tei-parse-failure`
sentinel (there is no abstract fallback).

## CLI interface

```
laglitsynth fulltext-eligibility \
    [--catalogue data/catalogue-dedup/deduplicated.jsonl] \
    [--screening-verdicts data/screening-abstracts/<run-id>/verdicts.jsonl] \
    [--screening-threshold 50] \
    [--extractions data/fulltext-extraction/extraction.jsonl] \
    [--extraction-output-dir data/fulltext-extraction/] \
    [--data-dir data/] [--run-id <iso>_<12hex>] \
    [--eligibility-criteria examples/eligibility-criteria/lagrangian-oceanography.yaml] \
    [--config <run-dir>/config.yaml] \
    [--skip-existing] [--max-records N] [--dry-run] \
    --base-url URL --model MODEL [--api-key KEY] [--decisions-url URL] \
    [--concurrency 1]
```

The resolved output directory is `<data-dir>/fulltext-eligibility/<run-id>/`.
The three input flags default to the paths shown under `--data-dir`; an
omitted flag whose default does not exist exits naming the upstream stage
to run (see [run-manifest.md](run-manifest.md)).

### Arguments

- `--catalogue`: the deduplicated catalogue (`Work` records from stage 2).
- `--screening-verdicts`: the stage 3 verdict sidecar
  (`ScreeningVerdict` records). The stage joins this against the catalogue
  at the `--screening-threshold` cutoff to determine which works to assess.
  The default path uses the resolved `--run-id`.
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
- `--run-id`: run identifier, shared with the screening run whose verdicts
  are read. Default: the `run_id` in `<data-dir>/manifest.json`; the stage
  exits when neither is available.
- `--eligibility-criteria`: path to the YAML carrying the `decision`
  block (default:
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
  any downstream analysis.
- `--max-records`: process only the first N works from the catalogue.
- `--dry-run`: print verdicts to stderr without writing any output.
- `--base-url`, `--model`: the OpenAI-compatible base URL (including
  `/v1`) and the decisions model. Both required. The full rendered text is sent; a
  text longer than the model's context is the endpoint's to reject, which
  gives `llm-rejected`.
- `--api-key`: the key; falls back to `LLM_API_KEY` in the environment,
  then in `.env`. Never written to `config.yaml`.
- `--decisions-url`: the decisions endpoint (default:
  `<base-url>/decisions`).
- `--concurrency`: number of in-flight LLM requests (default: `1`).
  See [llm-concurrency.md](llm-concurrency.md).

## Request

The question is loaded at runtime from the `decision` block of the YAML
pointed at by `--eligibility-criteria`; a file without one exits before
any request, naming the file. One `POST` per work to the decisions URL,
in OpenAI's Decisions shape:

```json
{
  "model": "<model>",
  "input": "<rendered full text>",
  "questions": [
    {"type": "predicate", "name": "eligible",
     "instructions": "<instructions>\n\nTrue if: <true>\nFalse if: <false>"}
  ]
}
```

The probability is read from the answer named `eligible`.
