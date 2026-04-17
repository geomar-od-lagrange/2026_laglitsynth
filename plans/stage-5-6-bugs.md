# Plan: stage 5 / stage 6 bug cluster

## Goal

Fix a cluster of small bugs and rough edges in
[stage 5 `fulltext-retrieval`](../src/laglitsynth/fulltext_retrieval/)
and
[stage 6 `fulltext-extraction`](../src/laglitsynth/fulltext_extraction/)
surfaced by the three-agent review
([plans/review-2026-04-16.md](review-2026-04-16.md) theme 4, plus I-5 /
I-6 / I-7 in
[review-2026-04-16-system.md](review-2026-04-16-system.md)). The fixes
are each individually small but several silently bake wrong data into
downstream artifacts (unreachable `failed` status, duplicated rows in
`retrieval.jsonl`, corrupt `work_id` from stray PDF names, HTML pages
accepted as PDFs). After this plan the two stages behave the way their
docs and models already describe; nothing in their external CLI shape
changes (the flag-don't-filter cutover handles that in a separate
plan), and no new provenance / quality-gate concerns are opened.

## Non-goals

These are real review findings but explicitly out of scope for this
plan. Each is tracked elsewhere or deferred.

- Flag-don't-filter cutover and the stage 5 CLI change from `--input`
  to `--data-dir`. Tracked in
  [plans/flag-dont-filter-cutover.md](flag-dont-filter-cutover.md).
- Reproducibility / `_RunMeta` refactor (git commit SHA, input paths,
  LLM sampling params, prompt digests, `content_sha256` on
  `RetrievalRecord`, `source_pdf_sha256` on `ExtractedDocument`, full
  GROBID params in `ExtractionMeta`). See
  [review-2026-04-16-system.md](review-2026-04-16-system.md) R-1..R-20
  and D-7.
- Stage 6 quality gate on `ExtractedDocument`
  (`extraction_status` enum + quality metrics). See D-1 in
  [review-2026-04-16-system.md](review-2026-04-16-system.md) and
  [docs/full-text-extraction.md](../docs/full-text-extraction.md)
  lines 59–73.
- `Work.authorships` / `Work.biblio` default-value fix and the
  `updated_date: str` vs `publication_date: date` inconsistency.
- `_Base` deletion and per-model `ConfigDict(extra="ignore" |
  "forbid")` split. See D-6.
- Documentation filename renames
  (`full-text-retrieval.md` → `fulltext-retrieval.md`, etc.) and
  [docs/pipeline.md](../docs/pipeline.md) / [README.md](../README.md)
  terminology sweeps.
- Resume-on-crash integration test for stage 5 (kept — see Test
  migration).
- Stage 3 / stage 4 mode-`"x"` fixes. Same class of bug, but belongs
  to the flag-don't-filter cutover which rewrites those `run()`
  bodies anyway.

## Coordination with flag-don't-filter cutover

This plan is designed to land independently of
[flag-dont-filter-cutover.md](flag-dont-filter-cutover.md). The
stage 5 CLI surface is not touched here: `--input` /
`--skip-existing` / `--dry-run` stay. All changes in this plan are
internal to
[src/laglitsynth/fulltext_retrieval/](../src/laglitsynth/fulltext_retrieval/)
and
[src/laglitsynth/fulltext_extraction/](../src/laglitsynth/fulltext_extraction/),
plus the new shared [src/laglitsynth/ids.py](../src/laglitsynth/ids.py)
module.

Ordering assumption: this plan lands first. Rationale — the cutover
plan rewrites stage 5 `run()` to take `--data-dir`, and it is easier
to do that rewrite once the bugs are fixed and the retrieval logic
already uses `_RateLimiter`, a tightened `_validate_pdf`, the new
`unretrieved.txt` derivation, and `write_retrieval_jsonl` at end of
run. If the cutover lands first instead, the merge cost is the
conflict in `retrieve.run()` — resolvable by hand. Not a blocker
either way.

Collisions to call out for whoever merges second:

- [src/laglitsynth/fulltext_retrieval/retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py)
  `run()` body: both plans rewrite it.
- [src/laglitsynth/ids.py](../src/laglitsynth/ids.py): this plan
  creates it; the cutover plan's `resolve.py` may want to import
  `filename_to_work_id` to report manual PDFs on disk that don't
  match a known work.

## Target state

- [src/laglitsynth/ids.py](../src/laglitsynth/ids.py) exists and
  exposes `work_id_to_filename(work_id: str) -> str` and
  `filename_to_work_id(stem: str) -> str | None`. Stage 5 and stage 6
  both import from it; the duplicated `work_id_to_filename` helper in
  [retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py) is
  gone and the `f"https://openalex.org/{pdf.stem}"` construction in
  [extract.py](../src/laglitsynth/fulltext_extraction/extract.py) is
  gone.
- Stage 5 `_retrieve_one` produces `RetrievalStatus.failed` when at
  least one source was attempted (manual / OA / Unpaywall) and every
  attempt raised; the record's `error` field carries the last
  exception's message. `abstract_only` is reserved for works where no
  source was even attempted (no manual file, no OA URL, no DOI).
- Stage 5 writes `retrieval.jsonl` exactly once at end of run, from
  the in-memory list, merged with previously-existing records under
  `--skip-existing`. No duplicate `work_id` rows across re-runs.
- Stage 5 writes `unretrieved.txt` from the final in-memory record
  list (union of skipped + newly-processed records), filtered to
  `status in {abstract_only, failed}`. No filesystem scan; populated
  correctly under `--dry-run` and `--skip-existing`.
- Stage 5 `_validate_pdf` returns `True` only when `data[:4] ==
  b"%PDF"`. Content-type is ignored.
- Stage 5 DOI normalisation strips `https?://(dx.)?doi.org/` with a
  case-insensitive regex.
- Stage 5 rate-limit state is encapsulated in a `_RateLimiter` class
  instantiated per `run()` call. The module-level `_last_request`
  dict is gone.
- Stage 5 manual-first cascade stays as-is (manual → OA → Unpaywall);
  [docs/full-text-retrieval.md](../docs/full-text-retrieval.md) is
  updated to describe manual-first with a one-line rationale.
- Stage 5 `--dry-run` status synthesis drops the "has-DOI implies
  retrieved_unpaywall" branch; any work whose only plausible source
  is Unpaywall is reported as `abstract_only` in the dry-run record.
  (Rationale in Design decisions.)
- Stage 5 meta summary heading capitalisation is aligned with
  [docs/full-text-retrieval.md](../docs/full-text-retrieval.md).
- Stage 5 exclusive-create writes (if any remain in this stage) print
  a friendly message rather than a raw `FileExistsError`. (Applies to
  `write_meta` / `write_retrieval_jsonl` if they use mode `"x"` —
  inspect during implementation; current code uses mode `"w"` for
  the meta and append for the jsonl, so this sub-item may be a
  no-op in stage 5 specifically. Keep the check as a cheap guard.)
- Stage 6 skips PDFs whose stem does not match `^W\d+$` with a
  `logger.warning` and a stderr line. Valid PDFs are processed as
  before; the file is not counted as failed.
- Stage 6 GROBID preflight
  (`/api/isalive`, `/api/version`) runs against an `httpx.Client`
  configured with a short preflight timeout (5 seconds); per-paper
  POSTs continue to use `args.timeout`.
- Stage 6 `parse_tei` uses flat (direct-child) enumeration of
  `<body>/<div>` and `<div>/<p>`, with a short comment stating this
  matches the "flat list of sections" doc contract.
- Stage 6 `parse_tei` constructs the `lxml` parser with
  `resolve_entities=False` and `no_network=True` before calling
  `etree.fromstring`.

## Design decisions

### D-5.1 — `RetrievalStatus.failed` wiring

Wire it in. `_retrieve_one` tracks whether any source was attempted
and captures the last exception; when at least one attempt happened
and none succeeded, return a record with `status=failed` and
`error=str(last_exc)`. When no source was attempted at all (no
manual file, no OA URL, no DOI), return `status=abstract_only`.
Rationale: `failed` vs `abstract_only` is a load-bearing distinction
for PRISMA reporting in
[docs/full-text-retrieval.md](../docs/full-text-retrieval.md) lines
84–87, and the enum value already exists. Deleting it would be
throwing away signal.

### D-5.2 — `retrieval.jsonl` rewrite strategy

Build the full `list[RetrievalRecord]` in memory (seeded from any
existing file under `--skip-existing`), then write it out with
`write_jsonl`-equivalent overwrite semantics at end of run. Rationale:
the working set is bounded by the input catalogue; re-running twice
must not grow the file; the meta counts already derive from the
in-memory record list so one source of truth is cleanest.
Deduplication-by-`work_id`-at-write is not used because it pushes
the "which record wins" decision into the writer; an explicit
"replace on re-processed, keep on skip" rule in the caller is
clearer.

### D-5.3 — `unretrieved.txt` derivation

Derive from the final in-memory record list
(`skipped + newly_processed`), filter to
`status in {abstract_only, failed}`, and write one DOI-plus-filename
line per matching record. Open for plain overwrite (mode `"w"`).
Under `--dry-run`, use the synthetic record statuses produced by the
dry-run branch. Rationale: single source of truth, matches the
`RetrievalRecord` contract, works uniformly under both `--dry-run`
and `--skip-existing`.

### D-5.4 — `_validate_pdf` tightening

Keep only the `%PDF` magic-bytes check. Rationale: content-type is
hints-not-gospel; CDN error pages with
`Content-Type: application/pdf` should not pass. Size checks and
content hashes are deferred to the reproducibility plan.

### D-5.5 — DOI normalisation

Replace `doi.replace("https://doi.org/", "")` with
`re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.IGNORECASE)`.
Rationale: one-liner, handles the realistic input variations, no
external dependency.

### D-5.6 — rate-limit encapsulation

Introduce a `_RateLimiter` class with a
`wait(domain: str, min_interval: float = 1.0) -> None` method and a
`dict[str, float]` of last-request times. Instantiate once per
`run()` invocation and pass into `_download_pdf` /
`_try_unpaywall`. The module-level `_last_request` dict and the
module-level `_rate_limit` function are removed. Tests construct the
class directly. Rationale: no module-global state; tests don't need
the `mod._last_request.clear()` poke that
[tests/test_fulltext_retrieval.py](../tests/test_fulltext_retrieval.py)
line 298 currently does.

### D-5.7 — cascade order (manual-first vs OA-first)

Keep the code's manual-first order; update
[docs/full-text-retrieval.md](../docs/full-text-retrieval.md) to
match with a one-line rationale. Rationale: the review calls
manual-first "the pragmatic choice" because manual files are local
and cheap and deliberately placed by a human (implying authority);
no reason to regress that behaviour just to match stale text.

### D-5.8 — `--dry-run` synthetic status

Remove the "has DOI implies `retrieved_unpaywall`" branch. Dry-run
records fall into three buckets only:
`retrieved_manual` (manual file is on disk),
`retrieved_oa` (at least one OA URL is present on the `Work`),
`abstract_only` (nothing else). Rationale: DOI presence does not
imply Unpaywall serves a PDF; the old mapping caused the dry-run
meta counts to over-report `retrieved_unpaywall`. Keeping the dry-run
honest is more useful than a third synthetic enum value
(`would_attempt_unpaywall`) that doesn't exist in the real retrieval
path.

### D-5.9 — friendly exclusive-create error

Wrap any remaining `open(..., "x")` or `write_jsonl(...)` call in
stage 5 with a `try/except FileExistsError` that prints
`f"{path} already exists; remove it or use --skip-existing to
resume."` and exits with `SystemExit(1)`. Rationale: the default
Python `FileExistsError` trace is not a user-friendly fail-fast for
a CLI. Stage 5 today uses `append_jsonl` and `write_meta`
(overwrite), so this may be a no-op in practice — keep the check as
a cheap guard that survives future refactors.

### D-5.10 — meta summary capitalisation

Align the stderr summary prints in
[retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py)
with the doc's capitalisation (`Retrieved (OA)`, `Retrieved
(Unpaywall)`, `Retrieved (preprint)`, `Retrieved (manual)`,
`Abstract-only`, `Failed`). Rationale: cosmetic parity between doc
and code — pick one, change the other to match, done.

### D-6.1 — stage 6 invalid PDF stem handling

Option (a): skip PDFs whose stem does not match `^W\d+$`; log at
`warning` level via `logger` and print a one-line stderr notice.
Update the `total` counter to reflect only processed PDFs (i.e.
exclude the skipped stems from the `total_pdfs` reported in
`ExtractionMeta`). Rationale: option (b) (drive from
`retrieval.jsonl`) requires cross-stage awareness that belongs in a
bigger refactor (the resolve module in the flag-don't-filter plan);
option (a) is the simpler fix that prevents corrupt `work_id`
values today.

### D-6.2 — GROBID preflight timeout

Instantiate a dedicated preflight `httpx.Client` with
`timeout=5.0` for the `/api/isalive` and `/api/version` calls. The
per-paper POST loop continues to use `httpx.Client(timeout=args.timeout)`.
Close both clients on exit. Rationale: a slow GROBID startup
shouldn't hang the preflight check for 120 seconds; 5 s is plenty
for a local-network `isalive` call. Do not share the client because
`httpx.Client.timeout` is per-client.

### D-6.3 — flat vs recursive section enumeration

Keep flat (direct-child) enumeration for both `<body>/<div>` and
`<div>/<p>`. Add a one-line comment above each `findall` explaining
the choice ("Flat: matches the 'flat list of sections' contract in
[docs/full-text-extraction.md](../docs/full-text-extraction.md)").
Rationale: the doc explicitly specifies flat output; recursion would
duplicate paragraph text across nested divs. Losing sub-section
titles is an accepted trade-off of the flat-list-of-sections
contract.

### D-6.4 — lxml parser hardening

Construct the parser explicitly:

```python
_TEI_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)
...
root = etree.fromstring(xml_bytes, parser=_TEI_PARSER)
```

Rationale: cheap defence-in-depth. GROBID output is locally sourced
and trusted in practice, but the parser's default behaviour resolves
external entities, which is the XXE footgun; disabling it costs one
line.

### D-shared — `laglitsynth.ids` signature

```python
# src/laglitsynth/ids.py
import re

_WORK_ID_PREFIX = "https://openalex.org/"
_STEM_RE = re.compile(r"^W\d+$")


def work_id_to_filename(work_id: str) -> str:
    """Return the filesystem stem for an OpenAlex work_id URL."""
    return work_id.rsplit("/", 1)[-1]


def filename_to_work_id(stem: str) -> str | None:
    """Return the OpenAlex work_id URL for a stem, or None if not a W-ID."""
    if not _STEM_RE.match(stem):
        return None
    return f"{_WORK_ID_PREFIX}{stem}"
```

Rationale: two functions, one regex, one constant. `filename_to_work_id`
returns `None` per AGENTS "None means None"; callers decide whether to
skip or error. Stage 5 imports `work_id_to_filename`; stage 6 imports
`filename_to_work_id`.

## Implementation sequence

Each step is intended to be `pixi run test` + `pixi run typecheck`
clean before the next step. Group by module. Commit at each numbered
step unless noted.

### Step 1 — shared ids module

1. Create [src/laglitsynth/ids.py](../src/laglitsynth/ids.py) with
   the two functions and constants from D-shared above.
2. Create [tests/test_ids.py](../tests/test_ids.py) covering:
   - `work_id_to_filename("https://openalex.org/W1234")` → `"W1234"`
   - `work_id_to_filename("W1234")` → `"W1234"` (bare id passthrough)
   - `filename_to_work_id("W1234")` → `"https://openalex.org/W1234"`
   - `filename_to_work_id("sample")` → `None`
   - `filename_to_work_id("W1234abc")` → `None`
   - `filename_to_work_id("")` → `None`
3. Update
   [src/laglitsynth/fulltext_retrieval/retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py):
   remove the local `work_id_to_filename` and import it from
   `laglitsynth.ids`. Keep the existing stage-5 tests green; they
   import `work_id_to_filename` from
   `laglitsynth.fulltext_retrieval.retrieve`, so either re-export it
   there (one-liner `from laglitsynth.ids import work_id_to_filename`
   at module top) or update the test imports. Pick re-export — it
   keeps the diff tight for this step.
4. `pixi run typecheck` + `pixi run test`, commit.

### Step 2 — stage 5 `_RateLimiter` class

1. In
   [retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py),
   replace the module-level `_last_request` dict and `_rate_limit`
   function with a `class _RateLimiter` exposing `wait(self, domain:
   str, min_interval: float = 1.0) -> None`.
2. Thread a `_RateLimiter` instance through `_download_pdf` and
   `_try_unpaywall` as a keyword argument. Instantiate once in
   `run()`.
3. Update `TestRateLimiting` in
   [tests/test_fulltext_retrieval.py](../tests/test_fulltext_retrieval.py)
   to construct a `_RateLimiter` directly, drop the
   `mod._last_request.clear()` call.
4. `pixi run typecheck` + `pixi run test`, commit.

### Step 3 — stage 5 `_validate_pdf` tightening

1. In
   [retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py),
   remove the content-type branch from `_validate_pdf`. Function
   body becomes `return data[:4] == b"%PDF"`.
2. Update
   [tests/test_fulltext_retrieval.py](../tests/test_fulltext_retrieval.py)
   `TestValidatePdf`: rename `test_content_type` to
   `test_rejects_html_with_pdf_content_type` and change the assertion
   to `is False`.
3. Add a new test: `test_rejects_html_body` — the body is HTML, the
   content-type header is `application/pdf`, expect `False`.
4. `pixi run typecheck` + `pixi run test`, commit.

### Step 4 — stage 5 DOI normalisation

1. In
   [retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py),
   replace `work.doi.replace("https://doi.org/", "")` with a
   `re.sub` using the pattern from D-5.5. Import `re` at the top of
   the file.
2. Add a test `TestDoiNormalisation` in
   [tests/test_fulltext_retrieval.py](../tests/test_fulltext_retrieval.py)
   that verifies Unpaywall is queried against a DOI with
   `http://`, `https://`, `dx.doi.org`, uppercase `HTTPS`, and
   already-bare forms. Either extract the normalisation into a
   small helper and test it directly, or assert on the URL passed
   to the mocked client.
3. `pixi run typecheck` + `pixi run test`, commit.

### Step 5 — stage 5 `RetrievalStatus.failed` wiring

1. In
   [retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py),
   change `_try_oa_urls` so it tracks `last_exc: Exception | None`
   and returns either `tuple[RetrievalStatus, str]`, `None` (no URLs
   attempted), or a new sentinel (e.g. raise a private
   `_AllAttemptsFailedError(last_exc)`). Mirror the pattern in
   `_try_unpaywall`.
2. Rewrite `_retrieve_one` to track whether any source was attempted
   and the last exception:
    - Start with `attempted = False`, `last_exc: Exception | None =
      None`.
    - Each helper that runs sets `attempted = True` and updates
      `last_exc` on failure.
    - If any helper succeeds, return the success record (unchanged).
    - After the cascade: if `attempted and last_exc is not None`,
      return a `RetrievalRecord` with `status=RetrievalStatus.failed`
      and `error=str(last_exc)`. Otherwise return the existing
      `abstract_only` record.
3. Update
   [tests/test_fulltext_retrieval.py](../tests/test_fulltext_retrieval.py)
   `TestFailedOnHttpError.test_http_403`:
    - Change the expected status to `RetrievalStatus.failed`.
    - Assert `record.error is not None` and `"403" in record.error`.
    - Update the comment to state that the OA URL was attempted and
      failed, so `failed` is the correct outcome.
4. Add a new test `test_unpaywall_download_fails_marks_failed`:
   Unpaywall API returns a `best_oa_location.url_for_pdf`, the PDF
   GET then fails with `httpx.HTTPStatusError`; assert
   `status == failed` and the error is captured.
5. `pixi run typecheck` + `pixi run test`, commit.

### Step 6 — stage 5 `retrieval.jsonl` end-of-run rewrite

1. In
   [retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py),
   rewrite `run()`:
    - Load existing records into `existing: dict[str, RetrievalRecord]`
      from `retrieval.jsonl` if it exists (regardless of
      `--skip-existing`, since we always want to preserve non-rerun
      rows).
    - Under `--skip-existing`, `skip_ids` is still
      `{work_id for work_id, rec in existing.items() if
      rec.retrieval_status.value.startswith("retrieved_")}`.
    - Accumulate newly-produced records in an `OrderedDict` keyed
      by `work_id` (the input catalogue order is the desired
      write order).
    - Final records = (for each work in input order) the new record
      if produced, else the existing record if present (i.e.
      skipped). Drop the `append_jsonl` call inside the loop.
    - At end of run, write the final list via a new
      `write_retrieval_jsonl(records, retrieval_path)` helper that
      opens the file in mode `"w"`, truncating the prior file.
      (Simplest: write to a tempfile in the same directory, then
      `rename`, to stay consistent with the PDF-download atomic-write
      pattern. Optional for this plan; plain `"w"` is acceptable.)
2. Remove the `failed_count` stale computation that counted by
   scanning statuses inside the loop; derive all counters from the
   final record list after the loop.
3. Add a new test `test_retrieval_jsonl_no_duplicates_on_rerun`:
   run twice against the same 2-work input, assert the file has 2
   lines and 2 distinct `work_id`s.
4. `pixi run typecheck` + `pixi run test`, commit.

### Step 7 — stage 5 `unretrieved.txt` derivation

1. In
   [retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py),
   replace the filesystem-scan block at the end of `run()` with a
   pass over the final record list:
    - For each record whose `retrieval_status in {abstract_only,
      failed}`, look up the corresponding `Work` (keep a
      `works_by_id` dict), compute the expected filename via
      `work_id_to_filename`, and write
      `f"{work.doi or 'no-doi'}\t{fname}.pdf\n"`.
    - Open for mode `"w"` (truncate per run).
    - Run under `--dry-run` too — the dry-run branch produces
      synthetic records with the same statuses, so the derivation
      works uniformly.
2. Update
   [tests/test_fulltext_retrieval.py](../tests/test_fulltext_retrieval.py)
   `TestUnretrievedTxt`: unchanged assertions, but add a sibling
   test that calls `run()` with `args.dry_run = True` and asserts
   `unretrieved.txt` is populated. Add a second sibling test
   exercising `--skip-existing`: pre-seed `retrieval.jsonl` with a
   `failed` record for W1 and a `retrieved_oa` record for W2, run
   with an input containing both W1 and W2 where W1 retries and
   still fails; assert `unretrieved.txt` contains W1 but not W2.
3. `pixi run typecheck` + `pixi run test`, commit.

### Step 8 — stage 5 dry-run status honesty

1. In
   [retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py)
   `_retrieve_one` dry-run branch, remove the
   `elif has_doi: status = retrieved_unpaywall` clause. The three
   buckets become `retrieved_manual`, `retrieved_oa`, and
   `abstract_only`.
2. Update / add a test that passes a DOI-only work (no manual, no
   OA) under `--dry-run` and asserts the synthetic record's status
   is `abstract_only`.
3. `pixi run typecheck` + `pixi run test`, commit.

### Step 9 — stage 5 exclusive-create friendly error (guard only)

1. Inspect
   [retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py)
   — the current code uses `append_jsonl` (mode `"a"`) and
   `write_meta` (mode `"w"`). If any `open(..., "x")` or
   `write_jsonl(...)` (mode `"x"` per
   [io.py](../src/laglitsynth/io.py) line 35) call is introduced by
   step 6, wrap it in a `try/except FileExistsError` that prints a
   friendly `SystemExit(1)` message. Otherwise this step is a no-op
   in stage 5 — document the rationale in the commit message and
   skip. (The same class of fix for stages 2/3/4 belongs to the
   flag-don't-filter plan.)
2. `pixi run typecheck` + `pixi run test`, commit only if code
   changed.

### Step 10 — stage 5 summary capitalisation and doc cascade order

1. In
   [retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py)
   summary-printing code, change the heading formatter so the
   emitted labels match
   [docs/full-text-retrieval.md](../docs/full-text-retrieval.md)
   lines 180–188 (`Retrieved (OA)`, `Retrieved (Unpaywall)`,
   `Retrieved (preprint)`, `Retrieved (manual)`, `Abstract-only`,
   `Failed`). Simplest implementation: keep a small
   `dict[RetrievalStatus, str]` mapping to the display label.
2. Edit
   [docs/full-text-retrieval.md](../docs/full-text-retrieval.md)
   "Source cascade" section: reorder so "Manual batch" is item 1,
   OA URLs 2, Unpaywall 3, preprints 4. Add a short rationale
   sentence under the new item 1: "Manual files live on local disk
   and are deliberately placed by a human; checking them first
   means re-runs pick them up cheaply and manual placement always
   wins."
3. `pixi run typecheck` + `pixi run test`, commit.

### Step 11 — stage 6 invalid-stem skip

1. In
   [extract.py](../src/laglitsynth/fulltext_extraction/extract.py),
   import `filename_to_work_id` from `laglitsynth.ids`.
2. In `run()`'s per-PDF loop, compute `work_id =
   filename_to_work_id(pdf.stem)`. If `None`, log a warning via
   `logger.warning` and print a one-line stderr notice; `continue`.
3. Track skipped-for-invalid-stem separately from `failed_count`
   (e.g. a local `invalid_stem_count` for the summary print).
4. Update `total_pdfs` in `ExtractionMeta` to be the count of PDFs
   that matched `^W\d+$` (i.e. the number of PDFs attempted).
   Alternatively: keep `total_pdfs` as `len(pdfs)` and add a new
   `invalid_stem_count` field to `ExtractionMeta`. Prefer the
   latter — it matches the "don't fabricate, don't hide" principle.
5. Add a test `test_invalid_stem_skipped` in
   [tests/test_fulltext_extraction.py](../tests/test_fulltext_extraction.py)
   that places a `sample.pdf` (invalid stem) and a `W1234.pdf` in
   `--pdf-dir`; mock GROBID; assert the extraction record exists
   only for W1234 and that `extraction.jsonl` has one line.
6. `pixi run typecheck` + `pixi run test`, commit.

### Step 12 — stage 6 preflight timeout

1. In
   [extract.py](../src/laglitsynth/fulltext_extraction/extract.py)
   `run()`, construct a second client:
   `preflight_client = httpx.Client(timeout=5.0)`; use it for
   `_grobid_health` and `_grobid_version`; close it after the
   preflight. The per-paper `client = httpx.Client(timeout=args.timeout)`
   is only constructed after the preflight succeeds, so a slow or
   unreachable GROBID never blocks for `args.timeout`.
2. `_grobid_health` and `_grobid_version` already accept a client
   argument — no signature change needed.
3. No new test (covering this would require network-time mocking);
   the behavioural change is small and inspection-visible.
4. `pixi run typecheck` + `pixi run test`, commit.

### Step 13 — stage 6 parse_tei flat-enumeration comments

1. In
   [extract.py](../src/laglitsynth/fulltext_extraction/extract.py)
   `parse_tei`, above the `divs = body.findall(f"{TEI_NS}div")`
   line, add a one-line comment: `# Flat: matches the "flat list of
   sections" contract in docs/full-text-extraction.md. Sub-section
   titles are intentionally dropped; paragraph text inside nested
   divs is not reached.`
2. Above `for p in div.findall(f"{TEI_NS}p"):` add
   `# Flat: only direct-child <p> of each top-level <div>.`
3. No code change; no test change.
4. Commit with steps 12 and 14 (cosmetic).

### Step 14 — stage 6 lxml hardening

1. In
   [extract.py](../src/laglitsynth/fulltext_extraction/extract.py),
   add a module-level
   `_TEI_PARSER = etree.XMLParser(resolve_entities=False,
   no_network=True)`.
2. In `parse_tei`, change the first line to
   `root = etree.fromstring(xml_bytes, parser=_TEI_PARSER)`.
3. Add a test `test_parse_tei_rejects_external_entity` in
   [tests/test_fulltext_extraction.py](../tests/test_fulltext_extraction.py):
   feed TEI that contains an `<!ENTITY>` with `SYSTEM` reference to
   a non-existent path; assert either `parse_tei` raises
   `etree.XMLSyntaxError` or the entity is not resolved (document
   whichever lxml actually does — the test pins current
   behaviour).
4. Add a test `test_parse_tei_rejects_malformed_xml`: feed bytes
   that are not valid XML (`b"not xml at all"`); assert
   `etree.XMLSyntaxError` is raised. This is a
   missing-coverage test flagged by the code review
   ([review-2026-04-16-code.md](review-2026-04-16-code.md) Stage 6
   testing gap).
5. `pixi run typecheck` + `pixi run test`, commit.

## Test migration

Rewrites and additions, grouped by test file. Reference back to the
code-review finding each test closes.

### [tests/test_ids.py](../tests/test_ids.py) (new)

Covers `work_id_to_filename` round-trip, `filename_to_work_id`
acceptance of `^W\d+$`, and rejection of stray stems ("sample",
empty string, alphanumeric garbage). Closes the shared-primitive
requirement in scope item 15.

### [tests/test_fulltext_retrieval.py](../tests/test_fulltext_retrieval.py) (modified)

- `TestValidatePdf.test_content_type` — renamed / rewritten: HTML
  body with `application/pdf` content-type must return `False`.
  Closes the code review's "HTML-as-PDF" gap
  ([review-2026-04-16-code.md](review-2026-04-16-code.md) Stage 5
  `_validate_pdf`).
- `TestFailedOnHttpError.test_http_403` — rewritten: OA URL 403
  must yield `RetrievalStatus.failed` with a captured error, not
  `abstract_only`. Closes the "`failed` unreachable" finding and
  corrects the misleading comment flagged by the test-review
  section.
- `TestFailedOnHttpError.test_unpaywall_download_fails_marks_failed`
  — new: Unpaywall API returns a broken PDF URL; assert `failed`.
- `TestDoiNormalisation` — new class covering the five DOI prefix
  variants against Unpaywall.
- `TestRateLimiting` — rewritten to use `_RateLimiter` directly;
  drop the `mod._last_request.clear()` poke.
- `TestUnretrievedTxt.test_unretrieved_output` — keep.
- `TestUnretrievedTxt.test_unretrieved_output_dry_run` — new:
  `args.dry_run = True`, assert `unretrieved.txt` is populated
  from synthetic records.
- `TestUnretrievedTxt.test_unretrieved_output_skip_existing` — new:
  pre-seed `retrieval.jsonl`, run with `--skip-existing`, assert
  skipped-failed works are still listed. Closes I-7 in
  [review-2026-04-16-system.md](review-2026-04-16-system.md).
- `TestRetrievalJsonl` — new class:
    - `test_no_duplicates_on_rerun`: run twice, assert 2-work input
      yields exactly 2 rows with 2 distinct `work_id`s. Closes the
      "retrieval.jsonl duplicates" finding.
    - `test_preserves_existing_rows_under_skip_existing`: pre-seed
      a `retrieved_oa` record, run with `--skip-existing` and a new
      input containing the pre-seeded work plus a fresh work;
      assert both rows present in the rewritten file.
- `TestDryRunStatusHonesty` — new: DOI-only work under `--dry-run`
  must produce `abstract_only`, not `retrieved_unpaywall`.

### [tests/test_fulltext_extraction.py](../tests/test_fulltext_extraction.py) (modified)

- `test_parse_tei_rejects_malformed_xml` — new: feed bytes that
  aren't XML at all; assert `etree.XMLSyntaxError`. Closes the
  "malformed TEI" gap flagged in the code review.
- `test_parse_tei_rejects_external_entity` — new: pin
  `resolve_entities=False` behaviour.
- `test_invalid_stem_skipped` — new (integration-style,
  exercising `run()`): drop `sample.pdf` + `W1234.pdf` in the
  `--pdf-dir`, mock GROBID, assert the stray file is skipped with
  a warning and one extraction record is emitted for the valid
  stem. Closes I-5 in
  [review-2026-04-16-system.md](review-2026-04-16-system.md) and
  the stage 6 code-review finding.

### Deliberately deferred

- Resume-on-crash integration test for stage 5 (flagged by the code
  review as missing). The `TestRetrievalJsonl` tests above cover the
  append-dedup path end-to-end; a full crash-and-resume harness is
  more than this plan needs. Track as a follow-up under the
  reproducibility plan.
- `MagicMock(args)` → `_make_args` helper refactor. Cosmetic,
  touches every test file, belongs to a hygiene sweep.

## Risks and rollback

- `RetrievalStatus.failed` wiring could misclassify the
  "no-source-attempted" case (which should stay `abstract_only`) as
  `failed`. Mitigation: the `attempted` boolean is set inside each
  helper only when an actual request or file read happens, not when
  the helper returns immediately for lack of input
  (no DOI / no manual file / no OA URL).
- `retrieval.jsonl` end-of-run rewrite loses crash resilience (a
  crash mid-run drops all records produced this run). Mitigation:
  prior records (from the previous complete run) are preserved
  because they are loaded before the loop and carried into the
  output. The crash-resilience regression only affects the current
  run's in-flight results — consistent with how the meta file
  already works. If the regression bites, add a tempfile-plus-rename
  around the write or fall back to streaming `append_jsonl` plus an
  end-of-run dedup pass.
- Dropping the content-type fallback in `_validate_pdf` could
  reject a valid PDF served without a `%PDF` magic prefix in the
  first four bytes. That is vanishingly rare (valid PDFs start with
  `%PDF`); rollback would be a one-line revert.
- Stage 6 invalid-stem skip hides genuinely mislabelled PDFs that
  would have been processed (with a corrupt work_id) before.
  Mitigation: warning log plus stderr print makes the skip visible;
  operators can rename manually-retrieved PDFs to their OpenAlex W
  stem before rerunning, matching the pipeline's naming convention
  (see [docs/full-text-retrieval.md](../docs/full-text-retrieval.md)
  "Storage layout" section).

## Verification

- `pixi run test` passes; new tests added in steps 1, 3, 4, 5, 6,
  7, 8, 11, 14 all green.
- `pixi run typecheck` passes with no new ignores.
- Manual end-to-end dry-run:
  ```sh
  laglitsynth fulltext-retrieval \
      --input data/screening-adjudication/included.jsonl \
      --output-dir /tmp/retrieval-dryrun \
      --email test@example.com \
      --dry-run
  cat /tmp/retrieval-dryrun/unretrieved.txt
  cat /tmp/retrieval-dryrun/retrieval-meta.json
  ```
  Expect `unretrieved.txt` populated, `retrieved_unpaywall` absent
  from `by_source` (replaced by honest counts), summary labels
  matching the doc.
- Manual stage 6 smoke test with a stray `sample.pdf` dropped next
  to a real `W1234.pdf`: expect a `WARNING` log line about
  `sample.pdf`, one extraction record for `W1234`, and
  `ExtractionMeta.invalid_stem_count == 1`.
- Re-run stage 5 twice back-to-back against the same input:
  `wc -l data/fulltext-retrieval/retrieval.jsonl` equals the input
  work count, not twice it.

## Open questions

- `RetrievalRecord.error` is currently `str | None`. Wiring
  `failed` in means populating it from `str(exc)`. Acceptable to
  store the Python exception string verbatim (e.g. `"403
  Forbidden"`) or should it be structured (e.g. a small enum of
  `http_4xx | http_5xx | connect_error | validation_error`)?
  Recommending verbatim for this plan because the reproducibility
  plan will rework `RetrievalRecord` anyway; flagging in case Willi
  prefers to get structured errors in place now.
- Stage 6 `ExtractionMeta.invalid_stem_count` is a new field. Adding
  it is a model change, which the scope says to avoid. Alternative:
  deduct invalid stems from `total_pdfs` and add nothing. Adding
  the field is more faithful to the "carry missingness" principle;
  deducting is more conservative. Recommending the additional field;
  flagging because it straddles the scope line with the
  reproducibility plan.
- Under `--skip-existing`, the rewrite in step 6 will carry over
  records for works that are no longer in the input catalogue (e.g.
  the included set shrank between runs because a threshold moved).
  Two options: (a) preserve those records verbatim in the rewritten
  file — conservative, keeps the audit trail; (b) drop them — the
  file then exactly mirrors the current input. Recommending (a);
  flagging because it conflicts with the mental model "retrieval.jsonl
  mirrors the current run's input".
- Flat enumeration in `parse_tei` loses paragraph text inside
  nested `<div>`s entirely (the outer div is kept if it has any
  direct-child `<p>`, but inner-div paragraphs are invisible). The
  doc says "flat list of sections" but does not say "drop nested
  paragraph text". A middle ground would be: direct-child `<div>`s
  enumerated flat, but `<p>` collection recursive within each
  top-level div (`.//{TEI_NS}p`). The review locked flat; noting in
  case Willi wants the middle ground instead.
