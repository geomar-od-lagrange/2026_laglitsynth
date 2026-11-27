# `--skip-existing` retries sentinel rows

## Goal

`--skip-existing` in stages 7 (`fulltext-eligibility`) and 8
(`extraction-codebook`) skips every work with a row in the output JSONL,
sentinels included. A run cut short by a budget limit (the LiteLLM proxy
answers HTTP 422 `budget_exceeded`, recorded as `llm-rejected`) or an
outage cannot be resumed without removing the sentinel rows by hand, as
in [first-full-run-2026-11.md](../../docs/explorations/first-full-run-2026-11.md).
After this plan, `--skip-existing` skips only works with a successful
row and sends every sentinel work again.

## Target state

A row is successful when its `reason` is `None`: a decided verdict in
stage 7, a filled record in stage 8. Every sentinel is retried:
`llm-timeout`, `llm-rejected`, `llm-parse-failure` and
`tei-parse-failure`. The LLM sentinels are transient or sampled (a
budget or rate limit, an outage, an unfinished or unparseable answer),
so a retry can succeed. `tei-parse-failure` re-fails identically while
the TEI is unchanged, but it is decided locally before any LLM call, so
the retry costs nothing, and it picks up a TEI that stage 6 has since
re-extracted. One rule, "keep successful rows", is simpler to state and
test than a list of retryable reasons. An `llm-rejected` that is not
transient (a full text over the model's context) is sent again on every
resume; it fails the same way and is recorded again.

Under `--skip-existing`, `run()` reads the existing JSONL and builds
the list of works it will send: the active works (current extraction,
and in stage 8 an `eligible=True` verdict) without a successful row,
capped by `--max-records`. Before any LLM call it removes from the file
only the sentinel rows of works in that list, through a temporary file
in the same directory that is fsynced and moved over the original with
`os.replace` (removed again on failure), so a crash leaves either the
old or the new file. Every other line stays as it is: successful rows,
sentinel rows of works not sent this time (beyond `--max-records`, or
outside the active set), and lines that fail validation. The sent works
are appended as before, so each work ends with exactly one row, and the
meta counts the remaining rows plus the new ones. A crash after the
rewrite leaves the sent works without a row, which the next resume
sends anyway. `--dry-run` writes nothing. The resume guards
(`prompt_sha256` in both stages, `eligibility_prompt_sha256` and
`corpus_id`) run before the rewrite and are unchanged.

The helper lives in [`io.py`](../../src/laglitsynth/io.py) as
`drop_jsonl_rows(path, model, drop) -> int`, used by both stages with
`drop` true for a sentinel row of a work about to be sent. The resume
message counts works: `Skipping N already-assessed works; retrying M
works with a sentinel verdict.`

## Implementation sequence

1. This plan.
2. `drop_jsonl_rows` in [`io.py`](../../src/laglitsynth/io.py) and its
   use in stages 7 and 8, which now build the send list in `run()`,
   with `--skip-existing` help text updated. Tests in `tests/test_io.py`:
   `test_drop_jsonl_rows_rewrites_and_keeps_invalid_lines`,
   `test_drop_jsonl_rows_leaves_file_when_nothing_dropped`,
   `test_drop_jsonl_rows_removes_temp_file_on_failure`. In
   [test_fulltext_eligibility.py](../../tests/test_fulltext_eligibility.py)
   and [test_extraction_codebook.py](../../tests/test_extraction_codebook.py):
   `test_skip_existing_retries_sentinels`,
   `test_skip_existing_max_records_keeps_unsent_sentinels`,
   `test_skip_existing_retries_tei_parse_failure`,
   `test_skip_existing_keeps_sentinel_outside_active_set` and
   `test_skip_existing_dry_run_keeps_sentinel_rows`; stage 7 also
   `test_skip_existing_keeps_invalid_lines`.
3. Docs: the `--skip-existing` and `--max-records` entries in
   [eligibility.md](../../docs/eligibility.md) and
   [extraction-codebook.md](../../docs/extraction-codebook.md) and the
   resume paragraph in [interfaces.md](../../docs/interfaces.md); roadmap
   entry moved to Implemented; this plan to `plans/done/`.

## Critical files

- [src/laglitsynth/io.py](../../src/laglitsynth/io.py)
- [src/laglitsynth/fulltext_eligibility/eligibility.py](../../src/laglitsynth/fulltext_eligibility/eligibility.py)
- [src/laglitsynth/extraction_codebook/extract.py](../../src/laglitsynth/extraction_codebook/extract.py)
- [docs/eligibility.md](../../docs/eligibility.md)
- [docs/extraction-codebook.md](../../docs/extraction-codebook.md)
- [docs/interfaces.md](../../docs/interfaces.md)
