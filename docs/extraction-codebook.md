# Extraction codebook

One LLM pass per paper that fills a structured codebook record
capturing numerical choices, reproducibility indicators, and
sub-discipline tags. The stage joins the deduplicated catalogue against
the stage 7 eligibility verdict sidecar to determine eligible works,
prefers the full text, falls back to the abstract, and emits a record
sidecar in the same flag-don't-filter shape as [screening](screening-abstracts.md)
and [eligibility](eligibility.md).

This is the first real run of phase 2 of the codebook: a best-guess
schema populated by the LLM against real papers. Records feed stage 9
(human adjudication) and stages 10–12 (synthesis). Schema churn is
expected once phase 3 human review begins.

## Prototype scope

A single LLM call per paper over the flattened body (or the abstract
for abstract-only works). Same flag-don't-filter pattern as
[eligibility](eligibility.md): structured JSON output, Pydantic
validation, one record per input work, sentinel `reason` for skips.

The seed codebook fields — identification, numerical choices,
reproducibility indicators, extraction metadata — are defined in
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
deferred alternative: it becomes targeted work once phase 3 review
identifies which fields are worst-hit. The prototype uses a single
pass so end-to-end flow through stages 10–12 exists before optimising
any one component.

## Input

The stage consumes:

- The deduplicated catalogue
  ([`Work`](../src/laglitsynth/catalogue_fetch/models.py) records
  from [stage 2](catalogue-dedup.md)).
- The stage 7 eligibility verdict sidecar
  ([`EligibilityVerdict`](../src/laglitsynth/fulltext_eligibility/models.py)
  records). The stage joins this against the catalogue to obtain the
  eligible work set; the full `Work` record is retained to support the
  abstract fallback path.
- The extraction JSONL
  ([`ExtractedDocument`](../src/laglitsynth/fulltext_extraction/models.py)
  records from [stage 6](fulltext-extraction.md)).

Works with a usable `ExtractedDocument` are extracted on their full
text. Works without one fall back to the abstract. Works with neither
are recorded with a sentinel `reason` and no LLM call.

## Data model

### ExtractionRecord (built dynamically)

One per catalogue work reaching this stage. Every input work produces
exactly one record, successes and failures alike — `records.jsonl` is
the complete run record. See [codebook.md](codebook.md) for the field
list and
[`build_record_model`](../src/laglitsynth/extraction_codebook/codebook.py)
for the runtime construction (it composes the identification block on
top of the codebook-driven payload model via
`pydantic.create_model`).

The record carries an identification block (`work_id`, `source_basis`,
`reason`, `seed`, `truncated`, `raw_response`) plus the content fields
from the loaded codebook. Every content value is paired with a
`*_context: str | None` verbatim snippet (unless the YAML sets
`context: false` on that field). Sentinel records have `reason` set
and every content field `None`; a successful record has `reason=None`.

A successful-but-truncated record has `reason=None` and
`truncated=True`: the LLM answered, but on a shortened body. Abstract
and sentinel records are never truncated.

`raw_response` carries the LLM's message text before parsing. Set on
successful records and on `llm-parse-failure` sentinels so an operator
can see what the model actually said; `None` on sentinels emitted
without an LLM call (`no-source`, `tei-parse-failure`).

### ExtractionCodebookMeta

```python
class ExtractionCodebookMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta
    llm: LlmMeta
    input_catalogue: str
    input_eligibility_verdicts: str
    input_extractions: str
    input_count: int
    full_text_count: int
    abstract_only_count: int
    skipped_count: int          # no-source + tei-parse-failure
    llm_parse_failure_count: int
    llm_timeout_count: int
    truncated_count: int
    by_source_basis: dict[str, int]
```

`run` and `llm` are the shared reproducibility nests from
[`src/laglitsynth/models.py`](../src/laglitsynth/models.py); they
carry `tool`, `tool_version`, `run_at`, `validation_skipped`, `model`,
`temperature`, and `prompt_sha256`. `prompt_sha256` covers the
rendered codebook system prompt, `USER_TEMPLATE`, the Ollama `num_ctx`
setting, and `CHAR_BUDGET`, so any prompt, codebook, context-window,
or truncation-budget change shifts the hash.

## Storage layout

```
<data-dir>/extraction-codebook/<run-id>/
  records.jsonl                          # one ExtractionRecord per input work
  extraction-codebook-meta.json          # ExtractionCodebookMeta
  config.yaml                            # resolved CLI+config, codebook inlined
```

See [configs.md](configs.md) for the run-id directory model and
`config.yaml` semantics. Filename note: stage 6 already writes
`extraction.jsonl` (different directory, different schema). This
stage uses `records.jsonl` to match the conceptual record name and to
avoid the collision. There is no derived convenience file; stage 9
and stages 10–12 read `records.jsonl` directly.

## Fallback cascade

For each input work, in catalogue order:

1. If an `ExtractedDocument` exists and its
   [`sections()`](../src/laglitsynth/fulltext_extraction/tei.py) is
   non-empty, render the full text and take the `full_text` branch.
2. Else, if `work.abstract` is non-empty, render the abstract and
   take the `abstract_only` branch.
3. Else, record `source_basis="none"`, `reason="no-source"`,
   every content field `None`. No LLM call.

Malformed TEI (`sections()` raises `lxml.etree.XMLSyntaxError`) is
recorded as `reason="tei-parse-failure"` with `source_basis="full_text"`
and no abstract fallback — same operator-visible pattern as
[eligibility](eligibility.md).

## Sentinel reasons

All sentinels set every content field `None` and `seed=None`.

| Reason | `source_basis` | Trigger |
|---|---|---|
| `no-source` | `none` | No `ExtractedDocument`, empty TEI body, and no abstract. |
| `tei-parse-failure` | `full_text` | `sections()` raises `lxml.etree.XMLSyntaxError`. No abstract fallback — a malformed TEI is an operator-visible bug. |
| `llm-parse-failure` | whichever branch called the LLM | The LLM response did not validate against the payload schema. |
| `llm-timeout` | whichever branch called the LLM | The OpenAI client raised `APITimeoutError` / `APIConnectionError` after all retries exhausted. The OpenAI client is constructed with `timeout=600s` and `max_retries=3` so a single hang in a long extraction does not kill the stage. |

Empty-body TEI (valid XML, no content) returns `[]` from `sections()`
and falls back to the abstract per step 2.

## `source_basis` semantics

`source_basis` records which input the LLM actually saw:

- `full_text` — the flattened TEI body.
- `abstract_only` — the abstract string from the `Work` record.
- `none` — no LLM call; `reason="no-source"` records this branch.

Abstract-only records are honestly sparse. The abstract rarely
contains integration scheme, time step, interpolation, or code
availability; most numerical fields on those records will legitimately
be `None`. Downstream synthesis (stage 10) must filter on
`source_basis` before reporting completeness — an `abstract_only`
record with everything `None` is expected, not a failure.

## Surfacing TEI to the LLM

[`render_fulltext`](../src/laglitsynth/extraction_codebook/prompts.py)
walks `tei.sections()` depth-first. Each section contributes a block
whose first line is the title (when present) followed by its paragraphs
(one per line); nested children contribute further blocks. Blocks are
joined by blank lines. Figures and bibliography are dropped — they are
not load-bearing for the codebook fields.

Unlike stage 7, stage 8 truncates at a module-level `CHAR_BUDGET` to
keep the prompt within the LLM context window. Truncation prefers
paragraph boundaries: the last paragraph that would push the body over
budget is dropped whole rather than cut mid-sentence. The returned
`truncated` flag is carried on the record.

`CHAR_BUDGET` is a tuning placeholder at ship time; it will be tuned on
the first smoke run against real papers. Two-pass retrieval remains
the principled solution once phase 3 identifies which fields suffer
from attention dilution.

## CLI interface

```
laglitsynth extraction-codebook \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --eligibility-verdicts data/fulltext-eligibility/<run-id>/verdicts.jsonl \
    --extractions data/fulltext-extraction/extraction.jsonl \
    [--extraction-output-dir data/fulltext-extraction/] \
    [--data-dir data/] [--run-id <iso>_<12hex>] \
    [--codebook examples/codebooks/lagrangian-oceanography.yaml] \
    [--config <run-dir>/config.yaml] \
    [--skip-existing] [--max-records N] [--dry-run] \
    [--model llama3.1:8b] [--base-url http://localhost:11434]
```

The resolved output directory is `<data-dir>/extraction-codebook/<run-id>/`.

### Arguments

- `--catalogue`: the deduplicated catalogue (`Work` records from stage 2).
  Required for the abstract fallback path: when TEI is missing, stage 8
  falls back to `Work.abstract`, so the full `Work` record is needed.
- `--eligibility-verdicts`: the stage 7 verdict sidecar (`EligibilityVerdict`
  records). The stage joins this against the catalogue to build the eligible
  work set. Path pattern: `data/fulltext-eligibility/<run-id>/verdicts.jsonl`.
- `--extractions`: the extraction JSONL (`ExtractedDocument` records).
  Works without a matching record fall back to the abstract.
- `--extraction-output-dir`: directory that
  `ExtractedDocument.tei_path` is relative to. Defaults to the parent
  of `--extractions`. See
  [`tei-wrapper`](../plans/done/tei-wrapper.md) for why the path is
  stored relative.
- `--data-dir`: bucket root for stage outputs (default: `data/`).
- `--run-id`: run identifier. Default: a generated `<iso>_<12hex>`.
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
  produce, the run aborts with an error — mixing records from
  different prompt versions in one file would silently corrupt any
  downstream analysis. Run-id'd dirs are fresh by default, so this
  flag is meaningful only when paired with an explicit
  `--run-id <existing>`.
- `--max-records`: process only the first N works from the eligible
  catalogue.
- `--dry-run`: print summaries to stderr without writing any output.
- `--model`, `--base-url`: Ollama configuration. `--base-url` is
  checked at startup with the same preflight pattern as
  [eligibility](eligibility.md).

### Model sizing

The CLI default is `llama3.1:8b`. Smaller models (e.g. `gemma3:4b`)
return empty JSON on the full codebook payload — pick `--model gemma3:4b`
only for testing.

Pick the model once and carry `--model` through to all stage 8
invocations; the `prompt_sha256` covers `num_ctx` + `CHAR_BUDGET` but
not the model tag, so model identity is only recorded via
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
  # ... 13 more paired fields ...
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

User: <source_basis>:
<rendered text>
```

`response_format={"type": "json_object"}`, `temperature=0.8`, per-call
random seed recorded on the record. Same shape as
[eligibility](eligibility.md).

## Changing the codebook

Edit the YAML and re-run. The dynamically-built record class enforces
`extra="forbid"` so old `records.jsonl` files fail to load after a
schema change. Policy: start a fresh run dir (which `--run-id` does by
default) and re-run. This is green-field prototyping — there is no
migration path and no backwards compatibility
([AGENTS.md](../AGENTS.md)).

## What to defer

- Two-pass retrieval per [two-pass-extraction.md](two-pass-extraction.md).
  Trigger is phase 3 review flagging attention dilution on specific
  fields.
- Retry-with-error-in-prompt on `llm-parse-failure`. Cheap; add when
  failure rate warrants.
- Enum consolidation. Free text first; categories land at phase 3
  after human review clusters the LLM's actual vocabulary.
- Multi-run consensus. Single call per paper for now.
- Branching on `ExtractedDocument.extraction_status`. Malformed or
  empty TEI falls back to abstract; re-evaluate if phase 3 surfaces
  silent bad records.
