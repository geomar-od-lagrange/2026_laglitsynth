# LLM-stage timeouts and reviewer-xlsx restructure

## Goal

Three threads of work driven by feedback from the first production
NESH run (`nesh-pipeline-22047331`, branch `wr/nesh-prod`).

The run died at `extraction-codebook` iter 1562/3043 with an
`openai.APITimeoutError`, killing the whole stage and discarding ~7
hours of upstream work. Fix one: configure timeouts and retries on
the Ollama-backed OpenAI client in stages 3, 7, 8 and turn the
per-work failure into an `llm-timeout` sentinel so a single hang no
longer blows up a stage.

The screening sentinel for missing abstracts (`no-abstract`) currently
flows through stage 4 as a rejection — `relevance_score is None` ends
up below threshold. That is wrong: a missing abstract is not evidence
of irrelevance and should not foreclose full-text retrieval. Fix two:
stage 4 treats every null-score sentinel (`no-abstract`,
`llm-parse-failure`, new `llm-timeout`) as pass-through.

The stage-3 reviewer xlsx needs UX work per [remarks.md](../remarks.md):
enriched header, criterion shown verbatim, percent-formatted score,
reviewer placeholders, alignment fixes. Plus two design decisions
settled in conversation: the LLM and the human get the same
bibliographic context (title, authors, year, abstract) so scoring is
symmetric and head-to-head; and the LLM verdict block is collapsed
by default in the per-work sheet so the reviewer is not confronted
with a score before forming their own.

## Non-goals

Resumability of LLM stages — every NESH job runs from scratch by
design. Validation of reviewer cells (data validation, drop-downs):
deferred — placeholders are enough; per-reviewer consistency can be
fixed at ingest. Aggregating "has-this-row-been-filled-in" in the
index: deferred — easily verified by eye now, and Excel cell comments
are an option if we revisit. Adding CLI flags to override
timeouts/retries: hard-coded constants are consistent with the existing
`_TEMPERATURE = 0.8` shape and easy to change in code.

## Target state

### LLM-stage robustness

Three stages call Ollama via the OpenAI Python client:
[`screening_abstracts/screen.py`](../src/laglitsynth/screening_abstracts/screen.py),
[`fulltext_eligibility/eligibility.py`](../src/laglitsynth/fulltext_eligibility/eligibility.py),
[`extraction_codebook/extract.py`](../src/laglitsynth/extraction_codebook/extract.py).
Each constructs `OpenAI(base_url=..., api_key="ollama")` with no
`timeout=` and no `max_retries=`. The defaults are 600 s and 2 retries
(but retries fire on connection errors, not on a clean timeout). Today
a single hang kills the stage; a 1562/3043 partial dies with no
sentinel, no continuation.

After:

```python
# module-level constant per stage
_LLM_TIMEOUT_SECONDS = 60   # stage 3 (short title+abstract prompt)
_LLM_TIMEOUT_SECONDS = 300  # stage 7 (full text up to ~32K)
_LLM_TIMEOUT_SECONDS = 600  # stage 8 (full text + 30-field JSON output)
_LLM_MAX_RETRIES = 3

client = OpenAI(
    base_url=f"{base_url}/v1",
    api_key="ollama",
    timeout=_LLM_TIMEOUT_SECONDS,
    max_retries=_LLM_MAX_RETRIES,
)
```

Initial values are caps for "is the model wedged," not estimates of
expected generation latency. The April benchmarks under
[logs/bench-h100*](../logs/) show llama3.1:8b long-prompt generation
in the tens of seconds on H100; on V100 + 32K context the slowest
realistic generation should still leave large headroom under 600 s.
Tune in code if a real run hits these.

The OpenAI client treats `APITimeoutError` as retryable with
exponential backoff, so `max_retries=3` gives 4 total attempts per
work before the exception escapes.

When all retries fail the per-work call site catches
`APITimeoutError` and `APIConnectionError` and emits a new sentinel:

| Sentinel | Stages | Trigger |
|---|---|---|
| `llm-timeout` | 3, 7, 8 | All retries exhausted on `APITimeoutError` or `APIConnectionError`. |

Sentinel shape mirrors the existing `llm-parse-failure` sentinel in
each stage: same tri-state on `eligible` / `relevance_score`, every
content field `None`, `seed=None`, `raw_response=None`. The exception's
string repr goes in `reason` for operator visibility.

The per-stage `*Meta` model gains `llm_timeout_count: int` next to its
existing `llm_parse_failure_count`:
[`ScreeningMeta`](../src/laglitsynth/screening_abstracts/models.py),
[`EligibilityMeta`](../src/laglitsynth/fulltext_eligibility/models.py),
[`ExtractionCodebookMeta`](../src/laglitsynth/extraction_codebook/models.py).

Stage 3 already runs LLM calls on a `ThreadPoolExecutor` and yields
sentinels in the main thread; the timeout catch lives in the worker
function, returning the sentinel verdict. Stages 7 and 8 are sequential
— the catch goes inline around the `client.chat.completions.create`
call.

### Stage 3 input: symmetric bibliographic context

Today stage 3 sends only the abstract to the LLM
([`screen.py:66`](../src/laglitsynth/screening_abstracts/screen.py)).
The reviewer xlsx then either has to suppress all bibliographic info
to keep the human-vs-LLM comparison fair, or accept that the human
has more context. The cleaner solution is to give the LLM the same
"standard literature listing" tier the human gets:

```
Criterion: <criterion>

Title: <work.title>
Authors: <comma-joined display names from work.authorships>
Year: <work.publication_year>
Abstract: <work.abstract>
```

A helper `format_screening_input(work: Work) -> str` builds the
title/authors/year/abstract block; the criterion stays in the
existing user-message prefix. `prompt_sha256` shifts naturally — the
hash already covers the full user-template shape.

Edge cases: `work.title is None` and `work.publication_year is None`
render as `<unknown>`. Authors join with `, `; an empty list renders
as `<unknown>`. Abstract handling is unchanged — works without an
abstract still get the `no-abstract` sentinel and skip the LLM call
(see [Stage 4](#stage-4-null-score-pass-through) below). Title alone
is too noisy a signal to base a relevance score on, so we do not call
the LLM with title-only input.

### Stage 4 null-score pass-through

[`screening_adjudication/adjudicate.py:78`](../src/laglitsynth/screening_adjudication/adjudicate.py)
currently rejects every verdict with `relevance_score is None`. This
silently drops `no-abstract`, `llm-parse-failure`, and (after this
plan) `llm-timeout` sentinels — none of which are evidence of
irrelevance. A no-abstract work might still have a fetchable PDF;
stage 7's full-text eligibility pass is exactly the right place for
the real call.

After: only an explicit numeric score below threshold excludes.

```python
if verdict.relevance_score is not None and verdict.relevance_score < threshold:
    continue
```

[`AdjudicationMeta`](../src/laglitsynth/screening_adjudication/models.py)
gains `accepted_null_score_count: int` so an operator can see how
many sentinels rode through. The existing `accepted_count` continues
to total all `included.jsonl` entries.

This is a behaviour change for stage 5 onward: with a 22% no-abstract
rate on the smoke run, hundreds of additional works will reach
fulltext-retrieval. That's the intent.

### Reviewer xlsx restructure

All changes in
[`src/laglitsynth/screening_abstracts/export.py`](../src/laglitsynth/screening_abstracts/export.py),
in `build_index_sheet` and `build_work_sheet`. The CSV exporter is
unchanged. The CLI gains one optional argument:

```
laglitsynth screening-abstracts-export --format xlsx \
    --verdicts ... --catalogue ... \
    [--meta data/run/screening-abstracts/screening-meta.json] ...
```

`--meta` defaults to `<verdicts parent>/screening-meta.json`. The
current `ScreeningMeta` records `prompt_sha256` but not the prompt
text; this plan adds a `prompt: str` field so the export can quote it
back verbatim to the reviewer.

#### Index sheet layout

Three reviewer-identity rows above the table, then the table:

```
A1: reviewer_name      B1: <enter reviewer name here>
A2: reviewer_email     B2: <enter reviewer email here>
A3: review_date        B3: <YYYY-MM-DD>
A4: (blank)
A5: work_id  B5: title  C5: authors  D5: journal  E5: year  F5: llm_score  G5: llm_reason  H5: sheet
A6..N+5: data rows
```

Header row at row 5, frozen. Column widths sized for content;
`llm_score` formatted as percent (see [scoring](#scoring-and-percent-format)).
The Index keeps the LLM score visible — it is the navigation surface,
not the scoring surface, and a reviewer triaging which works to
spot-check needs the score to pick borderline cases. The blind-by-
default treatment lives on the per-work sheet.

#### Per-work sheet layout

Bibliographic block at the top — the same fields shown to the LLM at
screening time, plus journal and links for follow-up. Criterion and
scoring cells in the middle. LLM verdict and metadata at the bottom,
collapsed.

| Row | Field | Value |
|---|---|---|
| 1 | (back link) | `← back to Index` hyperlink → `'Index'!A1` |
| 2 | (blank) | |
| 3 | title | `work.title` (wrapped) |
| 4 | authors | comma-joined display names from `work.authorships` |
| 5 | journal | `work.primary_location.source.display_name` (or blank) |
| 6 | publication_year | `work.publication_year` |
| 7 | doi | clickable hyperlink to `work.doi` (display: bare DOI string) |
| 8 | openalex | clickable hyperlink to `https://openalex.org/{trailing_id}` |
| 9 | abstract | wrapped text |
| 10 | (blank) | |
| 11 | criterion | the screening prompt, verbatim from meta |
| 12 | scoring_instructions | `Score 0% (not relevant) to 100% (perfectly relevant)` |
| 13 | reviewer_score | `<insert relevance between 0% and 100% here — score based on info provided above only>` |
| 14 | reviewer_reason | `<give short (max 3 sentences) reason for the relevance score>` |
| 15 | (blank) | |
| 16 | LLM details (expand to peek) | header row, bold, visible |
| 17 | llm_score | `verdict.relevance_score` (percent-formatted) |
| 18 | llm_reason | `verdict.reason` (wrapped) |
| 19 | llm_model | `meta.llm.model` |
| 20 | llm_temperature | `meta.llm.temperature` |
| 21 | llm_prompt_sha256 | `meta.llm.prompt_sha256` |
| 22 | llm_raw_response | `verdict.raw_response` (wrapped) |

Field-name column (A) top-aligned. Value column (B) top + left
aligned, `wrap_text=True`. No explicit row heights on `abstract`,
`llm_reason`, `llm_raw_response`, or `reviewer_reason` — Excel
auto-sizes wrapped cells acceptably and the user called out fixed
heights as a problem.

Rows 17–22 collapse via openpyxl row outlining:

```python
ws.sheet_properties.outlinePr.summaryBelow = False
for row_idx in range(17, 23):
    ws.row_dimensions[row_idx].outline_level = 1
    ws.row_dimensions[row_idx].hidden = True
```

`summaryBelow = False` puts the `+`/`−` button next to row 16 (the
visible "LLM details" header). The reviewer has to actively click to
reveal the LLM verdict — protection against being confronted with
"LLM says 10%" before forming their own opinion. The full block
remains one click away for cross-check after scoring. Excel,
LibreOffice, and Numbers all honour the outline grouping.

Notes for sentinel verdicts (`no-abstract`, `llm-parse-failure`,
`llm-timeout`): `llm_score` cell is blank, `llm_reason` carries the
sentinel string, `llm_raw_response` is blank for `no-abstract` /
`llm-timeout` and carries the malformed text for
`llm-parse-failure`. Same treatment as today, just inside the
collapsed block.

#### Scoring and percent format

The LLM emits an integer 0–100. To make Excel display it as `85%`
without changing the underlying data model, store it as `value/100.0`
in the cell with `number_format="0%"`. Reviewer cells contain the
placeholder string at export time; when the reviewer overwrites with
`0.85` or `85%` Excel handles both.

The criterion text (row 11) and the scoring instruction (row 12) are
the same question shown to the LLM at screening time. The current
screening criterion in
[`scripts/run-pipeline.sh`](../scripts/run-pipeline.sh) is
`"Is this abstract about Lagrangian particle tracking in oceanography?"`
— a yes/no question, although the LLM is asked for a 0–100 score and
the reviewer is asked the same. Rewrite the script's criterion to
phrase the question as a score, e.g.:

```
"On a scale from 0% (not relevant) to 100% (perfectly relevant), how
relevant is this work to Lagrangian particle tracking in oceanography?"
```

One line in the runner script. The screening `SYSTEM_PROMPT` already
asks for a 0–100 integer, so no source code change there.

#### Hyperlinks

Three hyperlink kinds in the workbook:

- Index → per-work sheet: `Hyperlink(location="'<sheet>'!A1")` (already
  implemented).
- Per-work → Index: `Hyperlink(location="'Index'!A1")` at row 1.
- DOI / openalex.org: external `Hyperlink(target="https://...")` via
  `openpyxl.worksheet.hyperlink.Hyperlink(target=..., display=...)`.

### Doc updates

- [`docs/screening-abstracts.md`](../docs/screening-abstracts.md) —
  add `llm-timeout` to the sentinel reasons table; document the
  timeout/retry constants; document the new title/authors/year/abstract
  input format; rewrite the XLSX section to match the new layout
  (collapsed LLM block, hyperlinks, percent format, criterion + LLM
  details sections).
- [`docs/screening-adjudication.md`](../docs/screening-adjudication.md)
  — document the null-score pass-through rule and what it means for
  stages 5–7 (sentinels reach fulltext-retrieval).
- [`docs/eligibility.md`](../docs/eligibility.md) — add `llm-timeout`
  to the sentinel reasons table; document the constants.
- [`docs/extraction-codebook.md`](../docs/extraction-codebook.md) —
  same.
- [`plans/roadmap.md`](roadmap.md) — already updated; move to
  "Implemented" when the work lands.

[`README.md`](../README.md) needs no change: the export CLI shape
stays the same and the new `--meta` arg has a sensible default. The
screening criterion example in the runner script is updated, but the
README does not quote it.

## Implementation sequence

Seven commits. `pixi run typecheck` and `pixi run test` pass between
each.

1. **Stage 3 timeouts + `llm-timeout` sentinel.** Add `_LLM_TIMEOUT_SECONDS=60`
   and `_LLM_MAX_RETRIES=3` constants in
   [`screen.py`](../src/laglitsynth/screening_abstracts/screen.py); pass
   them to `OpenAI(...)`. Catch `APITimeoutError`/`APIConnectionError`
   in `classify_abstract`, return a sentinel verdict. Add
   `llm_timeout_count` to `ScreeningMeta`. Update
   [`docs/screening-abstracts.md`](../docs/screening-abstracts.md)
   sentinel-reasons table. Tests: mock the client to raise
   `APITimeoutError`; assert sentinel emitted, count incremented, no
   exception escapes the per-work boundary; same for
   `APIConnectionError`; existing tests still pass.

2. **Stage 7 timeouts + `llm-timeout` sentinel.** Same shape in
   [`fulltext_eligibility/eligibility.py`](../src/laglitsynth/fulltext_eligibility/eligibility.py)
   with `_LLM_TIMEOUT_SECONDS=300`. Sentinel branch in the per-work
   loop is inline `try/except` (sequential). Add count to
   [`EligibilityMeta`](../src/laglitsynth/fulltext_eligibility/models.py).
   Update [`docs/eligibility.md`](../docs/eligibility.md). Tests:
   mock-client timeout → sentinel; meta count; sequential loop
   continues after a timeout.

3. **Stage 8 timeouts + `llm-timeout` sentinel.** Same in
   [`extraction_codebook/extract.py`](../src/laglitsynth/extraction_codebook/extract.py)
   with `_LLM_TIMEOUT_SECONDS=600`. Add count to
   [`ExtractionCodebookMeta`](../src/laglitsynth/extraction_codebook/models.py).
   Update [`docs/extraction-codebook.md`](../docs/extraction-codebook.md).
   Tests as above.

4. **Stage 4 null-score pass-through.** Flip the threshold check in
   [`adjudicate.py:78`](../src/laglitsynth/screening_adjudication/adjudicate.py)
   so that `relevance_score is None` includes; only numeric below
   threshold excludes. Add `accepted_null_score_count` to
   [`AdjudicationMeta`](../src/laglitsynth/screening_adjudication/models.py).
   Update [`docs/screening-adjudication.md`](../docs/screening-adjudication.md).
   Tests: a `no-abstract` sentinel is included; a `llm-parse-failure`
   sentinel is included; a `llm-timeout` sentinel is included; a
   numeric `relevance_score=10, threshold=50` is still excluded; the
   meta count reflects null-score inclusions.

5. **Stage 3 input change: title/authors/year/abstract.** Add a
   `format_screening_input(work: Work) -> str` helper in
   [`screen.py`](../src/laglitsynth/screening_abstracts/screen.py)
   producing the `Title:/Authors:/Year:/Abstract:` block. Have
   `classify_abstract` accept the formatted string (or take a `Work`
   directly — pick the shape that minimises caller churn). Add a
   `prompt: str` field to
   [`ScreeningMeta`](../src/laglitsynth/screening_abstracts/models.py)
   and have the run record it. Update
   [`docs/screening-abstracts.md`](../docs/screening-abstracts.md)
   input format. Tests: helper renders all four lines; missing title
   /year render as `<unknown>`; empty authors render as `<unknown>`;
   `prompt_sha256` is recorded; `ScreeningMeta.prompt` is recorded.

6. **Reviewer xlsx restructure.** Add `--meta` CLI arg (defaulting
   to the verdicts sibling). Rewrite `build_index_sheet` and
   `build_work_sheet` in
   [`export.py`](../src/laglitsynth/screening_abstracts/export.py) to
   the layouts in [Index sheet layout](#index-sheet-layout) and
   [Per-work sheet layout](#per-work-sheet-layout). Update
   [`docs/screening-abstracts.md`](../docs/screening-abstracts.md)
   XLSX section. Tests: every new row present in the right position;
   back-to-Index hyperlink targets `'Index'!A1`; DOI and openalex.org
   cells are external hyperlinks; `llm_score` formatted as percent;
   reviewer header rows present at top of Index; placeholder strings
   present in reviewer cells; col-A top-aligned, col-B top+left
   aligned, wrap on; no explicit row heights on the long-text rows;
   LLM section rows have `outline_level == 1` and `hidden == True`,
   `summaryBelow == False`; "LLM details" header row visible at row
   16; LLM rows populated from meta; criterion row shows the prompt
   string read from meta; sentinel verdicts render with blank
   `llm_score` and the sentinel string in `llm_reason`.

7. **Update screening criterion in runner.** Rewrite the criterion
   string in [`scripts/run-pipeline.sh`](../scripts/run-pipeline.sh) to
   the scoring form. Update example invocations in
   [`docs/screening-abstracts.md`](../docs/screening-abstracts.md). No
   source change to the screening `SYSTEM_PROMPT`.

## Follow-ups

- Excel cell comments on each filled-in placeholder — would let us
  surface a per-row "filled?" tick in the Index without parsing the
  cell content.
- `screening-abstracts-export-ingest` (stage 4 hookup) reading reviewer
  cells back into the verdict pipeline; placeholder-vs-real values get
  filtered at ingest.
- Reviewer-export equivalents for stages 7 and 8 — same shape, same
  helper module once stage 3's settles.
- Make `OLLAMA_NUM_PARALLEL` and the timeout pair env-overridable in
  [`scripts/nesh-pipeline.sbatch`](../scripts/nesh-pipeline.sbatch) if
  a future run wants to widen them without a code change.

## Risks

Mocking the OpenAI client well enough to fire `APITimeoutError`
without hitting Ollama for real: use `unittest.mock.patch` on the
client's `chat.completions.create` to raise; the openai-python error
classes are importable and constructable with a fake `request=`. This
is already the test pattern for `llm-parse-failure`.

Stage 4 pass-through inflates the input volume to stages 5–7. With a
22% no-abstract rate on the first NESH run, that's a meaningful jump
in fulltext-retrieval and eligibility load. Acceptable — the alternative
is silently dropping works on a non-substantive ground — but worth
flagging in the run summary so an operator notices.

## Critical files

- [src/laglitsynth/screening_abstracts/screen.py](../src/laglitsynth/screening_abstracts/screen.py)
- [src/laglitsynth/screening_abstracts/models.py](../src/laglitsynth/screening_abstracts/models.py)
- [src/laglitsynth/screening_abstracts/export.py](../src/laglitsynth/screening_abstracts/export.py)
- [src/laglitsynth/screening_adjudication/adjudicate.py](../src/laglitsynth/screening_adjudication/adjudicate.py)
- [src/laglitsynth/screening_adjudication/models.py](../src/laglitsynth/screening_adjudication/models.py)
- [src/laglitsynth/fulltext_eligibility/eligibility.py](../src/laglitsynth/fulltext_eligibility/eligibility.py)
- [src/laglitsynth/fulltext_eligibility/models.py](../src/laglitsynth/fulltext_eligibility/models.py)
- [src/laglitsynth/extraction_codebook/extract.py](../src/laglitsynth/extraction_codebook/extract.py)
- [src/laglitsynth/extraction_codebook/models.py](../src/laglitsynth/extraction_codebook/models.py)
- [scripts/run-pipeline.sh](../scripts/run-pipeline.sh)
- [docs/screening-abstracts.md](../docs/screening-abstracts.md)
- [docs/screening-adjudication.md](../docs/screening-adjudication.md)
- [docs/eligibility.md](../docs/eligibility.md)
- [docs/extraction-codebook.md](../docs/extraction-codebook.md)
- [plans/roadmap.md](roadmap.md)
- [remarks.md](../remarks.md)
- [nesh-pipeline-22047331.err](../nesh-pipeline-22047331.err)
