# Data model

The pipeline builds on the OpenAlex data model. `Work` and its nested
types (`Authorship`, `Source`, `Topic`, etc.) in
[`src/laglitsynth/catalogue_fetch/models.py`](../src/laglitsynth/catalogue_fetch/models.py)
are OpenAlex's schema expressed as Pydantic models, not a generic
abstraction. Every stage that handles works consumes `Work` records: the
deduplicated catalogue before the [corpus](corpus.md), and
`data/corpus/works.jsonl` after it. See the source for the full schema.

The pipeline's own records are Pydantic models in each stage's
`models.py`; [interfaces.md](interfaces.md#model-dependency-graph) lists
them by stage. Stage 2 deduplication adds
[`DroppedRecord`](../src/laglitsynth/catalogue_dedup/models.py), with
fields `dropped_work_id` (the dropped duplicate), `survived_work_id`
(the surviving record), and `rule` (the matching rule that triggered the
drop: `openalex_id`, `doi`, or `title_author_year`). `corpus-assemble`
adds [`CorpusPdf` and `CorpusMeta`](../src/laglitsynth/corpus/models.py),
described in [corpus.md](corpus.md).

## Shared run-level types

`RunMeta` and `LlmMeta` in
[`src/laglitsynth/models.py`](../src/laglitsynth/models.py) are the two
shared types carried by the stages' `*Meta` sidecars.

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
four fields; the timestamp of a run lives in `run.run_at`.

`LlmMeta` is carried by the stages that call an LLM (3, 7, 8), and
`CorpusMeta.screening_llm` copies stage 3's:

```python
class LlmMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    base_url: str              # OpenAI-compatible base, including /v1
    decisions_url: str | None  # None for chat stages
    reasoning_effort: str | None  # stage 8's --reasoning-effort; None when none was sent
    prompt_sha256: str  # sha256(prompt or question JSON + "\n" + USER_TEMPLATE), full hex digest
```

No temperature or seed is sent to the endpoint, so none is recorded, and
the API key is never recorded. Per-stage `*Meta` classes (e.g.
`FetchMeta`, `ScreeningMeta`, `EligibilityMeta`, `ExtractionCodebookMeta`)
each nest `run: RunMeta` and, where applicable, `llm: LlmMeta`.

## Design decisions

- **`extra="ignore"` on OpenAlex-sourced models** (`Work`, `Authorship`,
  `Source`, `Topic`, etc.). OpenAlex adds new fields regularly; unknown
  fields are silently dropped rather than causing errors.
- **`extra="forbid"` on pipeline-owned models** (all `*Meta`, `*Verdict`,
  `*Record`, `CorpusPdf`). Drift between the schema definition and the
  data on disk is treated as a bug, not silently tolerated.
- **Nullable where OpenAlex is nullable.** Many fields that the OpenAlex schema
  documents as required can be `null` in practice (errata, old records, data
  quality issues). The models accept `None` rather than skipping records.
- **Mandatory `Work` fields.** A handful of fields carry no default and
  must be present (and the right type) for a record to validate:
  `authorships`, `biblio`, `cited_by_count`, `referenced_works`,
  `keywords`, and `topics`. A record missing any of these fails
  `Work` validation and is skipped at read time, incrementing
  `validation_skipped` — so a `validation_skipped` count traces back to
  records that lacked one of these fields.
- **`is_retracted: bool | None`.** OpenAlex's retraction flag is carried
  through on `Work` (nullable when OpenAlex omits it) so the review can
  surface or exclude retracted papers.
- **`publication_date` is `date | None`**, not a string. Pydantic validates the
  format; downstream code gets a real date object.
- **Abstract stored as plain text.** Reconstructed from OpenAlex's inverted
  index at ingest time.
- **`referenced_works` as `list[str]`.** These are OpenAlex IDs, kept as
  delivered; no stage resolves them to records.
- **No `concepts` field.** Deprecated by OpenAlex (replaced by `topics`).
- **No `related_works`.** Algorithmically generated, changes frequently, inflates
  file size. Not useful for literature review.
