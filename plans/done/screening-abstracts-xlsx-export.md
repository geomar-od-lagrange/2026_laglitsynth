# Screening-abstracts XLSX export

## Goal

Add `laglitsynth screening-abstracts-export-xlsx`: a workbook
reviewers can open in Excel / Numbers / LibreOffice for per-work
spot-checking. One tab per sampled work, each tab a vertical
`Field | Value` layout that fits a normal screen without horizontal
scrolling. A `--n-subset` flag draws a random sample; when
`--n-subset >= len(verdicts)` the export falls back to the full set,
so the same command covers both "sample 50 to check" and "all of
them." The flat [CSV exporter](screening-abstracts-csv-export.md)
stays for sort/filter/overview; this is the deep-read surface.

## Non-goals

XLSX ingest back into stage 4 (follow-up); `.ods` format (xlsx is
read cleanly by Excel, Numbers, Keynote, and LibreOffice); batching
into multiple workbooks (the subset flag removes the need).

## Target state

New dep: `openpyxl`.

New output `review.xlsx` sibling of `verdicts.jsonl`. Sheets:

- **Index** (sheet 1) — one row per included work:
  `work_id`, `title`, `relevance_score`, `llm_reason`, and a
  hyperlink cell jumping to the per-work sheet. Rows in verdict
  order. Column A frozen, header row frozen.
- **Per-work** (sheets 2..N) — named by the trailing OpenAlex id
  (`W3213722062` from `https://openalex.org/W3213722062`). Two
  columns, `Field | Value`. Column A ~28 chars, column B ~110 with
  `wrap_text=True`. Rows top-down: `work_id`, `title`, `doi`,
  `publication_year`, blank, `abstract` (tall wrapped), blank,
  `relevance_score`, `llm_reason`, blank, `reviewer_decision`
  (empty), `reviewer_reason` (empty), blank, `raw_response` (tall
  wrapped, at the bottom so it's out of the way).

CLI:

```
laglitsynth screening-abstracts-export-xlsx \
    --verdicts data/screening-abstracts/verdicts.jsonl \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    [--output data/screening-abstracts/review.xlsx] \
    [--n-subset N] [--subset-seed INT]
```

`--n-subset` defaults to "all." When present and less than the
verdict count, draw a uniform random sample of that many verdicts
using `random.Random(--subset-seed).sample`, then emit sampled
verdicts in their original verdict-file order. When `--n-subset
>= len(verdicts)` the whole set is emitted in verdict order —
identical output to running without the flag. `--subset-seed`
defaults to a fixed integer so re-runs are reproducible; override
to draw a different sample.

Decisions: tab names use the trailing OpenAlex id; on collision
(unlikely but possible with non-OpenAlex ids) fall back to
`W3213722062_2`, `_3`, … A `work_id` in verdicts but not in the
catalogue raises, same contract as the CSV exporter. Sentinel
verdicts still get a per-work sheet — the sentinel string in
`llm_reason` tells the reviewer why there's no score.

## Implementation sequence

Two commits. `pixi run typecheck` and `pixi run test` before each.

1. **Workbook module + tests.** Add `openpyxl` to
   [pyproject.toml](../pyproject.toml); new
   [src/laglitsynth/screening_abstracts/export_xlsx.py](../src/laglitsynth/screening_abstracts/export_xlsx.py)
   with `sample_verdicts(verdicts, n_subset, seed)` (returns
   verdicts in original order), `short_work_id(work_id)`,
   `build_index_sheet(ws, pairs)`, `build_work_sheet(ws, verdict,
   work)`, and `export_review_xlsx(verdicts_path, catalogue_path,
   output_path, n_subset, seed) -> int`. Tests: full-set output
   contains index + N per-work sheets; `n_subset < N` gives
   `n_subset + 1` sheets and preserves verdict order;
   `n_subset >= N` returns the full set; `--subset-seed`
   reproducibility (same seed → same selection); tab-name
   collisions get suffixed; sentinel verdict renders with empty
   `relevance_score` cell; missing-work-id raises.
2. **CLI + docs.** Register
   `screening-abstracts-export-xlsx` in
   [cli.py](../src/laglitsynth/cli.py) after the CSV export. CLI
   smoke test covering default and custom `--output`, plus
   `--n-subset` smaller than total. Add an "XLSX review workbook"
   subsection to
   [docs/screening-abstracts.md](../docs/screening-abstracts.md)
   alongside the existing "Human review export" section — same CLI
   block style, short column-layout description.

## Follow-ups

`screening-adjudication --from-xlsx` to ingest per-sheet reviewer
edits; shared `laglitsynth.review_export` sampling helper when a
second stage needs subset export; per-work hyperlink back to the
index sheet once reviewers ask for it.

## Risks

openpyxl is pure-Python but adds a dep; tolerable given the use
case. Tab names > 31 chars or containing `/\?*[]:` break Excel;
the short-id helper trims that surface, covered by tests.

## Critical files

- [src/laglitsynth/screening_abstracts/export.py](../src/laglitsynth/screening_abstracts/export.py)
- [src/laglitsynth/screening_abstracts/models.py](../src/laglitsynth/screening_abstracts/models.py)
- [src/laglitsynth/io.py](../src/laglitsynth/io.py)
- [src/laglitsynth/cli.py](../src/laglitsynth/cli.py)
- [pyproject.toml](../pyproject.toml)
- [docs/screening-abstracts.md](../docs/screening-abstracts.md)
