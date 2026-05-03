# Data model

Pydantic models in [`src/laglitsynth/catalogue_fetch/models.py`](../src/laglitsynth/catalogue_fetch/models.py).
These are the contract between the fetch layer and all downstream consumers
(BibTeX export, citation graph, filtering). See the source for the full schema.

The pipeline builds on the OpenAlex data model. `Work` and its nested types
(`Authorship`, `Source`, `Topic`, etc.) are OpenAlex's schema expressed as
Pydantic models, not a generic abstraction. All downstream components consume
`Work` records. The domain models are OpenAlex-specific; they carry
`extra="ignore"` directly so upstream field additions do not cause validation
errors.

[`src/laglitsynth/models.py`](../src/laglitsynth/models.py) holds two shared
types used by the pipeline's own internal records — `RunMeta` and `LlmMeta`
— described below.

## Shared run-level types

`RunMeta` and `LlmMeta` in
[`src/laglitsynth/models.py`](../src/laglitsynth/models.py) are the two
shared types carried by every stage's `*Meta` sidecar.

`RunMeta` captures provenance for a single tool invocation:

```python
class RunMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str               # e.g. "laglitsynth.catalogue_fetch.fetch"
    tool_version: str       # "alpha" until first release
    run_at: str             # ISO-8601 UTC
    validation_skipped: int # records dropped by read_jsonl on ValidationError
```

Every `*Meta` sidecar embeds `run: RunMeta` rather than repeating these
four fields. The stage-specific `*_at` field names (`fetched_at`,
`screened_at`, …) were removed in the reproducibility-meta refactor; the
timestamp lives in `run.run_at`.

`LlmMeta` is added to stages that call an LLM (currently stages 3, 7, 8):

```python
class LlmMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    temperature: float
    prompt_sha256: str  # sha256(SYSTEM_PROMPT + "\n" + user prompt), full hex digest
```

Per-stage `*Meta` classes (e.g. `FetchMeta`, `ScreeningMeta`,
`EligibilityMeta`, `ExtractionCodebookMeta`) each nest `run: RunMeta` and,
where applicable, `llm: LlmMeta`. All pipeline-owned models carry
`extra="forbid"` so schema drift is surfaced as a validation error.

## Design decisions

- **`extra="ignore"` on OpenAlex-sourced models** (`Work`, `Authorship`,
  `Source`, `Topic`, etc.). OpenAlex adds new fields regularly; unknown
  fields are silently dropped rather than causing errors.
- **`extra="forbid"` on pipeline-owned models** (all `*Meta`, `*Verdict`,
  `*Record`). Drift between the schema definition and the data on disk is
  treated as a bug, not silently tolerated.
- **Nullable where OpenAlex is nullable.** Many fields that the OpenAlex schema
  documents as required can be `null` in practice (errata, old records, data
  quality issues). The models accept `None` rather than skipping records.
- **`publication_date` is `date | None`**, not a string. Pydantic validates the
  format; downstream code gets a real date object.
- **Abstract stored as plain text.** Reconstructed from OpenAlex's inverted
  index at ingest time.
- **`referenced_works` as `list[str]`.** These are OpenAlex IDs. Resolving them
  to full records is the citation graph tool's job.
- **No `concepts` field.** Deprecated by OpenAlex (replaced by `topics`).
- **No `related_works`.** Algorithmically generated, changes frequently, inflates
  file size. Not useful for literature review.
