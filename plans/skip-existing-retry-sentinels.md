# `--skip-existing` retries sentinel rows

## Goal

`--skip-existing` in stages 7 (`fulltext-eligibility`) and 8
(`extraction-codebook`) skips every work with a row in the output JSONL,
sentinels included. A run cut short by a budget limit (the LiteLLM proxy
answers HTTP 422 `budget_exceeded`, recorded as `llm-rejected`) or an
outage cannot be resumed without removing the sentinel rows by hand, as
in [first-full-run-2026-11.md](../docs/explorations/first-full-run-2026-11.md).
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

Under `--skip-existing`, before any LLM call, the stage reads the
existing JSONL, keeps the successful rows and rewrites the file with
only those, through a temporary file in the same directory and
`os.replace`, so a crash leaves either the old or the new file. Skipped
ids are the kept rows' ids; the dropped works go through the normal
loop and are appended as before, so each work ends with exactly one
row and the meta counts (computed from kept plus new rows) stay
correct. A crash after the rewrite leaves those works without a row,
which the next resume sends anyway. Lines that fail validation are
already ignored on read and are not carried into the rewritten file.
`--dry-run` filters in memory and writes nothing. The resume guards
(`prompt_sha256` in both stages, `eligibility_prompt_sha256` and
`corpus_id`) run before the rewrite and are unchanged.

The helper lives in [`io.py`](../src/laglitsynth/io.py) as
`keep_jsonl_rows(path, model, keep, *, stats, rewrite) -> tuple[list, int]`
(kept rows, dropped count), used by both stages with
`keep=lambda r: r.reason is None`. The resume message names both
numbers: `Skipping N already-assessed works; retrying M sentinel rows.`

## Implementation sequence

1. This plan.
2. `keep_jsonl_rows` in [`io.py`](../src/laglitsynth/io.py) and its use
   in stages 7 and 8, with `--skip-existing` help text updated. Tests:
   `test_keep_jsonl_rows_rewrites_atomically` in `tests/test_io.py`
   (kept rows returned, file rewritten, no temporary file left) and
   `test_keep_jsonl_rows_without_rewrite_leaves_file`;
   `test_skip_existing_retries_sentinels` in
   [test_fulltext_eligibility.py](../tests/test_fulltext_eligibility.py)
   and [test_extraction_codebook.py](../tests/test_extraction_codebook.py)
   (a successful row is skipped, an `llm-rejected` row is retried, the
   file ends with one row per work, the meta counts the retried
   result); `test_skip_existing_dry_run_keeps_sentinel_rows` in stage 7.
3. Docs: the `--skip-existing` entries in
   [eligibility.md](../docs/eligibility.md),
   [extraction-codebook.md](../docs/extraction-codebook.md) and the
   resume paragraph in [interfaces.md](../docs/interfaces.md); roadmap
   entry moved to Implemented; this plan to `plans/done/`.

## Critical files

- [src/laglitsynth/io.py](../src/laglitsynth/io.py)
- [src/laglitsynth/fulltext_eligibility/eligibility.py](../src/laglitsynth/fulltext_eligibility/eligibility.py)
- [src/laglitsynth/extraction_codebook/extract.py](../src/laglitsynth/extraction_codebook/extract.py)
- [docs/eligibility.md](../docs/eligibility.md)
- [docs/extraction-codebook.md](../docs/extraction-codebook.md)
- [docs/interfaces.md](../docs/interfaces.md)
