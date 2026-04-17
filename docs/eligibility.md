# Eligibility

Full-text assessment of whether each included work meets the review's
inclusion criteria. Distinct from [screening](screening-abstracts.md),
which uses only title and abstract. Reading the full text may reveal
that a paper is not actually about computational Lagrangian methods, or
that it is a review article rather than primary research.

## Prototype scope

A single LLM pass over the extracted text (or the abstract for
abstract-only works). Same flag-don't-filter pattern as
[`screening-abstracts`](screening-abstracts.md): structured JSON output,
Pydantic validation, verdicts stored in a sidecar keyed by `work_id`.

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

The stage consumes two artifacts:

- The included catalogue ([`Work`](../src/laglitsynth/catalogue_fetch/models.py)
  records from [`screening-adjudication`](screening-adjudication.md)).
- The extraction JSONL ([`ExtractedDocument`](../src/laglitsynth/fulltext_extraction/models.py)
  records from [`fulltext-extraction`](fulltext-extraction.md)).

Works that have an `ExtractedDocument` with non-empty
[`sections()`](../src/laglitsynth/fulltext_extraction/tei.py) are
assessed on their full text. Works without a usable extraction fall back
to the abstract. Works without either are recorded with a sentinel
verdict and no LLM call.

## Data model

### EligibilityVerdict

One per catalogue work that reached this stage.

```python
SourceBasis = Literal["full_text", "abstract_only", "none"]


class EligibilityVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    eligible: bool | None              # None for sentinel-reason skips
    source_basis: SourceBasis
    reason: str | None                 # LLM free-text or sentinel
    seed: int | None                   # Ollama seed used; None for sentinels
```

`eligible` is tri-state. `True` and `False` are real LLM verdicts;
`None` indicates a sentinel skip — the LLM was not called or its output
could not be parsed. See [Sentinel reasons](#sentinel-reasons) below.

### EligibilityMeta

```python
class EligibilityMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: _RunMeta
    llm: _LlmMeta
    input_catalogue: str
    input_extractions: str
    input_count: int
    eligible_count: int
    excluded_count: int
    skipped_count: int
    by_source_basis: dict[str, int]
```

`run` and `llm` are the shared reproducibility nests from
[`src/laglitsynth/models.py`](../src/laglitsynth/models.py) — they
carry `tool`, `tool_version`, `run_at`, `validation_skipped`, `model`,
`temperature`, and `prompt_sha256`.

## Storage layout

```
data/fulltext-eligibility/
  verdicts.jsonl            # one EligibilityVerdict per input work
  eligible.jsonl            # Work records where verdict.eligible is True
  eligibility-meta.json     # EligibilityMeta
```

`verdicts.jsonl` is the source of truth. `eligible.jsonl` is a derived
convenience file rebuilt each run by joining the catalogue against the
verdict sidecar — same pattern as stage 4's
[`included.jsonl`](screening-adjudication.md).

## Fallback cascade

For each catalogue work, in catalogue order:

1. If an `ExtractedDocument` exists and its
   [`sections()`](../src/laglitsynth/fulltext_extraction/tei.py) is
   non-empty, render the full text and take the `full_text` branch.
2. Else, if `work.abstract` is non-empty, render the abstract and take
   the `abstract_only` branch.
3. Else, record `source_basis="none"`, `eligible=None`,
   `reason="no-source"`. No LLM call.

## Sentinel reasons

All sentinels set `eligible=None` and `seed=None`. Downstream consumers
read [`eligible.jsonl`](#storage-layout) and never see the tri-state —
sentinels are excluded from it by construction.

| Reason | Branch | Trigger |
|---|---|---|
| `no-source` | `none` | No `ExtractedDocument`, empty TEI body, and no abstract. |
| `tei-parse-failure` | `full_text` | `sections()` raises `lxml.etree.XMLSyntaxError`. No abstract fallback — a malformed TEI is an operator-visible bug. |
| `llm-parse-failure` | whichever branch called the LLM | The LLM returned output that could not be parsed into `{"eligible": bool, "reason": str}`. |

Empty-body TEI (valid XML, no content) returns `[]` from `sections()`
and falls back to the abstract per step 2 — extraction succeeded, just
produced nothing extractable.

## Surfacing TEI to the LLM

[`laglitsynth.fulltext_eligibility.prompts.render_fulltext`](../src/laglitsynth/fulltext_eligibility/prompts.py)
walks `tei.sections()` depth-first. Each section contributes a block
whose first line is the title (when present) followed by its paragraphs
(one per line); nested children contribute further blocks. Blocks are
joined by blank lines. Figures and bibliography are dropped — the three
criteria are answered from body text. Empty `sections()` returns the
empty string, which the caller treats as a signal to fall back to the
abstract.

## CLI interface

```
laglitsynth fulltext-eligibility \
    --catalogue data/screening-adjudication/included.jsonl \
    --extractions data/fulltext-extraction/extraction.jsonl \
    [--extraction-output-dir data/fulltext-extraction/] \
    [--output-dir data/fulltext-eligibility/] \
    [--skip-existing] [--max-records N] [--dry-run] \
    [--model gemma3:4b] [--base-url http://localhost:11434]
```

### Arguments

- `--catalogue`: the included catalogue (`Work` records).
- `--extractions`: the extraction JSONL (`ExtractedDocument` records).
  Works without a matching record fall back to the abstract.
- `--extraction-output-dir`: directory that
  `ExtractedDocument.tei_path` is relative to. Defaults to the parent
  of `--extractions`. See
  [`tei-wrapper`](../plans/done/tei-wrapper.md) for why the path is
  stored relative.
- `--output-dir`: where to write verdicts, `eligible.jsonl`, and the
  meta file.
- `--skip-existing`: load any prior `verdicts.jsonl` and skip already-
  assessed `work_id`s. The per-work verdict sidecar is appended to;
  `eligible.jsonl` is regenerated from the union.
- `--max-records`: process only the first N works from the catalogue.
- `--dry-run`: print verdicts to stderr without writing any output.
- `--model`, `--base-url`: Ollama configuration. `--base-url` is checked
  at startup with the same preflight pattern as
  [`screening-abstracts`](screening-abstracts.md).

## LLM prompt

Hardcoded in
[`laglitsynth.fulltext_eligibility.prompts`](../src/laglitsynth/fulltext_eligibility/prompts.py);
the digest is recorded as `meta.llm.prompt_sha256`.

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

User: <source_basis>:
<rendered text>
```

`response_format={"type": "json_object"}`, `temperature=0.8`, per-call
random seed recorded on the verdict. Same shape as
[`screening-abstracts`](screening-abstracts.md).

## What to defer

- Fine-grained exclusion reasons (enum of why a paper was excluded).
- Per-criterion verdicts (three booleans).
- Calibration against human judgments.
- Threshold-based re-eligibility (currently binary).
- Chunking or retrieval over long papers; prompt length policy is
  addressed as a follow-up in the stage 7 plan.
