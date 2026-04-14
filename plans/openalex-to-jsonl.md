# Plan: OpenAlex to JSONL publication database

Build a CLI tool that searches OpenAlex by keyword and stores the full result set
as a JSONL file. This JSONL becomes the primary data store for downstream tasks
(BibTeX export, citation graph construction, filtering, structured literature review).

## Data model

Pydantic models in a dedicated `models.py`. These serve as the contract between the
fetch layer and all downstream consumers (BibTeX export, citation graph, filtering).

Each JSONL line is a serialized `Work` model. Nested structures get their own models.

### Models

```python
class Institution(BaseModel):
    id: str
    display_name: str
    ror: str | None
    country_code: str | None
    type: str | None

class Author(BaseModel):
    id: str
    display_name: str
    orcid: str | None

class Authorship(BaseModel):
    author_position: str               # "first", "middle", "last"
    author: Author
    institutions: list[Institution]
    countries: list[str]
    is_corresponding: bool | None
    raw_affiliation_strings: list[str]

class Source(BaseModel):
    id: str
    display_name: str
    issn_l: str | None
    issn: list[str] | None
    type: str | None                    # "journal", "book series", "repository", ...
    host_organization_name: str | None

class Location(BaseModel):
    is_oa: bool | None
    landing_page_url: str | None
    pdf_url: str | None
    source: Source | None
    version: str | None
    license: str | None

class OpenAccess(BaseModel):
    is_oa: bool | None
    oa_status: str | None               # "gold", "green", "hybrid", "bronze", "closed"
    oa_url: str | None

class Biblio(BaseModel):
    volume: str | None
    issue: str | None
    first_page: str | None
    last_page: str | None

class TopicHierarchy(BaseModel):
    id: str
    display_name: str

class Topic(BaseModel):
    id: str
    display_name: str
    score: float
    subfield: TopicHierarchy
    field: TopicHierarchy
    domain: TopicHierarchy

class Keyword(BaseModel):
    id: str
    display_name: str
    score: float

class Work(BaseModel):
    id: str                             # OpenAlex ID, e.g. "https://openalex.org/W..."
    doi: str | None
    title: str | None
    type: str | None                    # "article", "book", "book-chapter", ...
    publication_year: int | None
    publication_date: date | None
    language: str | None
    authorships: list[Authorship]
    biblio: Biblio
    primary_location: Location | None
    open_access: OpenAccess | None
    cited_by_count: int
    referenced_works: list[str]         # list of OpenAlex IDs
    updated_date: str | None            # for staleness checks / incremental updates
    keywords: list[Keyword]
    topics: list[Topic]
    primary_topic: Topic | None
    abstract: str | None                # reconstructed plain text (not the inverted index)
    is_retracted: bool
```

### Design decisions

- **Pydantic, not dicts.** Validates on deserialization, catches schema drift from
  the API early, gives type-checked access everywhere downstream.
- **`model_config = ConfigDict(extra="ignore")`** on all models so we don't break
  when OpenAlex adds new fields.
- **Nullable where OpenAlex is nullable.** `title`, `type`, `open_access` can be
  null for errata, corrections, and older records. Don't crash on these.
- **`publication_date` is `date | None`**, not a string. Pydantic validates the
  format; downstream code gets a real date object.
- **No `concepts` field.** Deprecated by OpenAlex (replaced by `topics`).
- **No `related_works`.** Algorithmically generated, not citation-based, changes
  frequently, inflates file size. Not useful for literature review. Can be fetched
  on demand if ever needed.
- **Abstract stored as plain text.** The fetch layer reconstructs the abstract from
  OpenAlex's inverted index format during ingest. The JSONL stores human-readable
  text — no downstream tool should have to deal with the inverted index.
- **`referenced_works` as `list[str]`.** These are OpenAlex IDs. Resolving them to
  full records is the citation graph tool's job, not this model's.
- **`updated_date` included.** Enables staleness checks and incremental update
  workflows later.

## Authentication

As of Feb 2026, OpenAlex requires a (free) API key. Register at
openalex.org/settings/api. The tool reads the key from the environment variable
`OPENALEX_API_KEY` and sets `pyalex.config.api_key`.

Fail fast with a clear error if the key is missing.

## CLI interface

```
pixi run fetch-publications "lagrangian oceanography"
pixi run fetch-publications "lagrangian oceanography" -o custom/path.jsonl
```

Arguments:
- Positional: search query string (required)
- `-o` / `--output`: output JSONL path (optional; default:
  `data/openalex/<query_slug>_<ISO8601>.jsonl`). The timestamp is
  `datetime.now(UTC).isoformat(timespec='microseconds')` with colons replaced
  by hyphens for filesystem safety, e.g.
  `data/openalex/lagrangian_oceanography_2026-04-14T15-23-12.123456+00-00.jsonl`.
  The slug is the query lowercased with non-alphanumeric runs replaced by `_`.
- `--from-year`: filter publications from this year onward (optional, mapped to
  `from_publication_date: "YYYY-01-01"`)
- `--to-year`: filter publications up to this year (optional, mapped to
  `to_publication_date: "YYYY-12-31"`)
- `--max-results`: cap on number of results; default: no cap (fetch all) (optional).
  Note: pyalex's paginator stops after the page that crosses the threshold, so the
  actual count may slightly exceed this value. The tool post-truncates to exactly
  `max_results`.
- `--resume`: if the output file already exists, count existing records and continue
  fetching from where it left off (optional)

## Implementation

### 1. `src/laglitsynth/openalex/models.py` — pydantic models

The models listed above. All models use `ConfigDict(extra="ignore")`.
Shared by all components in the pipeline.

### 2. `src/laglitsynth/openalex/fetch.py` — fetch + write

Two public functions:

#### `search_openalex(query, *, from_year=None, to_year=None, max_results=None) -> Iterator[Work]`

- Constructs a `pyalex.Works` query with `.search(query)` and optional
  `.filter(from_publication_date=..., to_publication_date=...)`.
- Uses `.select(FIELDS)` to request only the fields covered by the models.
  Requests `abstract_inverted_index` from the API, reconstructs plain text,
  and yields `Work` instances with the `abstract` field populated.
- Paginates via `.paginate(per_page=200, n_max=...)`.
- Validates each record through `Work(**raw_dict)` — invalid records are logged
  and skipped rather than crashing the entire fetch.
- Yields `Work` instances.
- Logs progress to stderr (count so far, total expected from `.count()`).

**Retry handling:** Configure `pyalex.config.max_retries = 3` and
`pyalex.config.retry_backoff_factor = 0.5` so transient 429/5xx errors don't
kill a long-running fetch.

#### `write_jsonl(works: Iterator[Work], output: Path) -> int`

- Writes each `Work` as a single JSON line via `work.model_dump_json()`.
- Returns count of records written.
- Writes incrementally (does not buffer all records in memory).
- If `output` already exists and `--resume` is not set, refuse and exit with an
  error (no silent overwrite).

#### `__main__` block

- Parses CLI args with `argparse`.
- Reads `OPENALEX_API_KEY` from environment.
- Pipes `search_openalex(...)` into `write_jsonl(...)`.
- Prints summary to stderr: total records, file size, elapsed time.

### 3. pixi task

Register `fetch-publications` as a pixi task so it's invocable via
`pixi run fetch-publications ...`.

### 4. Output conventions

- Default output lands in `data/openalex/` (gitignored). The tool creates
  parent directories if they don't exist.
- Each run produces a unique filename via the `<slug>_<ISO8601>` convention,
  so reruns of the same query don't collide.
- **Sidecar metadata file**: alongside `output.jsonl`, write `output.meta.json`
  containing `{"query": ..., "fetched_at": ..., "total_count": ..., "records_written": ...}`.
  This keeps the JSONL as a pure stream of `Work` records — no special first-line
  handling needed.

### 5. Reading JSONL back

Any downstream tool loads records with:

```python
Work.model_validate_json(line)
```

Every line is a `Work`. No conditionals, no skipping. This is the main payoff of
using pydantic — a single line gives you a fully typed, validated object.

### 6. Resumability

When `--resume` is passed and the output file exists:
- Count existing lines in the JSONL.
- Read `total_count` from the sidecar metadata file.
- Skip that many records in the paginator and continue appending.
- Update the sidecar metadata on completion.

This handles the common failure mode of a long fetch dying partway through.

## Dependencies

- `pyalex` (PyPI) — OpenAlex API client
- `pydantic` (PyPI) — data model + validation
- Python stdlib only beyond that (`json`, `argparse`, `pathlib`, `sys`, `time`,
  `datetime`)

## What this plan does NOT cover

- BibTeX export (separate tool, reads JSONL via `Work` models)
- Citation graph construction (separate tool, reads `referenced_works`)
- Deduplication (preprint vs. published — future concern)
- Incremental updates / merging multiple JSONL files
- Arbitrary OpenAlex filter passthrough (query building will evolve; handle then)

## Estimated fetch times

At ~80 records/sec with full payload (measured empirically):

| Result count | Time     |
|-------------|----------|
| 1,000       | ~13s     |
| 10,000      | ~2 min   |
| 16,000      | ~3.5 min |
| 100,000     | ~21 min  |

## Post-implementation notes

- **Resumability dropped.** `--resume` was cut during implementation — cursor
  pagination isn't index-stable between runs, so skipping N records from a
  fresh query doesn't land on the right spot. Timestamped filenames mean
  failed fetches can simply be rerun.
- **Nullable fields relaxed beyond plan.** `Author.id`, `Institution.id`,
  `Institution.display_name`, and `is_retracted` are nullable in practice
  despite the OpenAlex schema. Models accept `None` rather than skipping
  records.
- **`--max-results` defaults to 199** as a safety cap (plan said no cap).
