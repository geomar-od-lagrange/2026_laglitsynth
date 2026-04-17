# Extraction codebook

One LLM pass per paper that fills a structured codebook record
capturing numerical choices, reproducibility indicators, and
sub-discipline tags. The stage joins [`eligible.jsonl`](eligibility.md)
from stage 7 against stage 6's extractions, prefers the full text,
falls back to the abstract, and emits a record sidecar in the same
flag-don't-filter shape as [screening](screening-abstracts.md) and
[eligibility](eligibility.md).

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
list and is not duplicated here. The Pydantic
[`ExtractionRecord`](../src/laglitsynth/extraction_codebook/models.py)
is a direct translation: every value field is paired with a `*_context`
verbatim snippet, every content field is `str | None`, and premature
enumeration is avoided on purpose.

The [two-pass extraction](two-pass-extraction.md) pattern is the
deferred alternative: it becomes targeted work once phase 3 review
identifies which fields are worst-hit. The prototype uses a single
pass so end-to-end flow through stages 10–12 exists before optimising
any one component.

## Input

The stage consumes:

- The eligibility-filtered catalogue
  ([`Work`](../src/laglitsynth/catalogue_fetch/models.py) records
  produced by [stage 7](eligibility.md) as `eligible.jsonl`).
- The extraction JSONL
  ([`ExtractedDocument`](../src/laglitsynth/fulltext_extraction/models.py)
  records from [stage 6](fulltext-extraction.md)).

Works with a usable `ExtractedDocument` are extracted on their full
text. Works without one fall back to the abstract. Works with neither
are recorded with a sentinel `reason` and no LLM call.

## Data model

### ExtractionRecord

One per catalogue work reaching this stage. Every input work produces
exactly one record, successes and failures alike — `records.jsonl` is
the complete run record. See
[codebook.md](codebook.md) for the field list.

The record carries an identification block (`work_id`, `source_basis`,
`reason`, `seed`, `truncated`, `raw_response`) plus the content fields
from the codebook. Every content value is paired with a
`*_context: str | None` verbatim snippet. Sentinel records have
`reason` set and every content field `None`; a successful record has
`reason=None`.

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
    run: _RunMeta
    llm: _LlmMeta
    input_catalogue: str
    input_extractions: str
    input_count: int
    full_text_count: int
    abstract_only_count: int
    skipped_count: int          # no-source + tei-parse-failure
    llm_parse_failure_count: int
    truncated_count: int
    by_source_basis: dict[str, int]
```

`run` and `llm` are the shared reproducibility nests from
[`src/laglitsynth/models.py`](../src/laglitsynth/models.py); they
carry `tool`, `tool_version`, `run_at`, `validation_skipped`, `model`,
`temperature`, and `prompt_sha256`. `prompt_sha256` covers
`SYSTEM_PROMPT`, `USER_TEMPLATE`, the Ollama `num_ctx` setting, and
`CHAR_BUDGET`, so any prompt, context-window, or truncation-budget
change shifts the hash.

## Storage layout

```
data/extraction-codebook/
  records.jsonl                         # one ExtractionRecord per input work
  extraction-codebook-meta.json         # ExtractionCodebookMeta
```

Filename note: stage 6 already writes `extraction.jsonl` (different
directory, different schema). This stage uses `records.jsonl` to match
the Pydantic model name (`ExtractionRecord`) and to avoid the
conceptual collision. There is no derived convenience file; stage 9
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
    --eligible data/fulltext-eligibility/eligible.jsonl \
    --extractions data/fulltext-extraction/extraction.jsonl \
    [--extraction-output-dir data/fulltext-extraction/] \
    [--output-dir data/extraction-codebook/] \
    [--skip-existing] [--max-records N] [--dry-run] \
    [--model gemma3:4b] [--base-url http://localhost:11434]
```

### Arguments

- `--eligible`: eligibility-filtered catalogue (`Work` records from
  [stage 7](eligibility.md)).
- `--extractions`: the extraction JSONL (`ExtractedDocument` records).
  Works without a matching record fall back to the abstract.
- `--extraction-output-dir`: directory that
  `ExtractedDocument.tei_path` is relative to. Defaults to the parent
  of `--extractions`. See
  [`tei-wrapper`](../plans/done/tei-wrapper.md) for why the path is
  stored relative.
- `--output-dir`: where to write `records.jsonl` and the meta file.
- `--skip-existing`: load any prior `records.jsonl` and skip already-
  extracted `work_id`s. New records are appended to the existing
  sidecar.
- `--max-records`: process only the first N works from the eligible
  catalogue.
- `--dry-run`: print summaries to stderr without writing any output.
- `--model`, `--base-url`: Ollama configuration. `--base-url` is
  checked at startup with the same preflight pattern as
  [eligibility](eligibility.md).

### Model sizing

The CLI default is `gemma3:4b` for consistency with stages 3 and 7,
but **gemma3:4b does not reliably handle stage 8's 30-field
structured JSON on typical paper bodies** — in smoke runs it returned
`{}` (empty object) on most full-text inputs. Pass a bigger model via
`--model`. Confirmed-working in smoke tests:

- `llama3.1:8b` — ~60s per paper, fills 7-10 of 16 value fields on
  a typical full paper.
- `qwen2.5:14b` — ~140s per paper, fills 10-12 of 16 value fields;
  better on `sub_discipline`, `code_tracking_software`, and
  `passage_locations` than `llama3.1:8b`.

Pick the model once and carry `--model` through to all stage 8
invocations; the `prompt_sha256` covers `num_ctx` + `CHAR_BUDGET` but
not the model tag, so model identity is only recorded via
`meta.llm.model`.

## LLM prompt

Hardcoded in
[`laglitsynth.extraction_codebook.prompts`](../src/laglitsynth/extraction_codebook/prompts.py);
the digest is recorded as `meta.llm.prompt_sha256`. The field block in
the system prompt is generated from
`_ExtractionPayload.model_json_schema()` so the prompt and the
validator stay in lockstep when fields are added or renamed.

```
System: You extract structured metadata from scientific papers on
computational Lagrangian methods in oceanography. Every value field
has a companion "*_context" field containing a short verbatim excerpt
from the paper supporting that value. If the paper does not state the
information, write null for both the value and its context — do not
guess or infer.

Fields:
<one-line-per-field description, generated from the pydantic model>

Respond with a single JSON object containing exactly these keys.

User: <source_basis>:
<rendered text>
```

`response_format={"type": "json_object"}`, `temperature=0.8`, per-call
random seed recorded on the record. Same shape as
[eligibility](eligibility.md).

## Regenerate on codebook change

The codebook is a hypothesis in phases 1–2 of
[codebook.md](codebook.md). When the codebook changes — fields added,
removed, split, merged — the Pydantic
[`ExtractionRecord`](../src/laglitsynth/extraction_codebook/models.py)
is regenerated to match. `extra="forbid"` makes old `records.jsonl`
fail to load after a schema change. Policy: delete the data directory
and re-run. This is green-field prototyping — there is no migration
path and no backwards compatibility ([AGENTS.md](../AGENTS.md)).

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
