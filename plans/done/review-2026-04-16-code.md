# Code review — 2026-04-16

Scope: per-stage implementation quality (stages 1–6), test suite, type
safety, repo hygiene. Read-only pass against the current `main`-ward
branch `wr/review-2026-04-16-01`. The review-running environment was able
to execute both `pixi run typecheck` and `pixi run test`; results are
reported inline.

Overall: the code is in good shape for a research tool. Stages 1–6 are
small, readable modules that track their docs closely. Types are strict
and clean (mypy `--strict` passes with no `type: ignore` anywhere in
`src/`). Tests pass and cover the main behaviours. The most consequential
issues are a few stage-5 behaviours that diverge from
[`full-text-retrieval.md`](../docs/full-text-retrieval.md), the
intentional-but-still-risky `mode="x"` writes on non-append paths, and
the yet-unreconciled gap between [`docs/interfaces.md`](../docs/interfaces.md)
(flag-don't-filter, verdict sidecars) and what the stage-3/4 code still
writes.

## Per-stage review

Each stage below uses the same rubric: does code match docs, does logic
make sense, what real bugs or rough edges exist, anything over- or
under-engineered. Priorities are `[blocker]` / `[should-fix]` / `[nit]`;
"questions" are phrased as questions rather than claims.

### Stage 1 — catalogue-fetch

Summary: concise, correct, and well-aligned with
[`docs/fetch-publications.md`](../docs/fetch-publications.md). Uses the
mature `pyalex` client rather than rolling an OpenAlex HTTP layer
(`Use what exists`), validates every record through the `Work` model,
and converts the inverted-index abstract to plain text at ingest
(`Plain text over clever encodings`). The default safety cap and the
pre/post warning about it are nice.

Findings:

- [should-fix] [`src/laglitsynth/catalogue_fetch/fetch.py:91`](../src/laglitsynth/catalogue_fetch/fetch.py) —
  `works_query.count()` is called before pagination, then the iterator
  re-runs the query with `.paginate(...)`. If `count()` fails (transient
  5xx) the whole fetch errors before any paging; if `count()` succeeds
  but the corpus mutates between `count()` and `paginate()`, the
  `{yielded} / {total}` progress line drifts. Neither is a real bug per
  se — just note that `total` is informational and not load-bearing.
- [should-fix] [`src/laglitsynth/catalogue_fetch/fetch.py:96–99`](../src/laglitsynth/catalogue_fetch/fetch.py) —
  `paginate(..., n_max=max_records)` already caps results server-side,
  and the inner `yielded >= max_records` guard also caps client-side.
  That double-cap is fine, but the early `return` inside the generator
  discards any remaining (already-fetched) records in the current page
  without surfacing them. If that's deliberate (hard cap), consider a
  comment; otherwise relying on `n_max` alone is simpler.
- [should-fix] [`src/laglitsynth/catalogue_fetch/fetch.py:32` (`FIELDS`)](../src/laglitsynth/catalogue_fetch/fetch.py) —
  the `Work` model in
  [`src/laglitsynth/catalogue_fetch/models.py:88`](../src/laglitsynth/catalogue_fetch/models.py)
  requires `authorships: list[Authorship]` (no default) and
  `biblio: Biblio` (no default). If OpenAlex ever returns a record
  missing those keys, the record is dropped. That is acceptable (the
  code logs and continues) but couples the schema of the upstream API
  to the local contract tightly. Consider making at least `biblio`
  default to an empty `Biblio()` so abstracts with sparse metadata
  survive ingest, per the `None means None` principle which says we
  should carry missingness rather than drop.
- [should-fix] [`src/laglitsynth/catalogue_fetch/fetch.py:155`](../src/laglitsynth/catalogue_fetch/fetch.py) —
  the `help=` string says `default: 199` but `default=None` is what
  argparse parses; the real default is applied in `run()`. The CLI
  `--help` output will therefore say "default: 199" even though
  `args.max_records` starts as `None`. Consider using
  `default=199` and setting `max_records_defaulted` based on whether
  the user passed the flag (e.g. via a sentinel). This is a small UX
  mismatch, not a bug.
- [nit] [`src/laglitsynth/catalogue_fetch/fetch.py:67`](../src/laglitsynth/catalogue_fetch/fetch.py) —
  `datetime.now(UTC).isoformat(timespec="microseconds").replace(":", "-")`
  produces filenames like `2026-04-16T19-09-00.000000+00-00`; the
  trailing `+00-00` is ugly but filesystem-safe. No action unless you
  ever parse these timestamps back.
- [nit] [`src/laglitsynth/catalogue_fetch/fetch.py:104–109`](../src/laglitsynth/catalogue_fetch/fetch.py) —
  `ValidationError` is caught per record which matches the doc
  ("Invalid records are logged and skipped"). Good.
- Good decision: `_preflight()` fails fast with the registration URL if
  `OPENALEX_API_KEY` is missing. Matches the "fail fast" wording in
  [`docs/fetch-publications.md`](../docs/fetch-publications.md).
- Good decision: the `tool_version: str = "alpha"` field in
  [`FetchMeta`](../src/laglitsynth/catalogue_fetch/models.py) is a
  cheap way to mark early-era artefacts.

### Stage 2 — catalogue-dedup

Summary: pass-all MVP as documented
([`docs/deduplication.md`](../docs/deduplication.md) — "Exact matching
only"). The code is small and does exactly what the plan committed to.
It could be a one-liner and isn't — the delta is reading through the
whole file into memory, which is the only thing to flag.

Findings:

- [should-fix] [`src/laglitsynth/catalogue_dedup/dedup.py:35`](../src/laglitsynth/catalogue_dedup/dedup.py) —
  `works = list(read_works_jsonl(args.input))` loads the whole
  catalogue into memory. For a research tool running on hundreds to a
  few thousand works this is fine, but the same `list(...)` pattern
  appears in stages 4 and 5
  ([retrieval `run` L288](../src/laglitsynth/fulltext_retrieval/retrieve.py),
  [adjudicate L35](../src/laglitsynth/screening_adjudication/adjudicate.py))
  — worth a comment that this is intentional for the MVP and the
  streaming `read_works_jsonl` iterator is available when it matters.
- [should-fix] [`src/laglitsynth/catalogue_dedup/dedup.py:38–39`](../src/laglitsynth/catalogue_dedup/dedup.py) —
  `write_jsonl` is wrapped around `open(..., "x")`
  ([io.py:35](../src/laglitsynth/io.py)); `mode="x"` raises if the
  output already exists. That makes `catalogue-dedup` non-idempotent:
  re-running fails loudly rather than overwriting. If that's
  deliberate (hygiene), it's worth a short note in the doc and a
  clearer error message — the default Python error ("FileExistsError:
  [Errno 17] File exists") isn't a user-friendly fail-fast.
- [should-fix] [`src/laglitsynth/catalogue_dedup/dedup.py:39`](../src/laglitsynth/catalogue_dedup/dedup.py) —
  `write_jsonl([], output_dir / "dropped.jsonl")` creates an empty
  file (zero bytes) with `mode="x"`. Fine, but
  [`docs/interfaces.md:56`](../docs/interfaces.md) declares the
  `dropped.jsonl` schema as `Work + merge reason (TBD)`. If the
  schema is TBD, consider not writing the file yet rather than
  committing to an empty-file contract downstream tooling might
  silently depend on.
- [nit] the `pr3-review-themes.md` plan mentioned a single
  `deduplicated_at` timestamp; this is consistent with the code.
- Good decision: using
  [`DeduplicationMeta`](../src/laglitsynth/catalogue_dedup/models.py)
  with `by_rule: dict[str, int]` means the schema won't change when
  real matching rules land — only the values will.

### Stage 3 — screening-abstracts

Summary: the most interesting stage to review. Behaviour matches
[`docs/filter-abstracts.md`](../docs/filter-abstracts.md) at face value,
but the stage hasn't yet been rewritten to the flag-don't-filter
pattern [`docs/interfaces.md:151–158`](../docs/interfaces.md) calls for.
That rewrite is documented as pending and is not a regression — just
the biggest outstanding structural gap.

Findings:

- [blocker] [`src/laglitsynth/screening_abstracts/screen.py:188–249`](../src/laglitsynth/screening_abstracts/screen.py) —
  The "Inconsistencies to resolve" section of
  [`docs/interfaces.md`](../docs/interfaces.md) is explicit:
  "The existing code uses timestamped filenames and splits
  accepted/rejected Work records into separate files. Both must
  change: write only verdicts, drop the Work-record split." The
  current `run()` still writes three JSONL files (accepted, rejected
  optional, verdicts) and a timestamped default path. Because nothing
  downstream has been wired against the new contract yet this is not
  urgent, but it is the single biggest doc/code drift. Call it a
  blocker for the "flag, don't filter" cutover, not for current
  usage.
- [should-fix] [`src/laglitsynth/screening_abstracts/screen.py:191–196`](../src/laglitsynth/screening_abstracts/screen.py) —
  Output files are opened with `"x"` (exclusive). There is no
  `--skip-existing`, so resuming a partial LLM run (i.e., the LLM
  made it through 800/1000 abstracts before crashing) requires
  deleting/moving outputs. Consider streaming writes + a resume
  keyed on `FilterVerdict.work_id` (the verdicts file is already
  written one-per-line; it could be the resume cursor). Even without
  full resume, opening in `"a"` and seeding from existing verdicts
  would prevent the current "one process crash ruins the run"
  failure mode.
- [should-fix] [`src/laglitsynth/screening_abstracts/screen.py:44`](../src/laglitsynth/screening_abstracts/screen.py) —
  `OpenAI(...)` is instantiated inside `classify_abstract()` for
  every work. That's one TCP handshake per abstract against Ollama;
  with hundreds of works this adds real latency. Move the client
  into `filter_works` and pass it down.
- [should-fix] [`src/laglitsynth/screening_abstracts/screen.py:67–70`](../src/laglitsynth/screening_abstracts/screen.py) —
  Any non-HTTP failure (e.g., transient 5xx from Ollama, timeout,
  network blip) would escape as a generic `Exception`, not
  `ClassifyError`. The `except (json.JSONDecodeError, KeyError,
  ValueError, TypeError)` at L67 only catches parse issues. At L92
  only `ClassifyError` is caught in `filter_works`, so a timeout on
  work N aborts the whole run. Is a fail-fast on transient errors
  the intended behaviour, or should transient errors be recorded as
  skip-with-reason and move on?
- [should-fix] [`src/laglitsynth/screening_abstracts/screen.py:97–99`](../src/laglitsynth/screening_abstracts/screen.py) —
  The verdict mutation via `verdict.model_copy(update=...)` is neat
  but subtle: `classify_abstract` returns with `accepted=False` as
  a placeholder, then `filter_works` overwrites it. Consider
  returning an `accepted=None`-by-default verdict from
  `classify_abstract` and setting `accepted` exactly once, in the
  caller. The comment `# caller sets this based on threshold` is
  correct but easy to miss.
- [should-fix] [`src/laglitsynth/screening_abstracts/screen.py:84–86`](../src/laglitsynth/screening_abstracts/screen.py) —
  `max_records` counts every work including those without abstracts.
  [`test_filter_works_max_records_counts_all`](../tests/test_screening_abstracts.py)
  pins this in as a test, which is good — the semantic is explicit.
  But from a user POV, "process only the first N works" probably
  means N LLM calls. Worth a docstring or help-text note.
- [should-fix] [`src/laglitsynth/screening_abstracts/screen.py:63`](../src/laglitsynth/screening_abstracts/screen.py) —
  `int(score)` will accept `int("42.9") → ValueError`, caught as
  `ClassifyError`, fine. But an LLM might return `85.0` (float):
  `int(85.0)` succeeds silently. Matters only if you want to pin
  "only integer scores allowed". Probably not worth changing.
- [nit] [`src/laglitsynth/screening_abstracts/screen.py:173`](../src/laglitsynth/screening_abstracts/screen.py) —
  `total = sum(1 for _ in read_works_jsonl(args.input))` iterates
  the whole file up-front only for the progress counter, then
  iterates it again in `filter_works`. Two passes over disk for a
  cosmetic counter. Consider dropping the total or caching the
  works list (memory already used by dedup).
- [nit] [`src/laglitsynth/screening_abstracts/screen.py:119`](../src/laglitsynth/screening_abstracts/screen.py) —
  default output path uses colon-replaced microsecond timestamps;
  given
  [`docs/interfaces.md:158`](../docs/interfaces.md) deprecates
  timestamped filenames anyway, this goes away on the planned
  rewrite.
- Good decision: `response_format={"type": "json_object"}` enforces
  JSON mode in the Ollama-backed endpoint. The `SYSTEM_PROMPT`
  pins the schema tightly; the unit tests exercise all three
  plausible LLM misbehaviours (malformed JSON, missing field, wrong
  type). The `ClassifyError` sentinel pattern is well-contained.

### Stage 4 — screening-adjudication

Summary: pass-through MVP, matches
[`docs/adjudication-screening.md`](../docs/adjudication-screening.md)
exactly. Functionally a copy, schematically a placeholder. The planned
`AdjudicationVerdict` model is not yet written, which is fine — the
doc says so.

Findings:

- [should-fix] [`src/laglitsynth/screening_adjudication/adjudicate.py:30–38`](../src/laglitsynth/screening_adjudication/adjudicate.py) —
  The stage currently reads the accepted works jsonl and copies it to
  `included.jsonl`. Per
  [`docs/interfaces.md:74–83`](../docs/interfaces.md) the target
  output is `verdicts.jsonl` (sidecar, not a new copy). Same
  flag-don't-filter remediation as stage 3. Not a bug today, because
  stage 5 reads `included.jsonl` — the contracts match — but stages
  3 and 4 need to migrate together, which this review recommends
  tracking as one change.
- [should-fix] [`src/laglitsynth/screening_adjudication/adjudicate.py:38`](../src/laglitsynth/screening_adjudication/adjudicate.py) —
  same `write_jsonl(..., mode="x")` non-idempotency as stage 2.
- [nit] [`src/laglitsynth/screening_adjudication/models.py:5`](../src/laglitsynth/screening_adjudication/models.py) —
  `tool: str = "laglitsynth.screening_adjudication.adjudicate"`
  is inconsistent with
  [`docs/adjudication-screening.md:33`](../docs/adjudication-screening.md)
  which documents `tool: str = "laglitsynth.screening_adjudication.screening"`.
  Since the module is named `adjudicate.py`, the code is right and
  the doc is stale. Fix the doc when this stage's docs are next
  touched.
- Good decision: `mode: str = "pass_through"` vs. planned
  `sampled_review`/`full_review` is exactly the sort of low-cost
  enum-like field that future-proofs the sidecar without
  overreaching.

### Stage 5 — fulltext-retrieval

Summary: the largest stage, and the one with the most real content. The
source cascade (OA URLs → Unpaywall → manual) matches
[`docs/full-text-retrieval.md`](../docs/full-text-retrieval.md). Rate
limiting, PDF validation, temp-file renames, and a `--skip-existing`
implementation are all present. Several behaviours diverge from the
doc — none fatal, all worth fixing or documenting.

Findings:

- [should-fix] [`src/laglitsynth/fulltext_retrieval/retrieve.py:145–230`](../src/laglitsynth/fulltext_retrieval/retrieve.py) —
  `_retrieve_one` can never produce `RetrievalStatus.failed`. Every
  download exception is swallowed in `_try_oa_urls` (L93–95) or
  `_try_unpaywall` (L222) and the cascade falls through to
  `abstract_only`. That contradicts
  [`docs/full-text-retrieval.md:77–87`](../docs/full-text-retrieval.md)
  which defines `failed` specifically as "a source was found but
  download failed (network error, 403, corrupt file)". The
  `failed_count` counter in `run()` (L296, L327–328) is therefore
  dead code. Either wire `failed` status in (record the last
  exception and return `failed` when at least one source was tried
  but none succeeded) or remove the enum value and counter.
- [should-fix] [`src/laglitsynth/fulltext_retrieval/retrieve.py:318–319`](../src/laglitsynth/fulltext_retrieval/retrieve.py) —
  `append_jsonl(record, retrieval_path)` appends every run; if a user
  runs twice without `--skip-existing`, `retrieval.jsonl` accumulates
  duplicate `RetrievalRecord`s for the same work. `_load_existing`
  (L238) iterates and uses a set, so skip-existing is robust, but
  counts in the meta file will reflect only the latest run while the
  jsonl grows. Consider rewriting the jsonl at the end of the run,
  or deduplicating by `work_id` when writing `retrieval-meta.json`.
- [should-fix] [`src/laglitsynth/fulltext_retrieval/retrieve.py:336–348`](../src/laglitsynth/fulltext_retrieval/retrieve.py) —
  The unretrieved.txt logic has three issues worth looking at:
  (1) `works` is the full input; skip-existing entries are excluded
  from the file as expected (L339), but works processed and
  retrieved on this run are included only by the physical
  `pdf_path.exists()` check (L346) — fine but brittle (what if a
  PDF was retrieved into `manual/` by a prior run and the
  `pdfs/` file was cleaned up?). (2) `not args.dry_run` means in
  dry-run the file is never populated. (3) The file is opened for
  plain write and therefore truncated on every re-run, which
  conflicts with the "resumability" model where we add to
  unretrieved.txt on each pass. Consider regenerating it from
  `retrieval.jsonl` at run-end rather than from live file-existence
  checks.
- [should-fix] [`src/laglitsynth/fulltext_retrieval/retrieve.py:159–187`](../src/laglitsynth/fulltext_retrieval/retrieve.py) —
  The `dry_run` branch fakes statuses by source availability:
  `has_manual` → `retrieved_manual`, else `has_oa` →
  `retrieved_oa`, else `has_doi` → `retrieved_unpaywall`. The last
  one is misleading: the presence of a DOI does not imply Unpaywall
  will serve a PDF. The `RetrievalMeta` counts printed from a dry
  run will over-report `retrieved_unpaywall`. Consider reporting a
  `would_attempt_unpaywall` synthetic status or just mapping to
  `abstract_only` when the only plausible source is Unpaywall.
- [should-fix] [`src/laglitsynth/fulltext_retrieval/retrieve.py:99–125`](../src/laglitsynth/fulltext_retrieval/retrieve.py) —
  `_try_unpaywall` raises on `raise_for_status()` (L116). Those
  exceptions are caught in `_retrieve_one` (L222) — good. But any
  exception inside `_download_pdf` from the PDF URL that Unpaywall
  returned is also caught there, so a successful Unpaywall API call
  that hands off a broken PDF URL silently falls through to
  `abstract_only`. Same root cause as the `failed` status bug.
- [should-fix] [`src/laglitsynth/fulltext_retrieval/retrieve.py:24`](../src/laglitsynth/fulltext_retrieval/retrieve.py) —
  `_last_request: dict[str, float] = {}` is module-global state.
  Fine for single-process CLI use; will break tests that run in
  parallel or anything that imports the module twice. The test
  suite already pokes `mod._last_request.clear()` in
  [`tests/test_fulltext_retrieval.py:298`](../tests/test_fulltext_retrieval.py),
  which is a small signal that the state is fragile. Consider
  wrapping rate-limit state in a tiny class so it can be
  instantiated per run.
- [should-fix] [`src/laglitsynth/fulltext_retrieval/retrieve.py:40–45`](../src/laglitsynth/fulltext_retrieval/retrieve.py) —
  `_validate_pdf` returns `True` if either the first four bytes are
  `%PDF` OR the content-type says so. An HTML page with
  `Content-Type: application/pdf` (sometimes happens from CDN error
  pages) passes validation. Prefer checking `%PDF` prefix only;
  content-type is hints-not-gospel.
- [should-fix] [`src/laglitsynth/fulltext_retrieval/retrieve.py:108`](../src/laglitsynth/fulltext_retrieval/retrieve.py) —
  DOI normalisation uses `replace("https://doi.org/", "")` — won't
  strip `http://doi.org/`, `http://dx.doi.org/`, or uppercase
  `HTTPS`. Small; OpenAlex DOIs tend to be consistent, but a
  `re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)` is
  equally short.
- [should-fix] [`src/laglitsynth/fulltext_retrieval/retrieve.py:61–73`](../src/laglitsynth/fulltext_retrieval/retrieve.py) —
  `_download_pdf` uses `tempfile.NamedTemporaryFile(dir=dest.parent,
  delete=False)` then `tmp_path.rename(dest)`. The `except
  BaseException` cleanup is correct. One subtlety: the temp file is
  created before `response.content` is confirmed to be a valid PDF
  — it isn't, because the validity check happens first (L59).
  Ignore that part of my concern; the function is fine. The only
  genuine point: no `Content-Length` sanity check, so a zero-byte
  PDF with `%PDF` prefix passes. Probably not worth fixing.
- [should-fix] [`src/laglitsynth/fulltext_retrieval/retrieve.py:27–33`](../src/laglitsynth/fulltext_retrieval/retrieve.py) —
  `_rate_limit(domain)` updates `_last_request[domain]` AFTER a
  potentially-long `time.sleep`. The second `time.monotonic()`
  call (L33) captures wake time, which is correct. Good.
- [nit] [`src/laglitsynth/fulltext_retrieval/retrieve.py:188–198`](../src/laglitsynth/fulltext_retrieval/retrieve.py) —
  The order in the doc is OA → Unpaywall → preprint → manual
  ([`docs/full-text-retrieval.md:11–47`](../docs/full-text-retrieval.md)).
  The code runs manual first, then OA, then Unpaywall. Manual
  files are cheap (local copy), so manual-first is actually the
  pragmatic choice — but the doc says otherwise. Either update the
  doc or reorder. Preprint isn't implemented; doc says "defer" at
  line 205 so that's consistent.
- [nit] [`src/laglitsynth/fulltext_retrieval/retrieve.py:298`](../src/laglitsynth/fulltext_retrieval/retrieve.py) —
  `user_agent = f"laglitsynth/0.1 (mailto:{args.email})"` exposes
  user email to every publisher CDN. That's conventional for
  Unpaywall usage and fine; flag only so the behaviour is
  deliberate.
- Good decision: the `retrieved_*.startswith("retrieved_")`
  predicate for skip-existing
  ([`retrieve.py:239`](../src/laglitsynth/fulltext_retrieval/retrieve.py))
  keeps the resume semantic readable. The test at
  [`test_fulltext_retrieval.py:224–256`](../tests/test_fulltext_retrieval.py)
  explicitly locks this in — "skip retrieved, retry failed" is
  exactly what the doc promises.
- Good decision: validating the PDF magic bytes and using a temp
  file + atomic rename prevents partial-write corruption.

### Stage 6 — fulltext-extraction

Summary: a clean wrapper around GROBID TEI XML. Parses, handles the
known failure modes documented in
[`docs/grobid-lessons.md`](../docs/grobid-lessons.md) and
[`docs/full-text-extraction.md`](../docs/full-text-extraction.md):
missing `<head>`, no `<div>`s, figure stripping, TEI namespace.
Skip-existing is work-ID-keyed and pulls from `extraction.jsonl`.

Findings:

- [should-fix] [`src/laglitsynth/fulltext_extraction/extract.py:193`](../src/laglitsynth/fulltext_extraction/extract.py) —
  `work_id = f"https://openalex.org/{pdf.stem}"` assumes every PDF in
  `--pdf-dir` is named `W1234567890.pdf` (the OpenAlex suffix). A
  stray manually-added `sample.pdf` becomes
  `https://openalex.org/sample` — a corrupt `work_id`. Consider
  skipping files whose stem doesn't match `^W\d+$` with a warning.
- [should-fix] [`src/laglitsynth/fulltext_extraction/extract.py:210–213`](../src/laglitsynth/fulltext_extraction/extract.py) —
  `tei_path.write_bytes(tei_bytes)` writes the raw TEI. If a prior
  run left a TEI file and the current run fails at `parse_tei`,
  the old TEI is overwritten but no extraction record exists — the
  next `--skip-existing` run will reprocess from the PDF (because
  the `work_id` isn't in `extraction.jsonl` yet). That's the right
  behaviour; call it good.
- [should-fix] [`src/laglitsynth/fulltext_extraction/extract.py:66`](../src/laglitsynth/fulltext_extraction/extract.py) —
  `div.findall(f"{TEI_NS}p")` only finds direct-child paragraphs.
  Nested `<p>`s inside e.g. `<div><div><p>...` are dropped. Is that
  deliberate? GROBID does produce nested section divs. If you want
  recursive descent, use `f".//{TEI_NS}p"`; if flat is intentional
  (keeping top-level sections as atomic blocks), a comment would
  help.
- [should-fix] [`src/laglitsynth/fulltext_extraction/extract.py:44`](../src/laglitsynth/fulltext_extraction/extract.py) —
  Only top-level `<div>`s of `<body>` are enumerated
  (`body.findall(f"{TEI_NS}div")`). GROBID often nests sub-divs
  (Methods → Sub-methods). This approach flattens structure by
  ignoring sub-sections. Works for the doc's goal ("flat list of
  sections") — but sub-section heads are lost.
- [should-fix] [`docs/full-text-extraction.md:59–73`](../docs/full-text-extraction.md) —
  the doc promises a "quality gate" ("median sentence length,
  character-class entropy, ratio of non-ASCII characters"). The
  code has none. The doc says "flagged for implementation alongside
  the TEI parser, not deferred". Either implement a minimal gate
  or update the doc to defer.
- [should-fix] [`src/laglitsynth/fulltext_extraction/extract.py:32`](../src/laglitsynth/fulltext_extraction/extract.py) —
  `etree.fromstring(xml_bytes)` — lxml parses with default settings
  which do not disable external entity resolution. For GROBID
  output from a local server this is not a realistic threat, but
  defensively using `lxml.etree.fromstring(xml_bytes,
  parser=etree.XMLParser(resolve_entities=False, no_network=True))`
  is cheap. Low priority — known-trust input.
- [should-fix] [`src/laglitsynth/fulltext_extraction/extract.py:161–169`](../src/laglitsynth/fulltext_extraction/extract.py) —
  The GROBID preflight uses the same `httpx.Client` that's used
  for the per-paper `processFulltextDocument` POSTs, whose timeout
  is `args.timeout` (default 120 s). If GROBID is slow to respond
  to `/api/isalive`, we wait the full 120 s before failing. A
  short preflight timeout (e.g. 5–10 s) would be friendlier.
- [nit] [`src/laglitsynth/fulltext_extraction/extract.py:216–218`](../src/laglitsynth/fulltext_extraction/extract.py) —
  `raw_text = "\n\n".join(f"## {s.title}\n\n{s.text}" for s in sections)`
  is duplicate data (the info is already in `sections`). Fine for
  convenience, but adds ~2x file size to `extraction.jsonl`. Is
  storing both the structured and flat text deliberate, or could
  `raw_text` be a property?
- Good decision: GROBID health check up front with docker command
  in the error message. Matches
  [`docs/full-text-extraction.md:173–177`](../docs/full-text-extraction.md)
  exactly.
- Good decision: save raw TEI before parsing. Matches the doc's
  "TEI XML files are retained so parsing can be re-run without
  re-calling GROBID"
  ([`docs/full-text-extraction.md:134–135`](../docs/full-text-extraction.md)).

## Tests

Summary: the suite is small (46 tests, all passing, coverage 70%
total). Tests are mostly behavioural but three of the six files
(`test_catalogue_dedup.py`, `test_screening_adjudication.py`,
`test_catalogue_fetch.py`) exercise placeholder-level code paths.
Mocking is consistent but pins the current implementation somewhat
tightly. Missing cases are around malformed inputs and LLM/HTTP
failure modes.

Ran `pixi run test` → 46 passed, 0 failed, 0.38s. Coverage totals
reported by `pytest-cov`:

```
src/laglitsynth/__init__.py                              100%
src/laglitsynth/catalogue_dedup/dedup.py                  80%
src/laglitsynth/catalogue_fetch/fetch.py                  33%
src/laglitsynth/cli.py                                     0%
src/laglitsynth/fulltext_extraction/extract.py            43%
src/laglitsynth/fulltext_retrieval/retrieve.py            80%
src/laglitsynth/io.py                                     89%
src/laglitsynth/screening_abstracts/screen.py             90%
src/laglitsynth/screening_adjudication/adjudicate.py      79%
TOTAL                                                     70%
```

Findings:

- [should-fix] [`tests/test_catalogue_dedup.py`](../tests/test_catalogue_dedup.py) —
  "Is the dedup test meaningful given dedup is a pass-all MVP?"
  Partly. The tests confirm the pass-all contract, the empty-input
  edge case, and the meta schema. They don't guard against future
  regressions in matching logic (because there is no logic yet).
  When real dedup lands, every test in this file will need to be
  rewritten. That's fine — just note the current tests are really
  smoke tests of "does the file get written".
- [should-fix] [`tests/test_screening_adjudication.py`](../tests/test_screening_adjudication.py) —
  Same as above. Purely shape-of-output assertions on a
  pass-through stage.
- [should-fix] [`tests/test_catalogue_fetch.py`](../tests/test_catalogue_fetch.py) —
  Only tests `_reconstruct_abstract`. Nothing exercises the OpenAlex
  pagination, field selection, filter builder, or the
  `ValidationError`-per-record recovery path. The tricky
  `max_records` loop behaviour around page boundaries is
  uncovered. The API client is external so full mocks are
  expensive, but the `search_openalex` generator could be unit
  tested with a fake `pyalex.Works()`.
- [should-fix] Missing: malformed JSONL input tests for
  `read_works_jsonl` and `read_jsonl` in
  [`src/laglitsynth/io.py`](../src/laglitsynth/io.py). The code
  logs and skips bad lines — that's behaviour worth pinning with a
  test. No test file covers `io.py` directly.
- [should-fix] Missing: network failure coverage for stage 5
  beyond `httpx.ConnectError`. What does `raise_for_status()` for
  a 503 do? What about `httpx.ReadTimeout`? What about a PDF
  response that is actually HTML (current `_validate_pdf` weakness
  — see stage 5 findings)?
- [should-fix] Missing: "PDFs that aren't PDFs" for stage 6 —
  e.g., a GROBID response that is a 200 with an empty TEI body,
  an XML with only `<text/>`, or TEI with UTF-8 BOM. The current
  stage-6 tests
  ([`tests/test_fulltext_extraction.py`](../tests/test_fulltext_extraction.py))
  cover empty-body, missing-head, no-divs, and figure stripping —
  good — but none exercise malformed XML that `etree.fromstring`
  would reject with `XMLSyntaxError`.
- [should-fix] Missing: LLM-returns-junk coverage beyond parse
  errors. The test "wrong types" verifies `int("high")` throws —
  but what about `relevance_score: 120` (out of range)? Currently
  there is no range validation in
  [`FilterVerdict`](../src/laglitsynth/screening_abstracts/models.py)
  so scores > 100 or < 0 silently pass. Is that intentional?
- [should-fix] Missing: partial-write recovery for stage 5
  retrieval. There's no test that simulates "crash halfway through
  the first run, resume with `--skip-existing`". The existing
  `TestSkipExisting` test is a unit test of `_load_existing`, not
  an integration test of the crash/resume cycle.
- [should-fix] [`tests/test_fulltext_retrieval.py:262–290`](../tests/test_fulltext_retrieval.py) —
  `TestUnretrievedTxt` mocks `httpx.Client` at the import site
  and all GETs fail with `ConnectError`. Under that setup the
  code fall-through yields `RetrievalStatus.abstract_only`, and
  both works end up in unretrieved.txt. Good, except that the
  semantic mixes "download failed" and "no source tried" into the
  same output — mirroring the production-code ambiguity that
  stage 5's "no `failed` status" finding flags.
- [should-fix] [`tests/test_fulltext_retrieval.py:191–220`](../tests/test_fulltext_retrieval.py) —
  The `test_http_403` comment says "OA 403 falls through the
  cascade to abstract_only (no Unpaywall without a DOI on this
  test work)" — but the work has DOI `https://doi.org/10.1234/test`
  set by `_make_work` default (L28). The reason it falls through
  is that the mocked client's single `return_value` is used for
  all GETs, so the Unpaywall JSON request returns a 403 response
  object whose `raise_for_status` raises, which is caught at L222.
  The test passes for a different reason than the comment claims.
  Worth fixing or expanding.
- [should-fix] [`tests/test_screening_abstracts.py:345–406`](../tests/test_screening_abstracts.py) —
  `test_run_writes_output_files` pins the current split-file
  output contract (accepted/rejected/verdicts). Once stage 3 is
  rewritten to verdict-only output, this test will be rewritten
  too. Flag not for fixing now but for tracking against the
  stage-3 rewrite.
- [nit] All test files use `MagicMock()` for `args`. That's fine
  but hides type errors — e.g. if the real `argparse.Namespace`
  ever requires a new attribute, tests silently return a new
  `MagicMock` for it. Consider a shared `_make_args(**overrides)`
  helper that constructs a real `argparse.Namespace`.
- [nit] [`.coverage` file at repo root](../.coverage) — present
  in the working tree. Not tracked by git
  (`.gitignore` line 12 ignores `.coverage`), so it is purely an
  artefact of the local test run. Good.
- Good decision: the extraction tests in
  [`tests/test_fulltext_extraction.py`](../tests/test_fulltext_extraction.py)
  use realistic TEI XML snippets rather than mocking lxml. They
  test behaviour of `parse_tei`, not its internal calls. Best-in-
  class in this suite.
- Good decision: the screening tests in
  [`tests/test_screening_abstracts.py`](../tests/test_screening_abstracts.py)
  use a `_mock_classify` side-effect factory keyed on `work_id`
  with a sentinel `"error"` value. Clean, reusable, and lets every
  test declare its LLM outputs declaratively.

## Type safety

Summary: strong. `mypy --strict` passes with zero issues on 22 source
files, and there are no `# type: ignore` comments, no `cast()` calls,
and no bare `Any` usage anywhere under `src/`. All public function
signatures carry annotations. Pydantic models are the cross-component
contract, consistent with the `Typed data, not dicts` principle.

Ran `pixi run typecheck` → `Success: no issues found in 22 source
files`.

Findings:

- [should-fix] [`pyproject.toml:38–44`](../pyproject.toml) — mypy
  overrides use `ignore_missing_imports = true` for `pyalex` and
  `lxml`. Both libraries have partial type stubs available
  (`lxml-stubs` is third-party; `pyalex` still ships without
  stubs). Consider installing `lxml-stubs` in the typecheck extras
  and removing the `lxml` override. Removes two lines of "I
  promise this is fine" and picks up real stub-based checks.
- [should-fix] [`src/laglitsynth/fulltext_extraction/extract.py:27–30`](../src/laglitsynth/fulltext_extraction/extract.py) —
  `_element_text` returns the result of `etree.tostring(...,
  encoding="unicode")` annotated as `str`. Without lxml stubs mypy
  takes the return annotation on faith. A typed lxml would catch
  that `etree.tostring(..., encoding="unicode")` indeed returns
  `str` — currently mypy is trusting rather than verifying.
- [should-fix] [`src/laglitsynth/io.py:48–57`](../src/laglitsynth/io.py) —
  `read_jsonl(path: Path, model: type[_T]) -> Iterator[_T]` —
  good, generic, typed. No dict-shaped sidetracks. The similar
  `read_works_jsonl` is a special case; consider replacing it with
  `read_jsonl(path, Work)` and dropping the duplicate function.
- [should-fix] [`src/laglitsynth/screening_abstracts/screen.py:56`](../src/laglitsynth/screening_abstracts/screen.py) —
  `content = response.choices[0].message.content or "{}"` — the
  `or "{}"` is a defensive default for `None`. The fallback
  silently becomes a `ClassifyError` on missing-keys at L59. OK
  but flags that the openai SDK's `Optional[str]` is absorbed by
  a broad exception class rather than a narrower
  `EmptyResponseError`.
- [should-fix] [`src/laglitsynth/screening_abstracts/screen.py:59–63`](../src/laglitsynth/screening_abstracts/screen.py) —
  `parsed = json.loads(content)` returns `Any`. `parsed["..."]`
  then feeds into Pydantic via `int(score)` / `str(reason)`
  conversions. This is `Typed data, not dicts` at the boundary —
  but the "dict" part lives briefly in `parsed`. Consider parsing
  into a tiny `_LLMResponse(BaseModel)` with `relevance_score:
  int` and `reason: str`, then letting Pydantic raise on bad
  data. Removes the manual try/except/KeyError/ValueError/TypeError
  tuple.
- [should-fix] [`src/laglitsynth/fulltext_retrieval/retrieve.py:117–124`](../src/laglitsynth/fulltext_retrieval/retrieve.py) —
  `data = response.json()` returns `Any`; `data.get(...)` chain
  drops into `dict[str, Any]`. Same suggestion — tiny
  `_UnpaywallResponse(BaseModel)` with `best_oa_location:
  _UnpaywallLoc | None`.
- [should-fix] [`src/laglitsynth/fulltext_retrieval/models.py:8`](../src/laglitsynth/fulltext_retrieval/models.py) —
  `RetrievalStatus` subclasses `str, Enum` (i.e. `StrEnum`-style).
  Since the codebase targets Python 3.12+
  ([`pyproject.toml:5`](../pyproject.toml)), consider
  `enum.StrEnum`. Cosmetic.
- [nit] [`src/laglitsynth/models.py:5`](../src/laglitsynth/models.py) —
  `class _Base(BaseModel)` with `ConfigDict(extra="ignore")`.
  `extra="ignore"` matches the AGENTS.md "no raw dicts flowing
  through" spirit — any upstream field not in the model gets
  dropped silently. For an OpenAlex-backed project that's the
  right choice. Good.
- [nit] [`src/laglitsynth/catalogue_fetch/models.py:88`](../src/laglitsynth/catalogue_fetch/models.py) —
  `Work.authorships: list[Authorship]` has no default; similarly
  `biblio`, `cited_by_count`, `referenced_works`, `keywords`,
  `topics`. In the "None means None" spirit, should these default
  to `[]`/`None` so that API records with missing optional fields
  survive instead of being dropped? See stage-1 findings.
- [nit] [`src/laglitsynth/catalogue_fetch/models.py:94`](../src/laglitsynth/catalogue_fetch/models.py) —
  `updated_date: str | None = None` — is there a reason this is
  `str` and not `datetime | date`? `publication_date` next to it
  is `date`. Minor inconsistency.
- Good decision: every `Meta` model carries a static `tool: str`
  and `tool_version: str` field for provenance. Cheap,
  future-useful, and keeps sidecars self-describing.

## Hygiene & deps

Summary: the repo is tidy. No secrets tracked, no stale commented-out
code, no TODOs or FIXMEs in `src/`. The pyproject is small and
focused. Two items stand out: `pixi.lock` is gitignored (lockfiles
usually want committing), and `.mypy_cache` / `.pytest_cache` aren't
ignored explicitly (though they happen not to be tracked in the
current tree).

Findings:

- [should-fix] [`.gitignore:11`](../.gitignore) — `pixi.lock` is
  gitignored. Pixi's default workflow commits the lockfile for
  reproducible environments; ignoring it means CI (if added) and
  fresh clones will re-solve the environment and potentially pick
  up different dependency versions. Is there a reason the lockfile
  is excluded?
- [should-fix] [`.gitignore`](../.gitignore) — missing explicit
  entries for `.mypy_cache/` and `.pytest_cache/`. Neither
  directory is tracked (verified with `git ls-files`), so this is
  precautionary, not a live problem.
- [should-fix] [`pyproject.toml`](../pyproject.toml) — no `ruff`,
  no `black`, no `pre-commit`. That's consistent with "research
  tool, not a product", but given the repo makes formatting
  claims (headings, typed Python) it would cost little to add at
  least `ruff` in the typecheck extras and wire a `pixi run lint`
  task. Do not add pre-commit automatically — that's a user-tool
  choice.
- [should-fix] No CI. `.github/workflows/` does not exist. For a
  research repo this is fine while the team is one person, but
  `pixi run test` + `pixi run typecheck` both pass cleanly — the
  cost of wiring a minimal GitHub Actions workflow is low and
  catches accidental regressions before the next PR review.
- [should-fix] [`pyproject.toml:9`](../pyproject.toml) — `openai`
  is unpinned. Every other dependency has a floor
  (`httpx>=0.27`, `lxml>=5.0`, `pyalex>=0.16`, `pydantic>=2.0`).
  Pick a floor for `openai` too. `python-dotenv` is also
  unpinned.
- [should-fix] [`pyproject.toml`](../pyproject.toml) — no
  [`[tool.pytest.ini_options]`](../pyproject.toml) section. With
  `pytest-cov` in test extras, the default coverage target is
  everywhere; the `pixi` task hardcodes `--cov=laglitsynth` which
  is correct. Consider moving to config for discoverability.
- [should-fix] [`src/laglitsynth/__init__.py`](../src/laglitsynth/__init__.py) —
  empty file. Harmless, but many projects stash a `__version__`
  constant here that matches
  [`pyproject.toml:3`](../pyproject.toml). Optional.
- [should-fix] [`.env`](../.env) — present at repo root (41
  bytes), gitignored (line 5). Good. But the file is still on
  disk from earlier testing — consider documenting in
  [`docs/fetch-publications.md`](../docs/fetch-publications.md)
  that users should populate `.env` with `OPENALEX_API_KEY=...`.
  The doc mentions the environment variable and `.env` but
  doesn't say to create the file.
- [nit] [`.coverage` file at repo root](../.coverage) — not
  tracked by git, but present in working tree. Gets regenerated
  on every `pixi run test`. Already handled by
  [`.gitignore:12`](../.gitignore). No action.
- [nit] [`plans/`](../plans) — `plans/done/` is 14 files; the
  file tree notice at the top of the git status shows a rename
  from `plans/stages-4-5-plumbing.md` to
  `plans/done/stages-4-5-plumbing.md`, which is the canonical
  "finish a plan" workflow from
  [`AGENTS.md:15–18`](../AGENTS.md). Good.
- [nit] [`README.md`](../README.md) — 1275 bytes. Not read for
  this review. If the README is the entry point for new
  contributors, a pointer to `pixi run test` / `pixi run
  typecheck` belongs there.
- [nit] `pyproject.toml` has no `authors`, `readme`, or
  `license` metadata under `[project]`. For a non-distributed
  research package this is fine; for any eventual PyPI publishing
  it would need adding.
- [nit] [`pyproject.toml:15–17`](../pyproject.toml) — test and
  typecheck are separate optional-dependencies groups, installed
  together via
  [`pyproject.toml:31`](../pyproject.toml)
  (`extras = ["test", "typecheck"]`). Clean split.
- Good decision: `pixi` tasks named exactly
  [`test`](../pyproject.toml) and
  [`typecheck`](../pyproject.toml), matching AGENTS.md
  ("`pixi run typecheck` before merging"). No hidden state.
- Good decision: `.gitignore` handles `data/**` with a gitkeep
  exception
  ([`.gitignore:1–3`](../.gitignore)) — so the per-stage output
  directories can be committed as empty placeholders without
  committing real data.
- Good decision: every stage package is consistently structured
  (`__init__.py`, `models.py`, `<verb>.py`). No exceptions. This
  makes dropping in stage 7+ mechanical.

## Summary of top items to act on

Grouped by area, priority-ordered.

- Stage 3/4 flag-don't-filter migration
  ([`docs/interfaces.md:151–158`](../docs/interfaces.md)) — the
  biggest doc/code drift. Tracked as a single change per the
  AGENTS.md "green field, no backwards compatibility" rule.
- Stage 5 `RetrievalStatus.failed` is unreachable
  ([`retrieve.py:145–230`](../src/laglitsynth/fulltext_retrieval/retrieve.py)).
  Either wire it in or remove it.
- Stage 5 `retrieval.jsonl` duplicates on re-run
  ([`retrieve.py:318–319`](../src/laglitsynth/fulltext_retrieval/retrieve.py)).
- Stage 6 bare-PDF-stem-to-work-ID assumption
  ([`extract.py:193`](../src/laglitsynth/fulltext_extraction/extract.py)).
- Commit `pixi.lock` ([`.gitignore:11`](../.gitignore)) or
  document why it's excluded.
- Add missing test coverage: malformed JSONL (`io.py`), partial
  retrieval resume cycle, GROBID XML error.
- Pin `openai` and `python-dotenv` in
  [`pyproject.toml`](../pyproject.toml).

No blockers outside the stage-3 flag-don't-filter migration; the
flagged [blocker] is a planned cutover, not a bug in released
behaviour.
