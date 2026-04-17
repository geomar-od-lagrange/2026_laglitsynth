# Reproducibility meta refactor

## Goal

Move every `*Meta` to a shared nested `_RunMeta` shape. Delete
`_Base` and split the pydantic `extra` policy cleanly between
OpenAlex-sourced models (`extra="ignore"`, upstream drift tolerated)
and internally-owned models (`extra="forbid"`, drift is a bug). Add
LLM determinism knobs on stage 3 — explicit temperature, per-call
random seed recorded on each `ScreeningVerdict`, and a prompt digest
on `ScreeningMeta` — so a final run is reproducible record-by-record
and multi-run consensus can draw genuinely independent samples.

One coherent cutover so the meta files the flag-don't-filter cutover
just rewrote aren't rewritten twice.

## Non-goals

- `tool_commit_sha` and dirty-tree tracking. Skipped per
  [roadmap](roadmap.md) pragmatism: this is a long-dev session with
  one or two final runs, not a reproducible-deploy story. Add later
  if we need it.
- `input_content_sha` hashing of input files. Work IDs are the
  stable identity; file hashing adds plumbing without answering a
  question we actually have.
- CLI flags for `--temperature` / `--seed`. Hardcoded defaults for
  now; lift to flags when we want to vary them.
- Ollama model digest via `ollama show --modelfile`. Model tag
  string is good enough at our scale.
- [Multi-run consensus](multi-run-consensus.md) implementation.
  Separate plan; this one just lands the data contract it needs.
- Anything under `Work` / OpenAlex models beyond flipping the
  `extra` policy. Field additions (`source_catalogues`,
  `is_peer_reviewed`, etc.) are tracked on the roadmap.

## Target state

### Shared types in `src/laglitsynth/models.py`

```python
class _RunMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str
    tool_version: str = "alpha"
    run_at: str              # ISO-8601 UTC, replaces per-stage *_at
    validation_skipped: int  # records dropped by read_jsonl on ValidationError


class _LlmMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    temperature: float
    prompt_sha256: str       # sha256(SYSTEM_PROMPT + "\n" + user prompt)
```

`_Base` is deleted.

### Per-stage `*Meta` restructure

Every `*Meta` gains a `run: _RunMeta` field. Stage-specific `*_at`
fields are removed — the timestamp lives in `run.run_at`. Stage
counts and parameters stay on the outer `*Meta`.

`ScreeningMeta` also gains `llm: _LlmMeta`.

Concretely:

- `FetchMeta`: `run`, `query`, `total_count`, `records_written`.
- `DeduplicationMeta`: `run`, `input_count`, `output_count`,
  `duplicates_removed`, `by_rule`.
- `ScreeningMeta`: `run`, `llm`, `threshold`, `input_path`,
  `input_count`, `above_threshold_count`, `below_threshold_count`,
  `skipped_count`.
- `AdjudicationMeta`: `run`, `threshold`, `input_count`,
  `accepted_count`, `rejected_count`.
- `RetrievalMeta`: `run`, `total_works`, `retrieved_count`,
  `abstract_only_count`, `failed_count`, `by_source`.
- `ExtractionMeta`: `run`, `grobid_version`, `total_pdfs`,
  `extracted_count`, `failed_count`, `invalid_stem_count`.

### `ScreeningVerdict.seed`

```python
class ScreeningVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    relevance_score: int | None = None
    reason: str | None = None
    seed: int | None = None   # NEW: Ollama seed used for this call;
                              # None for no-abstract / llm-parse-failure
```

### Extra policy split

- `extra="ignore"` on OpenAlex-sourced models (upstream adds fields
  we don't own): `Work`, `Author`, `Authorship`, `Institution`,
  `Source`, `Location`, `OpenAccess`, `Biblio`, `TopicHierarchy`,
  `Topic`, `Keyword`.
- `extra="forbid"` on everything we own: all `*Meta`, `_RunMeta`,
  `_LlmMeta`, `ScreeningVerdict`, `AdjudicationVerdict`,
  `RetrievalRecord`, `RetrievalStatus`, `ExtractedDocument`,
  `TextSection`.

### Stage 3 LLM call changes

In `screening_abstracts/screen.py`:

- `classify_abstract` draws `seed = random.randint(0, 2**31 - 1)`
  per call.
- Ollama call passes `temperature=0.8` (current Ollama default, now
  explicit) and `seed=seed`.
- Returned `ScreeningVerdict` carries `seed=seed`.
- `run()` computes `prompt_sha256 = hashlib.sha256((SYSTEM_PROMPT +
  "\n" + args.prompt).encode("utf-8")).hexdigest()` once.
- `ScreeningMeta.llm = _LlmMeta(model=args.model, temperature=0.8,
  prompt_sha256=prompt_sha256)`.

Per-verdict seed records two runs of the same abstract as genuinely
independent samples (Python's `random` reseeds from `os.urandom` per
process). Multi-run consensus is a loop of stage-3 invocations that
concatenates `verdicts.jsonl`s and deduplicates by `(work_id, seed)`.

### `validation_skipped` plumbing

`io.read_jsonl` gains an optional `stats` parameter:

```python
@dataclass
class JsonlReadStats:
    skipped: int = 0


def read_jsonl(
    path: Path, model: type[_T], stats: JsonlReadStats | None = None
) -> Iterator[_T]:
    ...
    except ValidationError as exc:
        logger.warning(...)
        if stats is not None:
            stats.skipped += 1
```

Every stage's `run()` creates a local `stats = JsonlReadStats()`,
passes it into its `read_jsonl` / `read_works_jsonl` calls, and
reads `stats.skipped` at the end for `_RunMeta.validation_skipped`.

`read_works_jsonl` gets the same treatment.

## Design decisions

### Nested `_RunMeta`, not flat

Flat would mean duplicating four fields across every `*Meta`.
Nested keeps provenance in one place and lets future run-meta
extensions land in one file without touching every stage. JSON
ergonomics cost: `meta.run.run_at` vs. `meta.run_at`. Acceptable.

### `tool_version` keeps the `"alpha"` placeholder

Real semver comes when we cut releases. Not in scope here.

### `_RunMeta` lives in `src/laglitsynth/models.py`

The current home of `_Base`. No need for a separate `meta.py`;
`models.py` is the shared-shapes file.

### `run_at` replaces per-stage `*_at`

The stage-specific timestamp names (`fetched_at`, `deduplicated_at`,
`screened_at`, `adjudicated_at`, `retrieved_at`, `extracted_at`)
are all "when this run completed". One uniform name under
`run.run_at` is the right shape. Green-field data dirs; no
migration.

### `RetrievalRecord.retrieved_at` and `ExtractedDocument.extracted_at` stay

These are per-record wall-clock timestamps, not run-level meta.
Keep on the record.

### `AdjudicationVerdict.adjudicated_at` stays

Per-verdict on purpose — real adjudication will have different
timestamps per verdict. Pass-through MVP duplicates the run
timestamp, which is fine.

### Ollama `temperature=0.8`

The current Ollama default is 0.8. Now we pass it explicitly so the
meta records the actual value. Not a tuning claim; if we want to
vary, add a CLI flag.

### Per-call seed draw, not process-level seed

Drawing per call via `random.randint(0, 2**31 - 1)` means each
verdict carries an independent seed. Multi-run consensus loops the
whole stage and gets fresh seeds every iteration (Python reseeds
`random` from `os.urandom` per process). No process-level seed
field in the meta — it would be a static curiosity and wouldn't
help reproduce individual verdicts.

### `seed: int | None` on `ScreeningVerdict`

`None` for the two sentinel cases (`no-abstract`,
`llm-parse-failure`). Both bypass the LLM so there's no seed to
record. Consumers filter by `reason` before interpreting `seed`.

### Prompt digest scope

`sha256(SYSTEM_PROMPT + "\n" + user_prompt)` as hex digest. One
string, full hash, 64 hex chars. Silent edits to either string show
up in the meta.

## Implementation sequence

Each step is a self-contained commit. Run `pixi run test` and
`pixi run typecheck` before each commit.

### 1. Shared types in `models.py`

Replace `_Base` with `_RunMeta` and `_LlmMeta`. Keep `_Base`
temporarily to avoid breaking imports mid-step; delete in step 8.

### 2. Flip extra policy on OpenAlex-sourced models

In `src/laglitsynth/catalogue_fetch/models.py`: replace
`from laglitsynth.models import _Base` with an explicit
`model_config = ConfigDict(extra="ignore")` on every
OpenAlex-sourced class (`Work`, `Author`, `Authorship`,
`Institution`, `Source`, `Location`, `OpenAccess`, `Biblio`,
`TopicHierarchy`, `Topic`, `Keyword`). Each class inherits from
`BaseModel` directly now.

`FetchMeta` is handled in step 3 (internal, `extra="forbid"`).

### 3. Flip extra policy on internal models; rework `*Meta` shapes

For each internal model across the codebase:

- Replace `_Base` with `BaseModel` + `model_config =
  ConfigDict(extra="forbid")`.
- For `*Meta` classes: add `run: _RunMeta`, drop the stage-specific
  `*_at` field, drop the now-redundant `tool` / `tool_version`
  fields (they live on `_RunMeta`).

Files touched:

- `src/laglitsynth/catalogue_fetch/models.py` — `FetchMeta`.
- `src/laglitsynth/catalogue_dedup/models.py` — `DeduplicationMeta`.
- `src/laglitsynth/screening_abstracts/models.py` —
  `ScreeningMeta` (add `llm: _LlmMeta`), `ScreeningVerdict` (add
  `seed: int | None`).
- `src/laglitsynth/screening_adjudication/models.py` —
  `AdjudicationMeta`, `AdjudicationVerdict`.
- `src/laglitsynth/fulltext_retrieval/models.py` — `RetrievalMeta`,
  `RetrievalRecord`.
- `src/laglitsynth/fulltext_extraction/models.py` —
  `ExtractionMeta`, `ExtractedDocument`, `TextSection`.

### 4. `JsonlReadStats` in `io.py`

Add `JsonlReadStats` dataclass. Extend `read_jsonl` and
`read_works_jsonl` with optional `stats` param.

### 5. Stage `run()` updates

For each stage, update `run()` to:

- Create a local `stats = JsonlReadStats()`, pass into every input
  read.
- Build `run_meta = _RunMeta(tool=TOOL_NAME, run_at=now,
  validation_skipped=stats.skipped)` where `TOOL_NAME` is a
  module-level constant (e.g., `"laglitsynth.catalogue_fetch.fetch"`).
- Pass `run=run_meta` into the stage's `*Meta`.

Stages touched: `catalogue_fetch/fetch.py`,
`catalogue_dedup/dedup.py`, `screening_abstracts/screen.py`,
`screening_adjudication/adjudicate.py`,
`fulltext_retrieval/retrieve.py`,
`fulltext_extraction/extract.py`.

### 6. Stage 3 LLM wiring

In `screening_abstracts/screen.py`:

- `classify_abstract` generates `seed = random.randint(0, 2**31 -
  1)` before the Ollama call.
- Ollama call: `client.chat.completions.create(model=model,
  response_format={"type": "json_object"}, messages=[...],
  temperature=0.8, seed=seed)`.
- Returned `ScreeningVerdict` carries `seed=seed`.
- Both sentinel-reason branches (`no-abstract`,
  `llm-parse-failure`) emit `seed=None`.
- `run()` builds the `prompt_sha256` once and passes a
  `_LlmMeta(model=args.model, temperature=0.8,
  prompt_sha256=prompt_sha256)` into `ScreeningMeta.llm`.

### 7. Tests

Every meta-shape test breaks. Update:

- `tests/test_catalogue_fetch.py` — assertions against `FetchMeta`
  shape.
- `tests/test_catalogue_dedup.py` — `DeduplicationMeta` shape.
- `tests/test_screening_abstracts.py` — `ScreeningMeta` with nested
  `run` and `llm`; `ScreeningVerdict` with `seed`; mock Ollama call
  to assert `temperature=0.8` and a non-None integer `seed` are
  passed.
- `tests/test_screening_adjudication.py` — `AdjudicationMeta`
  shape.
- `tests/test_fulltext_retrieval.py` — `RetrievalMeta` shape.
- `tests/test_fulltext_extraction.py` — `ExtractionMeta` shape.

Add new tests:

- `test_validation_skipped_counted`: write a JSONL with one valid +
  one invalid line; run a stage; assert
  `meta.run.validation_skipped == 1`.
- `test_seed_recorded_on_verdict`: patch `random.randint` to return
  a known value; run stage 3; assert verdict `seed` matches.
- `test_seed_none_on_sentinel_reasons`: `no-abstract` and
  `llm-parse-failure` verdicts have `seed=None`.
- `test_prompt_sha256_matches`: compute expected digest, compare
  `meta.llm.prompt_sha256`.

### 8. Delete `_Base`

Once every subclass has been migrated, remove `_Base` from
`src/laglitsynth/models.py`. Typecheck confirms no stragglers.

### 9. Docs

[docs/interfaces.md](../docs/interfaces.md):

- Update the meta-shape descriptions for all six stages to the new
  nested `run` (and `llm` on stage 3) layout.
- Add a short subsection describing `_RunMeta` and `_LlmMeta` as
  shared shapes.

Per-stage docs (`docs/catalogue-fetch.md`,
`docs/catalogue-dedup.md`, `docs/screening-abstracts.md`,
`docs/screening-adjudication.md`, `docs/fulltext-retrieval.md`,
`docs/fulltext-extraction.md`):

- If the doc shows example meta JSON, update it to the nested
  shape.
- `docs/screening-abstracts.md` additionally documents the `seed`
  field on verdicts and the `llm` block on the meta.

## Test migration summary

Changed: every existing meta-shape test (six files).

New: four tests listed in step 7.

Removed: none.

## Risks and rollback

- Existing `*-meta.json` files on disk don't have `run` /
  (on stage 3) `llm`. With `extra="forbid"` on all `*Meta`, loading
  them would fail. We don't currently re-read our own meta files
  anywhere in the code, so this is a data-dir cleanup only (green
  field — delete `data/**/*-meta.json` during migration).
- Existing `verdicts.jsonl` for stage 3 doesn't have `seed`; with
  `extra="forbid"` on `ScreeningVerdict`, re-reads would fail on
  missing field — actually no: `seed: int | None = None` has a
  default, so old records load as `seed=None`. No break.
- Rollback: single `git revert`. Green-field.

## Verification

Automated:

- `pixi run typecheck` passes.
- `pixi run test` passes with updated + new tests.

Manual (requires a running Ollama):

- Run stage 3 twice on the same 5-work input. Inspect
  `verdicts.jsonl` — same work_id shows two different seeds and
  (likely) two different scores across runs. Inspect
  `screening-meta.json` — `run.run_at` differs, `llm.prompt_sha256`
  matches across runs.
- Delete a field from a meta JSON on disk and try to reload through
  a read path (if any ever gets added) — `extra="forbid"` surfaces
  the drift.

## Open questions

1. Ollama 0.8 temperature default — is that the right value, or
   should we use 0.7 / 1.0 / let it be model-dependent? Not a
   blocker for this refactor; pick a value, land it, tune in a
   separate pass. Default: 0.8.
   FB: Default.
2. Do we want `seed` recorded on the adjudication verdict too?
   Currently `AdjudicationVerdict` is pass-through (no LLM), but
   future real adjudication may be LLM-assisted. Default: no — add
   when that stage gets an LLM.
   FB: If we go LLM we add.
