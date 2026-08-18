# Plan: PR #20 review fixes

## Goal

Close the findings from the [PR #20 review](https://github.com/geomar-od-lagrange/2026_laglitsynth/pull/20)
— diversified full-text retrieval plus the stages 7–8 full-text-only
cutover. The review found no blocking bug but one store-consistency
defect (a `missing` re-run silently orphans a previously retrieved PDF)
and a handful of green-field / typed-data violations the project does
not let stand: a misnamed meta counter, dead "tolerate older files"
fallbacks that re-introduce untyped dicts, and a ceremony exception that
encodes a distinction nothing reads. After this plan the store can no
longer hold an inconsistent `missing`-with-PDF state, every meta field
names what it holds, and the export path carries a typed `LlmMeta`
end to end. Retrieval also becomes sticky: once a work has a PDF the
store never re-fetches or overwrites it, so a normal run cannot spend
the scarce request budget (rate limits, captchas even for paid access)
on PDFs already held. `--skip-existing` is removed in favour of that
default, and a new `--refetch` opts back into deliberate re-download.

## Design decisions

Retrieval is sticky by default. A normal
[`fulltext-retrieval`](../../src/laglitsynth/fulltext_retrieval/retrieve.py)
run skips every work that already has a non-`missing` provenance record
and attempts only `missing` (and never-seen) works; it never overwrites
an on-disk PDF. Obtaining PDFs is the project's main pain point — rate
limits and captchas throttle even full-subscription access — so
re-downloading a PDF we already hold is wasted budget and a clobber
hazard (a "successful" re-fetch returning a paywalled stub or truncated
copy still passes the `%PDF` magic check and would replace a good full
text). Today's opt-in `--skip-existing` flag *is* this behaviour, so
under green-field it is removed rather than kept as a default-on alias;
a new `--refetch` forces re-download of held works for the rare case
where a stored PDF is known bad.

A `missing` outcome never downgrades a prior success — the safety net
that makes `--refetch` safe. When `--refetch` re-attempts a held work
and every download fails, `run()` keeps the prior non-`missing` record
rather than overwrite it with `missing`: the earlier `<stem>.pdf` still
exists (`_download_pdf` writes via tmp+rename, so a failed attempt never
touches it), and deleting a held PDF to make a `missing` record true
would throw away the scarce asset. Implemented as a guard at the single
write point in `run()` (the `upsert_provenance` call), keyed on
`record.source is PdfSource.missing and prior is not None and prior.source is not PdfSource.missing`.
On the sticky default path the guard is moot — held works are never
processed — but it is the load-bearing protection on `--refetch`.

The export meta loader returns a typed `LlmMeta | None`, not a dict.
Today `_load_meta` returns `tuple[str, dict[str, object]]` and falls
back, on validation failure, to pulling a raw `dict` out of the JSON to
"tolerate older meta files." Under green-field there are no older files
to tolerate, and the fallback re-introduces an untyped dict into the
sheet builder. The loader instead returns the `LlmMeta` model (or
`None` when no meta file is present — "None means None"); a genuinely
malformed meta raises rather than degrading silently.

## Implementation sequence

`pixi run typecheck` and `pixi run test` pass between each commit.

1. Sticky-by-default retrieval in
   [retrieve.py](../../src/laglitsynth/fulltext_retrieval/retrieve.py)
   `run()`. Replace the `--skip-existing` flag with `--refetch`: the
   skip set (works with a non-`missing` record) is now built *unless*
   `--refetch` is passed, so a plain run skips held works and attempts
   only `missing`/unseen ones; the stderr notice becomes "Skipping N
   works that already have a PDF (use --refetch to re-download)". Keep
   the no-downgrade guard at the single write point: before
   `upsert_provenance`, skip the write when the new record is `missing`
   and the existing record is not (log at debug) — this only fires on
   the `--refetch` path but is what makes it safe. Update
   [docs/fulltext-retrieval.md](../../docs/fulltext-retrieval.md),
   [docs/interfaces.md](../../docs/interfaces.md), and
   [docs/cross-machine.md](../../docs/cross-machine.md) (`grep` for
   `skip-existing`/`skip_existing` across docs and code first). Tests in
   [test_fulltext_retrieval.py](../../tests/test_fulltext_retrieval.py):
   (a) plain run skips a work that already has a non-`missing` record
   (no download attempted, PDF untouched); (b) plain run still attempts
   a `missing` work; (c) `--refetch` re-attempts a held work and, when
   all downloads fail, provenance still reads `oa` with the PDF present
   (the no-downgrade guard — the transition `TestSkipExisting` does not
   cover). Rename/repoint `TestSkipExisting` accordingly.

2. Rename `RetrievalMeta.abstract_only_count` → `missing_count` and drop
   `failed_count` in [models.py](../../src/laglitsynth/fulltext_retrieval/models.py),
   update the construction site in `retrieve.py` (drop the hard-wired
   `failed_count=0`), and fix the field list in
   [interfaces.md](../../docs/interfaces.md). `grep` for both names first
   to catch any reader; no test asserts them today, so add one asserting
   `missing_count` reflects the unretrieved tally.

3. Simplify `_try_oa_urls` to return `tuple[PdfSource, str] | None`
   (None = no URLs *or* all attempts failed) and delete
   `_AllAttemptsFailedError` and its `try/except` in `_retrieve_one` —
   the caller falls through to Unpaywall identically on both, so the
   distinction the exception carried is never read. Existing OA-failure
   tests stay green (the 403→`missing` path is unchanged in behaviour).

4. Tighten embedded-DOI matching in
   [import_.py](../../src/laglitsynth/fulltext_retrieval/import_.py): strip
   trailing `.,;:)` from a `_DOI_RE` match before normalising, so a
   first-page-text DOI rendered as `10.1234/abc.` matches manifest
   `10.1234/abc`. Test the first-page-*text* resolution path (currently
   only the metadata path is covered) with a trailing-period DOI.

5. Drop the dead meta `try/except` fallback in all three export modules
   ([fulltext_eligibility/export.py](../../src/laglitsynth/fulltext_eligibility/export.py),
   [extraction_codebook/export.py](../../src/laglitsynth/extraction_codebook/export.py),
   [screening_abstracts/export.py](../../src/laglitsynth/screening_abstracts/export.py)):
   `_load_meta` returns `tuple[str, LlmMeta | None]` (model on success,
   `None` when the file is absent, raise on malformed), and
   `build_work_sheet` takes `LlmMeta | None` and reads typed attributes
   instead of `dict.get`. Update the three export tests' call sites; the
   "meta absent → placeholder" path keeps its existing assertion.

6. Nits: a one-line comment at the `import_.py` manifest reader noting
   `expected_filename` is an advisory round-trip column (written, not
   read back); move the inline `from datetime import UTC, datetime` in
   `import_.py` to the module top, matching `retrieve.py`.

## Follow-ups

Dry-run reporting honesty is out of scope: under `--dry-run` a work with
a prior PDF but no current OA URL still shows `missing` in the in-memory
summary. That is arguably the honest dry-run answer ("couldn't fetch
this run") and it never touches disk; revisit only if the dry-run
summary proves misleading in practice.

## Critical files

- [src/laglitsynth/fulltext_retrieval/retrieve.py](../../src/laglitsynth/fulltext_retrieval/retrieve.py)
- [src/laglitsynth/fulltext_retrieval/models.py](../../src/laglitsynth/fulltext_retrieval/models.py)
- [src/laglitsynth/fulltext_retrieval/import_.py](../../src/laglitsynth/fulltext_retrieval/import_.py)
- [src/laglitsynth/fulltext_eligibility/export.py](../../src/laglitsynth/fulltext_eligibility/export.py),
  [src/laglitsynth/extraction_codebook/export.py](../../src/laglitsynth/extraction_codebook/export.py),
  [src/laglitsynth/screening_abstracts/export.py](../../src/laglitsynth/screening_abstracts/export.py)
- [docs/interfaces.md](../../docs/interfaces.md)
- [tests/test_fulltext_retrieval.py](../../tests/test_fulltext_retrieval.py),
  [tests/test_fulltext_retrieval_import.py](../../tests/test_fulltext_retrieval_import.py),
  and the three `test_*_export.py` files
