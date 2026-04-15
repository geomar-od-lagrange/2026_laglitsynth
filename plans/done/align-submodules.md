# Plan: Align submodule structure and conventions

The `openalex` and `llmfilter` submodules follow the same pipeline pattern
(ingest/process, validate, write JSONL + sidecar) but were built at different
times and have diverged in structure, naming, error handling, and how they use
shared utilities. This plan defines a common internal contract and lists the
specific changes needed to bring both submodules into alignment. Any future
pipeline component should follow the same contract.

## Findings: current misalignments

### 1. Metadata sidecars use raw dicts, not Pydantic models

Both `fetch.py:_write_metadata()` and `filter.py:run()` build metadata as
hand-assembled `dict` literals and dump them with `json.dump`. This violates
the project principle "Typed data, not dicts." A typo in a key name, a missing
field, or a type mismatch would pass silently.

### 2. JSONL writing is not shared

`fetch.py` defines `write_jsonl(works, output) -> int`. `filter.py` inlines
its own write logic (three separate `open()` calls for output, verdicts, and
reject files, each doing `model.model_dump_json() + "\n"`). Meanwhile,
`io.py` only has `read_works_jsonl`. The write side has no shared utility.

### 3. Preflight checks are structurally different

- `fetch.py:run()` checks for `OPENALEX_API_KEY` inline at the top of `run()`,
  prints to stderr, and calls `sys.exit(1)`.
- `filter.py` extracts the check into a named function `_check_ollama()` that
  makes a network call, prints to stderr, and calls `sys.exit(1)`.

Both are "preflight checks" but one is inline, the other extracted. Neither
raises an exception -- both hard-exit, which makes them untestable.

### 4. Progress reporting patterns differ

- `fetch.py` prints a count every 500 records: `fetched 500 / 12000 ...`
- `filter.py` prints a line per work: `[42/1337] accepted (score: 87) -- ...`

The per-work reporting is appropriate for the slow LLM step; the batch
reporting is appropriate for the fast API fetch. But the formatting, the
mechanism (raw `print` to stderr), and the total-count discovery differ.
`filter.py` pre-scans the entire input to get a total count (reading the file
twice); `fetch.py` gets the total from the API.

### 5. `_default_output_path` signatures differ

- `fetch.py:_default_output_path(query: str) -> Path` -- derives slug from
  query string, outputs to `data/openalex/`.
- `filter.py:_default_output_path(input_path: Path) -> Path` -- derives stem
  from input filename, outputs to `data/filtered/`.

The inputs differ because the semantics differ (one creates from a query, the
other derives from an input file). This is fine. But both should follow the
same naming pattern: `_default_output_path` returning a `Path` with a
timestamp, in a conventional subdirectory of `data/`.

### 6. `run()` functions have different shapes

- `fetch.py:run()` calls `load_dotenv()`, does the API key check, calls
  `search_openalex()` then `write_jsonl()`, then `_write_metadata()`.
  Clean separation between iteration and writing.
- `filter.py:run()` calls `_check_ollama()`, then does all processing and
  writing inline in a single loop with manual file handle management
  (`open`/`close` in try/finally). The `filter_works()` generator exists
  but is unused by `run()` -- `run()` reimplements the same logic inline.

### 7. File open mode inconsistency

`fetch.py:write_jsonl` uses `open(output, "x")` (exclusive create -- fail if
exists). `filter.py:run()` also uses `"x"` mode but only after a manual
`output.exists()` check, making the exists-check redundant.

### 8. `filter_works()` is dead code

`filter.py` defines `filter_works()` as a clean generator (the original plan's
design), but `run()` does not call it. Instead, `run()` reimplements the same
iteration inline because it needs to write to multiple files simultaneously.
This means the generator exists only for potential external callers or tests,
but diverges from what `run()` actually does.

### 9. `_Base` model defined in two places

Both `openalex/models.py` and `llmfilter/models.py` define their own `_Base`
class with identical `ConfigDict(extra="ignore")`. This should be shared.

### 10. CLI arg naming: `--max-results` vs `--limit`

Both cap how many records get processed. Same concept, different names.
Unify to `--max-records` in both submodules.

---

## Proposed internal contract

Every pipeline submodule should follow this structure:

### File layout

```
src/laglitsynth/<component>/
    __init__.py          # empty
    models.py            # Pydantic models for this component's data
    <verb>.py            # core logic + CLI wiring (fetch.py, filter.py, ...)
```

### Shared base model

A single `_Base` in `src/laglitsynth/models.py` (new file):

```python
from pydantic import BaseModel, ConfigDict

class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")
```

All component models inherit from it.

### Shared I/O utilities in `src/laglitsynth/io.py`

- `read_jsonl(path, model_class) -> Iterator[T]` -- generic JSONL reader
  (replaces the current `read_works_jsonl` which is `Work`-specific).
  `read_works_jsonl` becomes a thin wrapper or is replaced at call sites.
- `write_jsonl(records: Iterator[BaseModel], path: Path) -> int` -- generic
  JSONL writer. Uses exclusive-create mode (`"x"`). Returns record count.
  Moved from `fetch.py`.
- `write_meta(path: Path, meta: BaseModel) -> None` -- writes a Pydantic
  metadata model as indented JSON. Replaces raw-dict metadata writing in both
  submodules.

### Metadata sidecar models

Each component defines a typed metadata model in its `models.py`:

```python
# openalex/models.py
class FetchMeta(_Base):
    tool: str = "laglitsynth.openalex.fetch"
    tool_version: str = "alpha"
    query: str
    fetched_at: str
    total_count: int
    records_written: int

# llmfilter/models.py
class FilterMeta(_Base):
    tool: str = "laglitsynth.llmfilter.filter"
    tool_version: str = "alpha"
    prompt: str
    model: str
    threshold: int
    filtered_at: str
    accepted_count: int
    rejected_count: int
    skipped_count: int
```

### Preflight checks

Each component that needs a preflight check defines a `_preflight(args) -> None`
function that raises `SystemExit` with a clear message on failure. The pattern:

```python
def _preflight(args: argparse.Namespace) -> None:
    """Verify external dependencies before processing."""
    ...  # raise SystemExit("message") on failure
```

`fetch.py` extracts its API key check into `_preflight()`. `filter.py` renames
`_check_ollama` to `_preflight()` and takes `args` instead of individual
parameters. Both raise `SystemExit` instead of calling `sys.exit()` so they
are testable with `pytest.raises(SystemExit)`.

### `run()` shape

Every `run(args)` follows this sequence:

1. `_preflight(args)` -- fail fast
2. Resolve output path (default or from args)
3. Check output does not exist (rely on `"x"` mode, remove redundant
   `exists()` checks)
4. Print run parameters to stderr
5. Process: call the core generator/function, write output via shared `io`
   utilities
6. Write metadata sidecar via `write_meta()`
7. Print summary to stderr

### `build_subparser()` contract

Unchanged -- this pattern already works well in both submodules. Each module
defines `build_subparser(subparsers) -> ArgumentParser` and
`run(args: Namespace) -> None`. `cli.py` imports and wires them.

---

## Specific modifications

### Phase 1: Shared infrastructure

1. **Create `src/laglitsynth/models.py`** with the shared `_Base` model class.

2. **Expand `src/laglitsynth/io.py`**:
   - Add `write_jsonl(records: Iterable[BaseModel], path: Path) -> int`.
     Uses `open(path, "x")`, writes `record.model_dump_json() + "\n"` per
     record, returns count. Moved from `fetch.py:write_jsonl` and
     generalized (accept any `BaseModel`, not just `Work`).
   - Add `write_meta(path: Path, meta: BaseModel) -> None`. Writes
     `meta.model_dump_json(indent=2) + "\n"` to the given path. Replaces
     raw-dict metadata writing.
   - Keep `read_works_jsonl` as-is for now (it is used in multiple places
     and a generic version would require a type parameter that complicates
     call sites). Optionally add a generic `read_jsonl(path, model_class)`
     alongside it.

### Phase 2: Align openalex submodule

3. **`openalex/models.py`**: Import `_Base` from `laglitsynth.models` instead
   of defining it locally. Add `FetchMeta` model.

4. **`openalex/fetch.py`**:
   - Remove `write_jsonl()` -- use `laglitsynth.io.write_jsonl` instead.
   - Remove `_write_metadata()` -- use `laglitsynth.io.write_meta` with a
     `FetchMeta` instance instead.
   - Extract the API key check into `_preflight(args)`. Have it raise
     `SystemExit` instead of calling `sys.exit(1)`.
   - Move `load_dotenv()` call into `_preflight()` (it must run before the
     env var check).
   - Rename `--max-results` to `--max-records`.

### Phase 3: Align llmfilter submodule

5. **`llmfilter/models.py`**: Import `_Base` from `laglitsynth.models` instead
   of defining it locally. Add `FilterMeta` model.

6. **`llmfilter/filter.py`**:
   - Rename `_check_ollama` to `_preflight(args)`. Have it raise `SystemExit`
     instead of calling `sys.exit(1)`.
   - Refactor `run()` to use `write_jsonl` from `io.py` for the accepted
     output file. The verdicts file and reject file also use the same
     pattern but write different model types, so they can use the same
     shared `write_jsonl` or a context-manager approach. The key goal: stop
     managing raw file handles with manual open/close.
   - Replace the inline metadata dict with `FilterMeta` model + `write_meta()`.
   - Remove the redundant `output.exists()` check (the `"x"` mode in
     `write_jsonl` already handles this).
   - Rename `--limit` to `--max-records`.
   - Refactor `run()` to actually call `filter_works()`. The generator is the
     testable unit — `run()` should consume it and handle I/O (writing output,
     verdicts, rejects). This keeps classification logic unit-testable without
     needing to mock file I/O.

### Phase 4: Update docs

7. **Docs updates**:
   - `docs/data-layout.md`: note that `.meta.json` files are now typed
     Pydantic models (`FetchMeta`, `FilterMeta`) rather than ad-hoc dicts.
   - `docs/data-model.md` (or a new section in `docs/README.md`): make
     explicit that the pipeline builds on the OpenAlex data model. `Work`
     and its nested types are OpenAlex's schema expressed as Pydantic models
     — not a generic abstraction. All downstream components consume `Work`
     records. This matters because sharing `_Base` at the package level
     could give the false impression that the data model is pipeline-generic.
   - Update CLI arg docs to reflect `--max-records` (replaces
     `--max-results` in fetch and `--limit` in filter).

---

## What this plan does NOT cover

- Adding a shared progress-reporting abstraction. The two submodules have
  genuinely different progress patterns (batch vs. per-item) and a shared
  abstraction would be premature. Both already print to stderr, which is
  sufficient.
- Async or concurrent processing.
- Changes to the `cli.py` dispatcher (it is already clean and minimal).
- Test changes (tests should be updated to match, but the test plan follows
  from the code changes above).
