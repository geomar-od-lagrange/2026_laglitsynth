# Screening-abstracts CSV export

## Goal

Add `laglitsynth screening-abstracts-export`: joins stage 3's
`verdicts.jsonl` with the dedup catalogue and writes one `review.csv`
a reviewer opens in Excel / Numbers / Keynote. Carries everything
needed to second-guess the LLM plus two empty columns for the
reviewer's own verdict. Read-only export; ingesting the edited CSV
is a stage-4 follow-up.

## Non-goals

CSV ingest (stage 4); sampling/sorting (spreadsheet); generalising
to other stages (lift a helper when a second stage needs it); HTML
report (separate plan).

## Target state

New sibling of the existing stage-3 outputs:
`data/screening-abstracts/review.csv`. Fixed columns, one row per
verdict, in `verdicts.jsonl` order:

| # | Column | Source |
|---|---|---|
| 1 | `work_id` | verdict |
| 2 | `title` | catalogue |
| 3 | `doi` | catalogue |
| 4 | `publication_year` | catalogue |
| 5 | `abstract` | catalogue |
| 6 | `relevance_score` | verdict (blank for sentinels) |
| 7 | `llm_reason` | verdict |
| 8 | `reviewer_decision` | empty |
| 9 | `reviewer_reason` | empty |
| 10 | `raw_response` | verdict |

Reviewer columns sit between LLM verdict and raw response so they're
next to what's being judged. `reviewer_decision` is free-text at
export; stage 4's ingestor validates the enum later.

UTF-8 with BOM (`utf-8-sig`) so Excel-on-Windows renders non-ASCII
correctly. `csv.writer` default dialect round-trips commas, quotes,
and embedded newlines through every spreadsheet tried.

CLI:

```
laglitsynth screening-abstracts-export \
    --verdicts data/screening-abstracts/verdicts.jsonl \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    [--output data/screening-abstracts/review.csv]
```

Default `--output` = `<verdicts parent>/review.csv`. No `--dry-run`
or `--max-records`; the export is cheap and disposable.

Decisions: sentinel `relevance_score` → empty cell (unambiguous for
sort/filter); `llm_reason` keeps its sentinel string; a `work_id`
missing from the catalogue raises rather than emitting a half-empty
row; provenance stays in `screening-meta.json`, not the CSV.

## Implementation sequence

Two commits. `pixi run typecheck` and `pixi run test` before each.

1. **Export module + tests.** New
   [src/laglitsynth/screening_abstracts/export.py](../src/laglitsynth/screening_abstracts/export.py):
   `COLUMNS` tuple, `build_row(verdict, work)`, and
   `export_review_csv(verdicts_path, catalogue_path, output_path) ->
   int`. Tests: round-trip via `csv.DictReader`, comma/quote/newline
   survival in abstracts, sentinel → empty `relevance_score`,
   missing-work-id raises, column order matches `COLUMNS`, BOM
   present.

2. **CLI + docs.** Register `screening-abstracts-export` in
   [cli.py](../src/laglitsynth/cli.py); print `Wrote N rows to
   <path>`. CLI smoke test on the same fixture. Add a "Human review
   export" section to
   [docs/screening-abstracts.md](../docs/screening-abstracts.md).
   No pipeline.md change — review surface, not a stage.

## Follow-ups

`screening-adjudication --from-csv` to ingest reviewer edits; lift
`export.py` into `laglitsynth.review_export` when a second stage
needs it; HTML variant for stage 8's 30 field/context pairs.

## Risks

Reviewer can overwrite `relevance_score` or `llm_reason` in the
spreadsheet; the ingest follow-up will read only `work_id`,
`reviewer_decision`, `reviewer_reason` and document that.

## Critical files

- [src/laglitsynth/screening_abstracts/screen.py](../src/laglitsynth/screening_abstracts/screen.py)
- [src/laglitsynth/screening_abstracts/models.py](../src/laglitsynth/screening_abstracts/models.py)
- [src/laglitsynth/io.py](../src/laglitsynth/io.py)
- [src/laglitsynth/cli.py](../src/laglitsynth/cli.py)
- [docs/screening-abstracts.md](../docs/screening-abstracts.md)
