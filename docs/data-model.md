# Data model

Pydantic models in `src/laglitsynth/openalex/models.py`. These are the contract
between the fetch layer and all downstream consumers (BibTeX export, citation
graph, filtering).

## Work

The top-level record. Each JSONL line deserializes to a `Work`.

Key fields: `id` (OpenAlex ID), `doi`, `title`, `publication_year`,
`publication_date`, `authorships`, `biblio`, `primary_location`, `open_access`,
`cited_by_count`, `referenced_works`, `keywords`, `topics`, `abstract`,
`is_retracted`.

## Nested models

- **Authorship** — author + position + institutions + affiliations
- **Author** — name, OpenAlex ID, ORCID
- **Institution** — name, ROR, country
- **Source** — journal/repository metadata (name, ISSN, type)
- **Location** — where the work is hosted (OA status, URLs, version)
- **OpenAccess** — OA status and URL
- **Biblio** — volume, issue, pages
- **Topic** / **TopicHierarchy** — OpenAlex topic classification (topic -> subfield -> field -> domain)
- **Keyword** — OpenAlex keyword with relevance score

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
