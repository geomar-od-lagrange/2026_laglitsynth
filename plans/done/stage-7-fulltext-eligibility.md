# Stage 7: full-text eligibility

## Goal

Implement `fulltext-eligibility`: one LLM pass deciding whether each
included work meets the review's inclusion criteria. The stage joins
[data/screening-adjudication/included.jsonl](../data/screening-adjudication/)
against the extractions in
[data/fulltext-extraction/extraction.jsonl](../data/fulltext-extraction/),
prefers full text, falls back to the abstract, and emits a verdict
sidecar in the flag-don't-filter shape established by stage 3. Output
`eligible.jsonl` feeds stage 8.

Prototype is loose on purpose: binary verdict, broad criteria, false
positives tolerated. Per-criterion breakdown, thresholds, calibration,
and chunking are follow-ups.

## Non-goals

- Per-criterion verdicts, threshold math, human calibration — stage 9
  territory, not here.
- Chunking or retrieval over the full text. One concatenated prompt per
  work; truncate on overflow (see [Risks](#risks)).
- Multi-run consensus. Seeds are recorded so
  [multi-run-consensus.md](multi-run-consensus.md) can draw independent
  samples later, but running the loop is a separate plan.
- Populating `Work.source_catalogues` or `ExtractedDocument.extraction_status`.
  Both are flagged on the [roadmap](roadmap.md) as "defer until stage 7+
  consumer arrives"; stage 7 does not branch on either, so this is not
  that consumer. See [Follow-ups](#follow-ups).
- Rewriting [docs/eligibility.md](../docs/eligibility.md) before the
  code lands. It predates the flag-don't-filter cutover and the
  reproducibility meta refactor; refresh is step 5 below.

## Target state

### Storage layout

```
data/fulltext-eligibility/
  verdicts.jsonl            # one EligibilityVerdict per input work
  eligible.jsonl            # Work records where verdict.eligible is True
  eligibility-meta.json     # EligibilityMeta
```

`verdicts.jsonl` is the source of truth; `eligible.jsonl` is a derived
convenience file regenerated each run, same as stage 4's `included.jsonl`.

### Data models

New module [src/laglitsynth/fulltext_eligibility/](../src/laglitsynth/):

```python
TOOL_NAME = "laglitsynth.fulltext_eligibility.assess"

SourceBasis = Literal["full_text", "abstract_only", "none"]


class EligibilityVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    eligible: bool | None          # None for sentinel-reason skips
    source_basis: SourceBasis
    reason: str | None             # LLM free-text or sentinel
    seed: int | None


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

Deviations from [docs/eligibility.md](../docs/eligibility.md), all
upgrades to current conventions rather than reinterpretations:

- Drop `confidence` — binary verdict, no threshold.
- `eligible: bool | None` carries the three sentinel skips
  (`"no-source"`, `"tei-parse-failure"`, `"llm-parse-failure"`); stage 8
  reads `eligible.jsonl` and never sees the tri-state.
- Add `seed` on the verdict for multi-run consensus.
- Nest `run: _RunMeta` + `llm: _LlmMeta` per the reproducibility
  refactor; drop flat `tool` / `assessed_at`.
- Add `input_catalogue`, `input_extractions`, `skipped_count` for
  provenance.

### LLM prompt

Seed prompt — iterates during tuning. Hardcoded in `eligibility.py`;
`prompt_sha256` on the meta distinguishes versions.

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

User: <source_basis tag>:\n<text>
```

`response_format={"type": "json_object"}`, `temperature=0.8`, per-call
random seed — identical to stage 3's `classify_abstract`.

### Surfacing TEI to the LLM

`ExtractedDocument` is the thin wrapper from
[plans/done/tei-wrapper.md](done/tei-wrapper.md). To render prompt text:

1. `doc.open_tei(extraction_output_dir)` → `TeiDocument`.
2. `tei.sections()` yields recursive `Section`s. Flatten depth-first,
   concatenate title + paragraphs, separate sections with blank lines.
3. Drop figures and bibliography — the three criteria are answered from
   body text. Stage 8 will take more of the TEI surface.

Helper `eligibility.prompts.render_fulltext(tei) -> str` keeps this
logic in one place and tests it. Truncation policy lives here too.

### Fallback cascade

Per work, in catalogue order:

1. `ExtractedDocument` for `work.id` exists and `sections()` non-empty →
   `full_text` branch.
2. Else `work.abstract` non-empty → `abstract_only` branch.
3. Else → `source_basis="none"`, `eligible=None`, `reason="no-source"`,
   no LLM call.

Malformed TEI: `sections()` raises `XMLSyntaxError` → catch, log,
record `reason="tei-parse-failure"`, `eligible=None`. No abstract
fallback — a malformed extraction is an operator-visible bug; silent
fallback would hide it. Empty-body TEI (valid XML, no content) returns
`[]` and goes to step 2, not this branch.

Sentinel reasons all set `eligible=None` and `seed=None`:
`"no-source"`, `"tei-parse-failure"`, `"llm-parse-failure"`.

### CLI

```
laglitsynth fulltext-eligibility \
    --catalogue data/screening-adjudication/included.jsonl \
    --extractions data/fulltext-extraction/extraction.jsonl \
    [--extraction-output-dir data/fulltext-extraction/] \
    [--output-dir data/fulltext-eligibility/] \
    [--skip-existing] [--max-records N] [--dry-run] \
    [--model gemma3:4b] [--base-url http://localhost:11434]
```

`--extraction-output-dir` is needed because
`ExtractedDocument.tei_path` is stored relative to the extraction
output dir (see [tei-wrapper.md](done/tei-wrapper.md)). Defaults to the
parent of `--extractions`.

`--skip-existing`, `--dry-run`, `--max-records`, `--model`, `--base-url`
all match stage 3. Register the subparser in
[src/laglitsynth/cli.py](../src/laglitsynth/cli.py) in stage order.

## Design decisions

- **Join in memory, stream verdicts.** Extractions JSONL loads into a
  `dict[str, ExtractedDocument]` keyed by `work_id` (TEI bytes stay on
  disk, loaded lazily by `TeiDocument`). Catalogue streams via
  `read_works_jsonl`. Append verdicts with `append_jsonl` so partial
  runs leave valid output — same pattern as stage 5/6.
- **`source_basis` on both verdict and meta.** Meta carries
  `by_source_basis` counts; the per-verdict field answers "was this
  judged on full text?" without re-joining.
- **Pass-through LLM client.** Instantiate once in `run()`, thread into
  `assess_work()` — the per-work TCP-latency fix from the
  flag-don't-filter cutover applies here too.
- **`eligible.jsonl` regenerates from scratch each run.** Same as
  stage 4. `--skip-existing` appends to verdicts; `eligible.jsonl` is
  rewritten from the union.
- **No `extraction_status` branch.** Missing or empty `sections()`
  already gives the right answer. The flag matters when a consumer
  wants to reject badly-extracted methods sections; stage 7 just picks
  a source. Re-evaluate if stage 8 needs it.

## Implementation sequence

Five commits. `pixi run typecheck` and `pixi run test` before each.

### 1. Module scaffold

Create `src/laglitsynth/fulltext_eligibility/{__init__,models,prompts,eligibility}.py`.
Populate `models.py` per above. Stub `run()` in `eligibility.py` with
`TOOL_NAME`. No tests yet.

### 2. Prompt rendering

`prompts.py`: `SYSTEM_PROMPT`, `USER_TEMPLATE`, `render_fulltext(tei)`,
`render_abstract(abstract)`, `build_user_message(source_basis, text)`.

Tests (`tests/test_fulltext_eligibility_prompts.py`):

- `render_fulltext` flattens nested sections in depth-first order with
  blank-line separation.
- Empty `sections()` → empty string (caller's signal to fall back).

### 3. Join, cascade, LLM call

`eligibility.py`: `assess_works(catalogue, extractions,
extraction_output_dir, client, model, ...)` and
`classify_eligibility(work_id, prompt, source_basis, client, model)` —
same shape and error handling as stage 3's `classify_abstract`.

Tests:

- `full_text` branch when extraction exists; LLM sees flattened sections
  (asserted via mocked client call args).
- `abstract_only` branch when extraction missing; LLM sees abstract.
- Neither → `eligible=None`, `source_basis="none"`,
  `reason="no-source"`, no LLM call.
- Malformed TEI → `reason="tei-parse-failure"`, no abstract fallback.
- LLM returns bad JSON → `reason="llm-parse-failure"`, `seed=None`.
- `seed` recorded on successful verdicts.

### 4. CLI + `run()`

Preflight Ollama (copy from stage 3). Iterate catalogue, append each
verdict, then rebuild `eligible.jsonl` from verdicts and write
`eligibility-meta.json`. Register subparser in
[cli.py](../src/laglitsynth/cli.py).

Tests:

- Expected files exist; `--dry-run` writes nothing.
- `eligible.jsonl` contains exactly the `eligible is True` works.
- Meta counts match fixture (`input_count`, `eligible_count`,
  `excluded_count`, `skipped_count`, `by_source_basis`).
- `--skip-existing` adds only the delta.

### 5. Docs refresh

Rewrite [docs/eligibility.md](../docs/eligibility.md) to describe the
implemented stage: drop `confidence`, switch to `bool | None` + sentinel
reasons, use `run: _RunMeta` nesting, document TEI rendering and the
three sentinels, sync the CLI block.

Add stage-7 rows to [docs/pipeline.md](../docs/pipeline.md) and
[docs/interfaces.md](../docs/interfaces.md). Move stage 7 on
[roadmap.md](roadmap.md) from "Queued" to "Implemented" with a note
that `extraction_status` was not needed after all.

Must land in the same PR as the code — otherwise the "descriptive docs
stay useful after implementation" rule in [AGENTS.md](../AGENTS.md) is
violated.

## Follow-ups

- Promote `ExtractedDocument.extraction_status` if bad-TEI exclusions
  empirically cost real inclusions.
- `--temperature` and `--seed` CLI flags once multi-run consensus
  lands.
- Per-criterion verdicts (three booleans) if the single boolean hides
  too many close calls. Green-field, so schema break is fine.
- Shared CSV export for LLM-driven stages (3, 4, 7, 9). Not stage-7-
  specific; land once as a helper.
- Revisit `--skip-existing` semantics when `prompt_sha256` changes.
  Stage 5/6 have the same ambiguity; address it once across stages.

## Risks

### Prompt length vs. context window

Full papers may exceed small-model context (e.g. gemma3:4b). Prototype
policy: truncate `render_fulltext` at a hardcoded character budget
(tuned on the first real run, named constant in `prompts.py`), and
record a `truncated: bool` on the verdict when that triggers. Add the
field when first overflow is observed, not preemptively. Map-reduce
chunking is rejected for the prototype — revisit if truncation loses
eligibility signal.

### Abstract-only quality

Stage 3 already judged abstracts, on broader criteria. We re-run the
LLM here rather than inheriting the stage 3 verdict; the duplication
is intentional while both prompts are being tuned.

### Malformed vs. empty TEI

Empty body (valid XML, no content) falls back to abstract — extraction
succeeded, just produced nothing. Broken XML raises and records a
sentinel with no fallback. If `"tei-parse-failure"` fires often enough
to block a run, flip to "fall back and record both reasons".

## Critical files

- [src/laglitsynth/screening_abstracts/screen.py](../src/laglitsynth/screening_abstracts/screen.py)
- [src/laglitsynth/screening_abstracts/models.py](../src/laglitsynth/screening_abstracts/models.py)
- [src/laglitsynth/fulltext_extraction/models.py](../src/laglitsynth/fulltext_extraction/models.py)
- [src/laglitsynth/fulltext_extraction/tei.py](../src/laglitsynth/fulltext_extraction/tei.py)
- [src/laglitsynth/cli.py](../src/laglitsynth/cli.py)
