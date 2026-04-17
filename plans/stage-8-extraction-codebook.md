# Stage 8: extraction codebook

## Goal

Implement `extraction-codebook`: one LLM pass per paper that fills a
structured codebook record capturing numerical choices, reproducibility
indicators, and sub-discipline tags as described in
[docs/codebook.md](../docs/codebook.md). The stage joins `eligible.jsonl`
from stage 7 against stage 6's extractions, prefers full text, falls
back to the abstract, and emits a record sidecar in the flag-don't-
filter shape.

This is the first real run of phase 2 of the codebook: a best-guess
schema populated by the LLM against real papers. Records feed stage 9
(human adjudication) and stages 10–12 (synthesis). Schema churn is
expected once phase 3 human review begins.

## Non-goals

- Two-pass retrieval per
  [docs/two-pass-extraction.md](../docs/two-pass-extraction.md). Single
  LLM call over the flattened body; two-pass becomes targeted work
  once phase 3 identifies which fields are worst-hit.
- Enum consolidation. All "free text → enum" fields stay `str | None`
  in the prototype — premature enumeration is the main codebook-design
  failure mode per [docs/codebook.md](../docs/codebook.md).
- Human adjudication (stage 9), multi-run consensus, chunking,
  per-field retries.
- Populating `Work.source_catalogues` / `is_peer_reviewed`, or branching
  on `ExtractedDocument.extraction_status`. Malformed or empty TEI
  falls back to abstract, same as stage 7. Re-evaluate
  `extraction_status` if phase 3 surfaces silent bad-record issues.

## Prerequisite: docs/extraction-codebook.md

`docs/extraction-codebook.md` does not exist; [roadmap.md](roadmap.md)
requires it before a plan. Implementation step 1 writes the doc — this
plan describes what goes in it, not its contents. Sections to include:

- Stage purpose, I/O, and position relative to stages 7 and 9.
- Link to [docs/codebook.md](../docs/codebook.md) as the authoritative
  field list, with the "codebook is a hypothesis in phases 1–2"
  reminder.
- Link to [docs/two-pass-extraction.md](../docs/two-pass-extraction.md)
  as the deferred pattern.
- Storage layout, CLI block, sentinel `reason` vocabulary, and
  `source_basis` semantics (including: abstract-only records will have
  most numerical fields `None`).
- The "regenerate `ExtractionRecord` and drop old data on codebook
  change" contract — green-field, no migration.

Link the new doc from [docs/pipeline.md](../docs/pipeline.md) §8 and
[README.md](../README.md).

## Target state

### Storage layout

```
data/extraction-codebook/
  records.jsonl                         # one ExtractionRecord per input work
  extraction-codebook-meta.json
```

Filename note: stage 6 already writes `extraction.jsonl` (different
directory, different schema). To avoid the conceptual collision this
stage uses `records.jsonl` — it matches the pydantic model name
(`ExtractionRecord`) and reads unambiguously alongside stage 6's files.
Every input work produces exactly one record, successes and failures.
There is no derived convenience file; stage 9 and stages 10–12 read
records directly.

### Data models

New module [src/laglitsynth/extraction_codebook/](../src/laglitsynth/)
with `__init__.py`, `models.py`, `prompts.py`, `extract.py`. Translate
the seed codebook table in [docs/codebook.md](../docs/codebook.md) into
a typed pydantic model, pairing every value field with a `*_context:
str | None` verbatim snippet.

```python
TOOL_NAME = "laglitsynth.extraction_codebook.extract"

SourceBasis = Literal["full_text", "abstract_only", "none"]


class ExtractionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Identification.
    work_id: str
    source_basis: SourceBasis
    reason: str | None              # sentinel for skips; None for normal records
    seed: int | None
    truncated: bool

    # Tagging (free-text, consolidated later).
    sub_discipline: str | None
    sub_discipline_context: str | None

    # RQ1.2 — numerical choices.
    integration_scheme: str | None
    integration_scheme_context: str | None
    time_step_strategy: str | None
    time_step_strategy_context: str | None
    time_step_value: str | None
    time_step_value_context: str | None
    interpolation_spatial: str | None
    interpolation_spatial_context: str | None
    interpolation_temporal: str | None
    interpolation_temporal_context: str | None
    diffusion_scheme: str | None
    diffusion_scheme_context: str | None
    software: str | None
    software_context: str | None
    ocean_model: str | None
    ocean_model_context: str | None

    # RQ1.1 — reproducibility.
    methods_detail: str | None
    methods_detail_context: str | None
    code_tracking_software: str | None
    code_tracking_software_context: str | None
    code_experiment_setup: str | None
    code_experiment_setup_context: str | None
    code_analysis: str | None
    code_analysis_context: str | None
    config_available: str | None
    config_available_context: str | None

    # Extraction metadata.
    passage_locations: str | None
    extraction_notes: str | None


class ExtractionCodebookMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: _RunMeta
    llm: _LlmMeta
    input_catalogue: str
    input_extractions: str
    input_count: int
    full_text_count: int
    abstract_only_count: int
    skipped_count: int                  # no-source + tei-parse-failure
    llm_parse_failure_count: int
    truncated_count: int
    by_source_basis: dict[str, int]
```

Notes:

- Every value field is `str | None`. `None` is the canonical
  "not stated" marker per [AGENTS.md](../AGENTS.md); the prompt tells
  the LLM to write `null` in that case.
- `reason` uses stage 3's sentinel vocabulary: `"no-source"`,
  `"tei-parse-failure"`, `"llm-parse-failure"`, or `None` for a normal
  record. A successful-but-truncated record has `reason=None` and
  `truncated=True`.
- `passage_locations` stays free-text for now — it becomes ground
  truth for two-pass calibration, and preserving the LLM's own phrasing
  (e.g. "Section 2.3 and Table 1") is more useful than forcing a list.
- `sub_discipline` is a single string in the prototype (tagging, not
  multi-label). Flip to list at phase 3 if the single value loses
  signal; green-field, fine to break.

### LLM prompt

Seed prompt — hardcoded in `prompts.py`, iterates via code edits,
`prompt_sha256` on the meta distinguishes versions:

```
System: You extract structured metadata from scientific papers on
computational Lagrangian methods in oceanography. You will be shown the
text of one paper and must fill a JSON object with the fields below.
Every value field has a companion "*_context" field containing a short
verbatim excerpt from the paper supporting that value. If the paper
does not state the information, write null for both the value and its
context — do not guess or infer.

Fields:
<one-line-per-field description, generated from the pydantic model>

Respond with a single JSON object containing exactly these keys.

User: <source_basis tag>:
<text>
```

The field block is generated from
`ExtractionRecord.model_json_schema()` so the prompt and validator stay
in lockstep across schema changes. `response_format={"type":
"json_object"}`, `temperature=0.8`, per-call seed — same as stage 3.

Response validation uses a narrower `_ExtractionPayload` submodel
(content fields only); the caller fills `work_id`, `source_basis`,
`reason`, `seed`, `truncated`. On `ValidationError` the record is
emitted with `reason="llm-parse-failure"`, `seed=None`, all content
`None`. No retry in the prototype.

### Surfacing TEI, fallback cascade

Same shape as stage 7. `doc.open_tei(extraction_output_dir)`, walk
`sections()` depth-first, concatenate title + paragraphs with blank-
line separation. Figure captions and bibliography are omitted.
Stage 11 will consume bibliography later.

Helper `render_fulltext(tei, *, char_budget) -> tuple[str, bool]`
returns `(text, truncated)`. If stage 7 lands a helper first, stage 8
imports or duplicates depending on the shape — stage 8's budget is
larger (more of the paper needed) so light factoring is fine; a shared
helper is a later refactor.

Per work, in catalogue order:

1. `ExtractedDocument` exists and `sections()` non-empty →
   `full_text` branch.
2. Else `work.abstract` non-empty → `abstract_only` branch.
3. Else → `reason="no-source"`, `source_basis="none"`, no LLM call.

Malformed TEI (`sections()` raises) → `reason="tei-parse-failure"`,
no abstract fallback. Same operator-visible pattern stage 7 uses.

### CLI

```
laglitsynth extraction-codebook \
    --eligible data/fulltext-eligibility/eligible.jsonl \
    --extractions data/fulltext-extraction/extraction.jsonl \
    [--extraction-output-dir data/fulltext-extraction/] \
    [--output-dir data/extraction-codebook/] \
    [--skip-existing] [--max-records N] [--dry-run] \
    [--model gemma3:4b] [--base-url http://localhost:11434]
```

`--extraction-output-dir` defaults to the parent of `--extractions`
(same reasoning as stage 7). Register subparser in
[cli.py](../src/laglitsynth/cli.py) in stage order.

## Design decisions

- **Single-pass prototype, two-pass deferred.** We need end-to-end flow
  through stages 10–12 to judge where quality losses bite. Two-pass
  first optimises a component whose downstream effect isn't yet
  observable.
- **Write every record, successes and failures.** `records.jsonl` is a
  complete run record; sentinel rows carry `None` in content fields.
- **Free text over enums, tagging over classification.** Per
  [docs/codebook.md](../docs/codebook.md). Enums land at phase 3 after
  human review clusters the LLM's actual vocabulary.
- **Three-way code availability** (tracking software / setup /
  analysis) — distinct availability patterns are load-bearing for RQ1.1
  synthesis.
- **`source_basis` on every record** and summarised on meta. Downstream
  readers attribute per-extraction without re-joining.
- **Abstract-only records are honestly sparse.** Most numerical fields
  legitimately `None`. Stage 10 must filter on `source_basis` before
  reporting completeness.
- **Join in memory, stream records, client instantiated once** —
  identical shape to stages 5/6/7.

## Implementation sequence

Six commits. `pixi run typecheck` and `pixi run test` before each.

### 1. Write docs/extraction-codebook.md

Per [Prerequisite](#prerequisite-docsextraction-codebookmd). Link from
[pipeline.md](../docs/pipeline.md) §8 and [README.md](../README.md).

### 2. Module scaffold and data models

Create the module. Populate `models.py`
(`ExtractionRecord`, `ExtractionCodebookMeta`, `_ExtractionPayload`,
`SourceBasis`, `TOOL_NAME`). Stub `run()`.

Test: `test_schema_roundtrip` — populate every field, dump, reparse;
plus all-`None` sentinel variant.

### 3. Prompt rendering

`prompts.py`: `SYSTEM_PROMPT` (template expanding the field list from
`model_json_schema()`), `USER_TEMPLATE`, `render_fulltext(tei, *,
char_budget)`, `render_abstract`, `build_user_message`, `CHAR_BUDGET`
constant (initial value is a placeholder committed to revisit after
first smoke run — no invented number).

Tests:

- Depth-first flatten with blank-line separation on a nested fixture.
- Under-budget → `truncated=False`; over-budget →
  `truncated=True`, truncated to end of nearest paragraph.
- Empty `sections()` → empty string.
- `SYSTEM_PROMPT` contains every field name in `ExtractionRecord`
  (guards against prompt/model drift).

### 4. Join, cascade, LLM call

`extract_works(catalogue, extractions, extraction_output_dir, client,
model, ...)` mirrors `screen_works`. Per-work
`extract_codebook(work_id, source_basis, user_text, client, model)`
handles the LLM call and validation.

Tests:

- `full_text` branch → LLM sees flattened sections.
- `abstract_only` branch → LLM sees abstract.
- No source → `reason="no-source"`, no LLM call.
- Malformed TEI → `reason="tei-parse-failure"`, no fallback.
- Bad JSON response → `reason="llm-parse-failure"`, `seed=None`.
- Partial JSON missing keys → caught by `ValidationError`, same
  outcome.
- `seed` recorded on successful records.
- Truncation branch produces `truncated=True` / `False` correctly.

### 5. CLI + run()

Preflight Ollama (copy stage 3). Iterate eligibility-filtered
catalogue, `append_jsonl` each record, write
`extraction-codebook-meta.json`. Register subparser in cli.py.

Tests: expected files, `--dry-run`, meta counts per branch,
`--skip-existing` adds only the delta, CLI wiring.

### 6. Docs and roadmap

- [pipeline.md](../docs/pipeline.md) §8: replace placeholder prose,
  point at the new stage doc and [codebook.md](../docs/codebook.md).
- [README.md](../README.md), [interfaces.md](../docs/interfaces.md):
  add stage 8 rows.
- [roadmap.md](roadmap.md): move stage 8 from "Queued" to "Implemented";
  note whether `extraction_status` was needed.

## Follow-ups

- Two-pass retrieval — trigger is phase 3 review flagging attention-
  dilution on specific fields.
- Phase 3 codebook revision loop: regenerate `ExtractionRecord` + prompt,
  drop old `records.jsonl`.
- Enum consolidation after phase-3 vocabulary clustering.
- Promote `ExtractedDocument.extraction_status` if silent bad records
  show up.
- Retry-with-error-in-prompt on `llm-parse-failure`. Cheap; add when
  failure rate warrants.
- `--temperature` / `--seed` CLI flags once multi-run consensus lands.
- Shared CSV export helper across stages 3, 4, 7, 8, 9.
- Per-record Ollama token-usage logging — telemetry to tune
  `CHAR_BUDGET` against real data.

## Risks

### Prompt length vs. context window

Full papers routinely exceed small-model context. Truncate at
`CHAR_BUDGET` (named constant in `prompts.py`, tuned on first smoke
run), prefer paragraph boundaries, flag `truncated=True`. Map-reduce
chunking is rejected — it adds a second LLM call plus a merge rule,
which is exactly the design work two-pass retrieval does better.

### Schema churn during phase 3

Expected and cheap. `extra="forbid"` makes stale `records.jsonl` fail
to load after a schema change — desired. Policy: delete the data
directory, re-run.

### LLM cost and latency on full corpus

One long prompt per paper is expensive at scale. Mitigations:
`--max-records` for the first real run; two-pass retrieval as a
follow-up. No numbers invented — commit to measuring on a smoke run
before a full-corpus run.

### `passage_locations` as two-pass ground truth

[docs/codebook.md](../docs/codebook.md) marks this field load-bearing
for two-pass calibration. The prototype captures it faithfully but
does not consume it; the two-pass plan will.

### Cross-stage TEI rendering duplication

Stage 7 and stage 8 both flatten TEI with slightly different budgets.
Default: duplicate with light factoring; shared helper is a later
refactor once both stages are stable.

## Critical files

- [docs/codebook.md](../docs/codebook.md)
- [docs/two-pass-extraction.md](../docs/two-pass-extraction.md)
- [src/laglitsynth/screening_abstracts/screen.py](../src/laglitsynth/screening_abstracts/screen.py)
- [src/laglitsynth/fulltext_extraction/models.py](../src/laglitsynth/fulltext_extraction/models.py)
- [src/laglitsynth/fulltext_extraction/tei.py](../src/laglitsynth/fulltext_extraction/tei.py)
- [src/laglitsynth/cli.py](../src/laglitsynth/cli.py)
