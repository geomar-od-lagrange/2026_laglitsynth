# Extraction codebook

One LLM pass per paper that fills a structured codebook record
capturing numerical choices, reproducibility indicators, and
sub-discipline tags. The stage joins the corpus works against the
stage 7 eligibility verdict sidecar to determine eligible works,
extracts from the full text, and emits a record sidecar in the same
flag-don't-filter shape as [screening](screening-abstracts.md) and
[eligibility](eligibility.md). Like stage 7, it is **full-text-only**:
an eligible work without a usable full-text extraction is not processed
(no record row, no count), because the codebook fields turn on
numerical-method detail abstracts do not carry.

This is phase 2 of the [codebook](codebook.md): a best-guess schema
populated by the LLM against real papers. Records feed the planned
synthesis step. Schema churn is expected in phase 3 human review.

## Prerequisites

An OpenAI-compatible chat endpoint and its key as `LLM_API_KEY`; see
[external-services.md](external-services.md#llm-endpoint).

## Scope

A single LLM call per paper over the flattened full-text body. Same
flag-don't-filter pattern as [eligibility](eligibility.md): structured
JSON output, Pydantic validation, one record per processed work,
sentinel `reason` for skips.

The seed codebook fields — identification, numerical choices,
reproducibility indicators, rationale, extraction metadata — are defined in
[codebook.md](codebook.md). That document is the authoritative field
list and is not duplicated here. The shipped YAML transcription lives
at
[`examples/codebooks/lagrangian-oceanography.yaml`](../examples/codebooks/lagrangian-oceanography.yaml);
the Pydantic record class is built dynamically at startup by
[`load_codebook` + `build_record_model`](../src/laglitsynth/extraction_codebook/codebook.py).
Every value field is paired with a `*_context` verbatim snippet,
every content field is `str | None`, and premature enumeration is
avoided on purpose.

The [two-pass extraction](two-pass-extraction.md) pattern is the
alternative for papers or fields a single pass handles badly; phase 3
review and the `llm-rejected` count show whether it is needed.

## Input

The stage consumes:

- The corpus written by `corpus-assemble`: its works
  (`<corpus-dir>/works.jsonl`), the PDF hashes in `provenance.jsonl`, and
  `corpus_id` from `corpus-meta.json`.
- The stage 7 eligibility verdict sidecar
  ([`EligibilityVerdict`](../src/laglitsynth/fulltext_eligibility/models.py)
  records). The stage joins it against the corpus works to obtain the
  eligible work set and exits when a verdict names a work outside the
  corpus. It also exits when the `eligibility-meta.json` beside the
  verdicts is missing, names another `corpus_id`, or records another
  `prompt_sha256` than
  [`criteria_sha256`](../src/laglitsynth/fulltext_eligibility/eligibility.py)
  of `--eligibility-criteria`.
- The extraction JSONL
  ([`ExtractedDocument`](../src/laglitsynth/fulltext_extraction/models.py)
  records from [stage 6](fulltext-extraction.md)), checked as stage 7
  checks it: the stage exits when `extraction-meta.json` is missing or
  names another `corpus_id`, and ignores a record whose `pdf_sha256`
  differs from the corpus copy.

Only eligible works with a current `ExtractedDocument` are processed:
the eligible set is gated on extraction presence, so a work without full
text never enters the loop, produces no record row, and is not counted.
A work whose extraction parses but renders empty TEI is recorded with a
`tei-parse-failure` sentinel.

## Data model

### ExtractionRecord (built dynamically)

One per corpus work reaching this stage. Every input work produces
exactly one record, successes and failures alike — `records.jsonl` is
the complete run record. See [codebook.md](codebook.md) for the field
list and
[`build_record_model`](../src/laglitsynth/extraction_codebook/codebook.py)
for the runtime construction (it composes the identification block on
top of the codebook-driven payload model via
`pydantic.create_model`).

The record carries an identification block (`work_id`, `reason`,
`raw_response`) plus the content fields from the
loaded codebook. Because every record is full-text-based, there is no
`source_basis` field — the basis is implicit. Every content value is
paired with a `*_context: str | None` verbatim snippet (unless the YAML
sets `context: false` on that field). Sentinel records have `reason`
set and every content field `None`; a successful record has
`reason=None`.

Every content field is `str | None`, so the LLM's natural-JSON answers
are coerced before validation (`_coerce_llm_types` in
[`codebook.py`](../src/laglitsynth/extraction_codebook/codebook.py)): a
JSON boolean becomes the literal string `"yes"` or `"no"`, a JSON list
is joined with `" / "` (an empty list becomes `None`), and any other
non-string value is stringified. `None` passes through unchanged. This
flattens common type mismatches rather than failing the whole record.

`raw_response` carries the LLM's message text before parsing. Set on
successful records, on `llm-parse-failure` sentinels (the message) and on
`llm-rejected` sentinels (the error body) so an operator can see what the
endpoint actually said; `None` on `tei-parse-failure` and `llm-timeout`.

### ExtractionCodebookMeta

```python
class ExtractionCodebookMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    llm: LlmMeta
    corpus_id: str                   # CorpusMeta.corpus_id of the corpus read
    eligibility_prompt_sha256: str   # prompt_sha256 of the eligibility verdicts read
    input_eligibility_verdicts: str
    input_extractions: str
    input_count: int
    full_text_count: int
    skipped_count: int          # tei-parse-failure only
    llm_parse_failure_count: int
    llm_timeout_count: int
    llm_rejected_count: int
```

`run` and `llm` are the shared reproducibility nests from
[`src/laglitsynth/models.py`](../src/laglitsynth/models.py); they
carry `tool`, `tool_version`, `run_at`, `validation_skipped`, `model`,
`base_url`, `decisions_url` (`None` here), `reasoning_effort` (the
`--reasoning-effort` sent, `None` when none was), and `prompt_sha256`. `prompt_sha256` covers the
rendered codebook system prompt and `USER_TEMPLATE`, so any prompt or
codebook change shifts the hash.

`input_count` is the number of works that reached the stage (eligible
and with a full-text extraction). There is no `abstract_only_count` or
`by_source_basis` — full text is the only basis. The review export
embeds the criterion by reading it straight from the codebook spec
(`ctx.system_prompt`), which it loads anyway to reconstruct the record
model, so the meta does not duplicate it.

## Storage layout

```
<data-dir>/extraction-codebook/<run-id>/
  records.jsonl                          # one ExtractionRecord per input work
  extraction-codebook-meta.json          # ExtractionCodebookMeta
  config.yaml                            # resolved CLI+config, codebook and
                                         # eligibility criteria inlined
```

See [configs.md](configs.md) for the run-id directory model and
`config.yaml` semantics. Filename note: stage 6 already writes
`extraction.jsonl` (different directory, different schema). This
stage uses `records.jsonl` to match the conceptual record name and to
avoid the collision. There is no derived convenience file; the review
export and the planned synthesis read `records.jsonl` directly.

## Per-work flow

The eligible set is gated on extraction presence before the loop, so
each work reaching `_extract_one` has an `ExtractedDocument`. For each
such work, in verdict order:

1. Render the TEI. If it raises `lxml.etree.XMLSyntaxError`, record a
   `tei-parse-failure` sentinel (no LLM call).
2. If the render is empty (valid XML, no body content), record a
   `tei-parse-failure` sentinel (no LLM call).
3. Otherwise call the LLM and record the result.

## Sentinel reasons

All sentinels set every content field `None`.

| Reason | Trigger |
|---|---|
| `tei-parse-failure` | `sections()` raises `lxml.etree.XMLSyntaxError`, or renders empty. A paper with a PDF but unreadable TEI is an operator-visible bug, unlike a plain absence of full text (which is silently skipped). |
| `llm-parse-failure` | The LLM response did not validate against the payload schema. |
| `llm-timeout` | The OpenAI client raised `APITimeoutError`, `APIConnectionError`, `RateLimitError` or `InternalServerError` after all retries exhausted. The client is constructed with `timeout=600s` and `max_retries=3` so a single hang in a long extraction does not kill the stage. |
| `llm-rejected` | The endpoint refused the request (`BadRequestError`, or another 4xx that is not fatal), for example a full text longer than the model's context. `raw_response` carries the error body. |

`AuthenticationError`, `PermissionDeniedError`, `NotFoundError` and a 402
fail every work the same way, so the stage exits naming the base URL.

There is no `no-source` sentinel: an eligible work without full text is
simply not processed, not flagged.

## Surfacing TEI to the LLM

[`render_fulltext`](../src/laglitsynth/extraction_codebook/prompts.py)
walks `tei.sections()` depth-first. Each section contributes a block
whose first line is the title (when present) followed by its paragraphs
(one per line); nested children contribute further blocks. Blocks are
joined by blank lines. Figures and bibliography are dropped — they are
not load-bearing for the codebook fields.

As in stage 7, the whole rendered text is sent; nothing truncates it. A
text longer than the model's context is the server's to reject. Two-pass
retrieval remains the principled solution for very long papers once
phase 3 identifies which fields suffer from attention dilution.

## CLI interface

```
laglitsynth extraction-codebook \
    [--corpus-dir data/corpus/] \
    [--eligibility-verdicts data/fulltext-eligibility/<run-id>/verdicts.jsonl] \
    [--eligibility-criteria examples/eligibility-criteria/lagrangian-oceanography.yaml] \
    [--extractions data/fulltext-extraction/extraction.jsonl] \
    [--extraction-output-dir data/fulltext-extraction/] \
    [--data-dir data/] [--run-id <iso>_<12hex>] \
    [--codebook examples/codebooks/lagrangian-oceanography.yaml] \
    [--config <run-dir>/config.yaml] \
    [--skip-existing] [--max-records N] [--dry-run] \
    --base-url URL --model MODEL [--api-key KEY] \
    [--reasoning-effort {low,medium,high}] [--concurrency 1]
```

The resolved output directory is `<data-dir>/extraction-codebook/<run-id>/`.
The three input flags default to the paths shown under `--data-dir`; an
omitted flag whose default does not exist exits naming the upstream stage
to run (see [run-manifest.md](run-manifest.md)).

### Arguments

- `--corpus-dir`: the corpus written by `corpus-assemble` (default:
  `<data-dir>/corpus/`). Every eligibility verdict must name one of its
  works.
- `--eligibility-verdicts`: the stage 7 verdict sidecar (`EligibilityVerdict`
  records), with `eligibility-meta.json` beside it. The stage joins it
  against the corpus works to build the eligible work set. The default
  path uses the resolved `--run-id`.
- `--eligibility-criteria`: the eligibility-criteria YAML the verdicts were
  made with (default:
  `examples/eligibility-criteria/lagrangian-oceanography.yaml`). Its
  `criteria_sha256` must equal `prompt_sha256` in `eligibility-meta.json`.
  Inlined into `config.yaml` on save, like the codebook.
- `--extractions`: the extraction JSONL (`ExtractedDocument` records),
  with `extraction-meta.json` beside it. Eligible works without a current
  record are skipped (not processed, not counted).
- `--extraction-output-dir`: directory that
  `ExtractedDocument.tei_path` is relative to. Defaults to the parent
  of `--extractions`. See
  [`tei-wrapper`](../plans/done/tei-wrapper.md) for why the path is
  stored relative.
- `--data-dir`: bucket root for stage outputs (default: `data/`).
- `--run-id`: run identifier, shared with the eligibility run whose verdicts
  are read. Default: `analysis_run_id` in `<corpus-dir>/corpus-meta.json`.
- `--codebook`: path to the codebook YAML carrying the LLM system
  prompt and the field list. Default:
  `examples/codebooks/lagrangian-oceanography.yaml`. The codebook
  drives both the prompt and the dynamically-built payload validator —
  adding/removing/renaming fields is a YAML edit. The codebook
  contents are inlined into `config.yaml` on save so a run dir is a
  self-contained run snapshot. See [configs.md](configs.md) and the
  codebook-YAML format described below.
- `--config`: optional YAML config file whose values seed argparse
  defaults; explicit CLI flags still win. See [configs.md](configs.md).
- `--skip-existing`: load any prior `records.jsonl` (in the run dir
  pointed at by `--run-id`) and skip already-extracted `work_id`s. New
  records are appended to the existing sidecar. If
  `extraction-codebook-meta.json` already exists and its recorded
  `prompt_sha256` differs from the hash the current invocation would
  produce, or its `corpus_id` differs from the current corpus, the run
  aborts with an error — mixing records from different prompt versions or
  corpora in one file would silently corrupt any downstream analysis.
- `--max-records`: process only the first N eligible works.
- `--dry-run`: print summaries to stderr without writing any output.
- `--base-url`, `--model`: the OpenAI-compatible base URL (including
  `/v1`) and the chat model. Both required.
- `--api-key`: the key; falls back to `LLM_API_KEY` in the environment,
  then in `.env`. Never written to `config.yaml`.
- `--reasoning-effort`: `low`, `medium` or `high`, sent as the chat
  request's `reasoning_effort` and recorded in `meta.llm`. Unset (the
  default), no effort is sent and the provider's default applies.
- `--concurrency`: number of in-flight LLM requests (default: `1`).
  See [interfaces.md](interfaces.md#llm-concurrency).

### Model sizing

`--model` has no default. Small models can return empty JSON on the full
codebook payload.

Pick the model once and carry `--model` through to all stage 8
invocations; the `prompt_sha256` covers the prompt but not the model
tag, so model identity is only recorded via
`meta.llm.model`.

## Codebook YAML

Codebooks are data-driven. The YAML carries the system prompt and the
field list together so stage 8 has one config-file domain artifact:

```yaml
id: lagrangian-oceanography
description: Codebook for computational Lagrangian methods in oceanography.
system_prompt: |-
  You extract structured metadata from scientific papers on computational
  Lagrangian methods in oceanography. ...
  Fields:
  {fields}

  Respond with a single JSON object containing exactly these keys.
fields:
  - name: sub_discipline
    description: >-
      Sub-discipline tag. Free text — use the paper's own phrasing ...
    context_description: Short verbatim excerpt from the paper supporting the sub_discipline tag.
  # ... 14 more paired fields, rationale among them ...
  - name: in_text_locations
    description: >-
      Where in THIS PAPER the extracted values were found ...
    context: false
  - name: extraction_notes
    description: >-
      What was ambiguous, surprising, or hard to classify ...
    context: false
```

`context: true` is the default. Each named field gets a paired
`<name>_context: str | None` automatically; `context_description`
overrides the auto-generated paired-field description. `context: false`
skips the pair entirely (used for `in_text_locations` and
`extraction_notes`).

The system prompt's `{fields}` placeholder is substituted at runtime
with the rendered field list (one
`- "<name>": <description>` line per field, in YAML order). The
runtime payload-validation pydantic model is built from `fields` via
`pydantic.create_model()` — adding/removing/renaming fields is a YAML
edit, not a code change.

## LLM prompt

Loaded at runtime from the codebook YAML pointed at by `--codebook`;
the digest is recorded as `meta.llm.prompt_sha256`.

```
System: <spec.system_prompt with {fields} substituted>

User: full_text:
<rendered text>
```

The request carries `model`, `messages` and
`response_format={"type": "json_object"}` only: no temperature or seed is
sent, so sampling is the endpoint's default and two runs with the same
prompt can differ record by record.

## Changing the codebook

Edit the YAML and re-run. The dynamically-built record class enforces
`extra="forbid"` so old `records.jsonl` files fail to load after a
schema change. Policy: start a fresh run dir (which `--run-id` does by
default) and re-run. This is green-field prototyping — there is no
migration path and no backwards compatibility
([AGENTS.md](../AGENTS.md)).

## Review export

`extraction-codebook-export` writes an XLSX review workbook
([`export.py`](../src/laglitsynth/extraction_codebook/export.py)) for
human spot-checking the extraction stage and tuning its codebook /
prompt. It is XLSX-only — the JSONL sidecar is the machine-readable
form. The workbook has an `Index` sheet and one tab per sampled work.
The `Index` opens with three reviewer-identity rows
(`reviewer_name` / `reviewer_email` / `review_date`, with placeholder
values to fill in) above a table with one row per sampled work
(bibliographic columns plus a hyperlink to the per-work tab, as a light
navigation aid). The per-work tab is the working surface: a back-to-Index
link, a bibliographic block, the embedded criterion, then one row per
codebook field with columns `field | value | context`, a
`reviewer_remarks` row, and
finally a collapsed LLM-meta block (reason, model, base_url,
prompt_sha256, raw_response — hidden behind an outline toggle). The field list is driven off the codebook
(the record model is codebook-built), so a non-default codebook's field
set follows automatically — nothing is hard-coded. The criterion is read
from the `--codebook` spec itself (the export loads it anyway to
reconstruct the record model); `extraction-codebook-meta.json` supplies
only the LLM fingerprint.

Because the record model is built dynamically from the codebook, the
export takes a `--codebook` argument to reconstruct it (defaulting to
the shipped codebook); the reviewer must pass the same codebook the run
used.

```
laglitsynth extraction-codebook-export \
    --records data/extraction-codebook/<run-id>/records.jsonl \
    [--catalogue data/corpus/works.jsonl] \
    [--codebook examples/codebooks/lagrangian-oceanography.yaml] \
    [--meta data/extraction-codebook/<run-id>/extraction-codebook-meta.json] \
    [--output <records parent>/review.xlsx] \
    [--n-subset N] [--subset-seed 0]
```

`--n-subset` / `--subset-seed` draw a reproducible random sample (the
intended workflow is digging into a subset and generalizing, not a wide
all-works scan); unset or `>=` the record count emits the full set in
record order. A record whose `work_id` is absent from the catalogue
raises a `ValueError` naming the offending id.

## Not done here

- Two-pass retrieval ([two-pass-extraction.md](two-pass-extraction.md)).
  The whole rendered text goes into one call.
- Retry with the validation error in the prompt. An unparseable response
  is recorded as `llm-parse-failure`.
- Enum consolidation. Every field is free text; categories come out of
  phase 3 review of the LLM's actual vocabulary.
- An extraction quality gate. Malformed or empty TEI records a
  `tei-parse-failure` sentinel; there is no other check on the TEI.
