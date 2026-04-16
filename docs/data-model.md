# Data model

Pydantic models in [`src/laglitsynth/catalogue_fetch/models.py`](../src/laglitsynth/catalogue_fetch/models.py).
These are the contract between the fetch layer and all downstream consumers
(BibTeX export, citation graph, filtering). See the source for the full schema.

The pipeline builds on the OpenAlex data model. `Work` and its nested types
(`Authorship`, `Source`, `Topic`, etc.) are OpenAlex's schema expressed as
Pydantic models, not a generic abstraction. All downstream components consume
`Work` records. The shared `_Base` in
[`src/laglitsynth/models.py`](../src/laglitsynth/models.py) is a convenience
base class (`extra="ignore"`) -- the domain models themselves are
OpenAlex-specific.

## Design decisions

- **`ConfigDict(extra="ignore")`** on all models. OpenAlex adds new fields
  regularly; unknown fields are silently dropped rather than causing errors.
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
