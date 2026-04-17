# Plan: Stages 2, 4 pass-through MVPs + Stage 5 full-text retrieval

## Context

The pipeline has stages 1 (search) and 3 (screen-abstracts) implemented.
Everything from stage 5 onward is blocked on retrieval. The goal is to get
full-text retrieval working so the rest of the pipeline can be plumbed out
with MVPs.

This plan covers:

- **Stage 2 (deduplication):** pass-all MVP with full CLI and correct
  output schema per [deduplication.md](../docs/deduplication.md) -- no
  records dropped, real matching logic deferred
- **Stage 4 (adjudication):** pass-through MVP per
  [adjudication-screening.md](../docs/adjudication-screening.md)
- **Stage 5 (full-text retrieval):** real implementation, OpenAlex OA URLs
  + Unpaywall + manual PDF pickup

Stage 6 (GROBID extraction) is out of scope for this pass but the models
are defined here so stage 5's output contracts are clear.

### Resolve module: deferred

[`interfaces.md`](../docs/interfaces.md) describes a `resolve` module and
`--data-dir` pattern for joining catalogues against verdict sidecars. That
module does not exist and building it requires also refactoring stage 3 to
the flag-don't-filter pattern. For now, stages 2, 4, and 5 take explicit
`--input` paths. This matches the
[adjudication-screening.md](../docs/adjudication-screening.md) spec. The
resolve module can be built when we have real adjudication.

## New files

```
src/laglitsynth/catalogue_dedup/
    __init__.py
    models.py          # DeduplicationMeta
    deduplicate.py     # build_subparser, run (pass-all)

src/laglitsynth/screening_adjudication/
    __init__.py
    models.py          # AdjudicationMeta
    screening.py       # build_subparser, run (pass-through)

src/laglitsynth/fulltext_retrieval/
    __init__.py
    models.py          # RetrievalStatus, RetrievalRecord, RetrievalMeta
    retrieve.py        # build_subparser, run, download logic, Unpaywall client

src/laglitsynth/fulltext_extraction/
    __init__.py
    models.py          # TextSection, ExtractedDocument, ExtractionMeta

tests/
    test_dedup.py
    test_adjudication_screening.py
    test_retrieval.py
```

## Modified files

- [`src/laglitsynth/cli.py`](../src/laglitsynth/cli.py) -- register
  `catalogue-dedup`, `screening-adjudication`, and `fulltext-retrieval` subcommands
- [`src/laglitsynth/io.py`](../src/laglitsynth/io.py) -- add
  `append_jsonl` for streaming/resumable writes and generic `read_jsonl`
- [`pyproject.toml`](../pyproject.toml) -- add `httpx` and `lxml`
  dependencies, mypy override for `lxml`

## Stage 2: deduplicate

### Models (`dedup/models.py`)

Per [deduplication.md](../docs/deduplication.md):

```python
class DeduplicationMeta(_Base):
    tool: str = "laglitsynth.catalogue_dedup.deduplicate"
    tool_version: str = "alpha"
    deduplicated_at: str
    input_count: int
    output_count: int
    duplicates_removed: int
    by_rule: dict[str, int]       # counts per matching rule
```

### CLI

```
laglitsynth catalogue-dedup \
    --input data/catalogue-fetch/combined.jsonl \
    --output-dir data/catalogue-dedup/
```

### Behavior (pass-all MVP)

1. Read all works from `--input` via `read_works_jsonl`.
2. Write them all to `<output-dir>/deduplicated.jsonl` via `write_jsonl`.
3. Write empty `<output-dir>/dropped.jsonl` (no records dropped).
4. Write `DeduplicationMeta` to `<output-dir>/dedup-meta.json` with
   `duplicates_removed=0`, `by_rule={}`.
5. Print summary to stderr.

The output files (`deduplicated.jsonl`, `dropped.jsonl`, `dedup-meta.json`)
match the storage layout in `deduplication.md` exactly. Downstream stages
consume `data/catalogue-dedup/deduplicated.jsonl`.

~40 lines of code.

## Stage 4: adjudicate-screening

### Models (`adjudication/models.py`)

```python
class AdjudicationMeta(_Base):
    tool: str = "laglitsynth.screening_adjudication.screening"
    tool_version: str = "alpha"
    adjudicated_at: str
    mode: str = "pass_through"
    input_count: int
    output_count: int
    human_reviewed: int = 0
```

### CLI

```
laglitsynth screening-adjudication \
    --input data/screening-abstracts/accepted.jsonl \
    --output-dir data/screening-adjudication/
```

### Behavior

1. Read all works from `--input` via `read_works_jsonl`.
2. Write them unchanged to `<output-dir>/included.jsonl` via `write_jsonl`.
3. Write `AdjudicationMeta` to `<output-dir>/adjudication-meta.json`.
4. Print summary to stderr.

~40 lines of code.

## Stage 5: retrieve

### Models (`fulltext/models.py`)

```python
class RetrievalStatus(str, Enum):
    retrieved_oa = "retrieved_oa"
    retrieved_unpaywall = "retrieved_unpaywall"
    retrieved_preprint = "retrieved_preprint"
    retrieved_manual = "retrieved_manual"
    abstract_only = "abstract_only"
    failed = "failed"

class RetrievalRecord(_Base):
    work_id: str
    retrieval_status: RetrievalStatus
    source_url: str | None = None
    pdf_path: str | None = None
    error: str | None = None
    retrieved_at: str

class RetrievalMeta(_Base):
    tool: str = "laglitsynth.fulltext_retrieval.retrieve"
    tool_version: str = "alpha"
    retrieved_at: str
    total_works: int
    retrieved_count: int
    abstract_only_count: int
    failed_count: int
    by_source: dict[str, int]
```

Stage 6 models go in the same file (TextSection, ExtractedDocument,
ExtractionMeta) per [full-text-extraction.md](../docs/full-text-extraction.md).
Defined now so the module is complete; implementation deferred.

### CLI

```
laglitsynth fulltext-retrieval \
    --input data/screening-adjudication/included.jsonl \
    --output-dir data/fulltext-retrieval/ \
    --email user@example.com \
    [--manual-dir data/fulltext-retrieval/manual/] \
    [--skip-existing] \
    [--dry-run]
```

### Source cascade

For each work, try in order, stop at first success:

1. **Manual directory** -- check `<manual-dir>/<work_id>.pdf`. Status:
   `retrieved_manual`. Runs first so re-runs pick up manually placed PDFs.
   Copy (not move) to `pdfs/` so the manual dir stays intact.
2. **OpenAlex OA URLs** -- try `work.primary_location.pdf_url` then
   `work.open_access.oa_url`. HTTP GET. Status: `retrieved_oa`.
3. **Unpaywall** -- if work has a DOI, query
   `https://api.unpaywall.org/v2/{doi}?email={email}`. Use
   `best_oa_location.url_for_pdf`. Status: `retrieved_unpaywall`.
4. **No source** -- mark `abstract_only`.

Preprint detection deferred.

### PDF download

- `httpx.Client` (synchronous, matching the sequential pattern).
- `User-Agent: laglitsynth/0.1 (mailto:{email})`.
- Validate response: content starts with `%PDF` or content-type is
  `application/pdf`.
- Stream to temp file, rename to final path (atomic write).
- On non-200, timeout, or validation failure: raise, caught by the
  per-work error handler.

### Rate limiting

Domain-keyed timestamp dict, 1 req/s per domain:

```python
_last_request: dict[str, float] = {}

def _rate_limit(domain: str, min_interval: float = 1.0) -> None:
    now = time.monotonic()
    last = _last_request.get(domain, 0.0)
    if now - last < min_interval:
        time.sleep(min_interval - (now - last))
    _last_request[domain] = time.monotonic()
```

### Resumability (`--skip-existing`)

Load existing `retrieval.jsonl`, build `set[str]` of work IDs with any
`retrieved_*` status. Skip those. Works with `failed` or `abstract_only`
are retried (a manual PDF may have appeared, or a transient error resolved).

### Streaming output

Write one `RetrievalRecord` per work immediately after processing (not
buffered). Use the new `append_jsonl` helper. Partial runs produce valid
JSONL.

### Unretrieved list

After the loop, write DOIs for `abstract_only` and `failed` works to
`<output-dir>/unretrieved.txt`, one per line, with the expected manual
filename.

### Work ID extraction

OpenAlex IDs are URLs like `https://openalex.org/W1234567890`. Use the
`W...` suffix as the PDF filename. A helper `work_id_to_filename(id) -> str`
extracts it.

## Changes to io.py

```python
def append_jsonl(record: BaseModel, path: Path) -> None:
    """Append a single record to a JSONL file, creating it if needed."""

def read_jsonl(path: Path, model: type[T]) -> Iterator[T]:
    """Yield validated records of type T from a JSONL file."""
```

`read_works_jsonl` stays as-is (it works, no need to touch it).

## Changes to pyproject.toml

Add dependencies:

```
"httpx>=0.27",
"lxml>=5.0",
```

Add mypy override:

```toml
[[tool.mypy.overrides]]
module = ["lxml", "lxml.*"]
ignore_missing_imports = true
```

## Build order

Each step is a testable, committable increment:

1. Models: `dedup/models.py` + `adjudication/models.py` +
   `fulltext/models.py`. Typecheck.
2. `io.py` additions: `append_jsonl`, `read_jsonl`. Tests.
3. Stage 2: `dedup/deduplicate.py`, register in `cli.py`. Tests.
4. Stage 4: `adjudication/screening.py`, register in `cli.py`. Tests.
5. Stage 5: `fulltext/retrieve.py`, register in `cli.py`, add deps to
   `pyproject.toml`. Tests.

## Testing

### Stage 2

- Pass-all writes all works unchanged.
- `dropped.jsonl` is empty.
- Meta records `duplicates_removed=0`, `by_rule={}`, correct counts.
- Empty input produces empty output + valid meta.

### Stage 4

- Pass-through copies all works unchanged.
- Meta records `mode="pass_through"`, correct counts, `human_reviewed=0`.
- Empty input produces empty output + valid meta.

### Stage 5

- Retrieve from OA URL (mock httpx, verify `retrieved_oa` + PDF on disk).
- Unpaywall fallback (mock API + download, verify `retrieved_unpaywall`).
- Manual pickup (place file in manual dir, verify `retrieved_manual` + copy).
- No source produces `abstract_only`.
- HTTP 403 produces `failed` with error message.
- `--skip-existing` skips `retrieved_*`, retries `failed`/`abstract_only`.
- `unretrieved.txt` lists correct DOIs.
- Rate limiting sleeps when called in quick succession (mock `time.sleep`).

All HTTP calls mocked. No real network in tests.

## Verification

```sh
pixi run typecheck            # mypy --strict passes
pixi run test                 # all tests pass

# Manual smoke test for stage 2:
laglitsynth catalogue-dedup \
    --input <any-works.jsonl> \
    --output-dir /tmp/dedup-test/
cat /tmp/dedup-test/dedup-meta.json

# Manual smoke test for stage 4:
laglitsynth screening-adjudication \
    --input /tmp/dedup-test/deduplicated.jsonl \
    --output-dir /tmp/adj-test/
cat /tmp/adj-test/adjudication-meta.json

# Manual smoke test for stage 5 (needs real data):
laglitsynth fulltext-retrieval \
    --input /tmp/adj-test/included.jsonl \
    --output-dir /tmp/retrieval-test/ \
    --email mail@example.com \
    --dry-run
```
