# Reviewer-facing exports for LLM-driven stages

Design rationale behind the CSV and XLSX exports for
[screening-abstracts.md](../screening-abstracts.md) (stage 3), plus
the shape we'd extend to stages 7, 8, and 9 when the same review
problem arises there. Captures what was considered and rejected so
the next iteration doesn't retrace the same ground.

## The problem

Every LLM-driven stage emits JSONL (one verdict per work). JSONL is
the right shape for inter-stage plumbing — schema-validated, streamable,
append-safe — but it is **not** what a human reviewer wants to look
at when spot-checking the LLM. Abstracts are long, scores need
sorting, and reviewer decisions have to flow back into the pipeline
somehow.

## Options considered

### 1. Flat CSV (shipped as `screening-abstracts-export`)

- Join `verdicts.jsonl` with the dedup catalogue; one row per work;
  columns for title / abstract / score / llm_reason / empty reviewer
  columns / raw_response.
- Excel, Numbers, LibreOffice all open it. Reviewer sorts, filters,
  fills two columns, saves.
- Ingest-back path: a stage-4 CLI consuming the edited CSV, validating
  the reviewer_decision enum, emitting `AdjudicationVerdict` records.
- **Pro**: cheapest possible shape, no new dependency, round-trips
  safely (UTF-8 with BOM + stdlib csv dialect handles commas / quotes
  / embedded newlines).
- **Con**: abstracts + raw_response in one row make Excel rows huge;
  horizontal scrolling; deep read is painful.

### 2. Static HTML report with client-side feedback capture

- Render per-work cards with radios / textareas bound to JS state.
- `localStorage` autosaves; "Download decisions.jsonl" serialises
  into the exact AdjudicationVerdict shape.
- No backend. Works from `file://` or `python -m http.server` in the
  report directory.
- **Pro**: richer review UX than a spreadsheet; write-back path
  captures decisions in the canonical JSONL shape directly.
- **Con**: whole new artifact to maintain; `file://` quirks with
  `localStorage`; concurrent-reviewer story is manual ("ship me your
  downloaded JSONL"). Deferred.

### 3. Per-work XLSX workbook (shipped as `screening-abstracts-export-xlsx`)

- One `Index` sheet (hyperlinked one-row-per-work summary) plus one
  tab per work with a vertical `Field | Value` layout.
- Abstract and `raw_response` wrap into tall cells — no horizontal
  scrolling; deep review feels natural.
- `--n-subset N` + `--subset-seed` draw a reproducible random sample
  in verdict order; falls back to the full set at `N >= total`.
- **Pro**: the per-work deep-read surface that CSV is bad at. Still
  offline, still no backend, still round-trippable later.
- **Con**: needs `openpyxl` (added to deps); 500 tabs per workbook is
  ugly (the `--n-subset` flag exists for exactly this reason — you
  spot-check 20–50 papers, not the whole catalogue).

### 4. Backed web app (Streamlit / FastAPI)

- Server-side state, auth, concurrent reviewers.
- **Con**: this is real infrastructure — deployment, persistence,
  auth. Explicitly scoped out until the pipeline is feature-complete
  and reviewing is the bottleneck. Currently a research tool with
  one reviewer at a time.

## Chosen shape

Ship both **flat CSV** and **per-work XLSX**. Different jobs:

- CSV = sort / filter / overview. Good at-a-glance across the whole
  sample.
- XLSX = deep review of individual works. Good at "does the LLM
  reason match what the abstract actually says?"

Reviewer picks the surface that fits the task. Both consume the same
`verdicts.jsonl` + catalogue inputs, so there's no configuration
divergence.

### Subset sampling (XLSX only)

500 abstracts in one workbook is ~500 tabs — unwieldy. The subset
flag solves it:

- `--n-subset 30 --subset-seed 1` draws 30 verdicts uniformly,
  reproducibly, in their original verdict-file order.
- `N >= len(verdicts)` (or `--n-subset` unset) emits the full set.
- One command covers both "spot-check 30" and "every record."

Not applied to the CSV — the flat export is the *overview* surface
by design. Its whole point is being complete.

### Sheet-name sanitation (XLSX)

Excel caps sheet names at 31 chars and forbids `/\?*[]:`. OpenAlex
IDs (`https://openalex.org/W3213722062`) don't fit either constraint.
`short_work_id()` strips to the trailing `W<digits>` and sanitises
anything else; collisions get `_2`, `_3`, … suffixes.

### In-workbook hyperlinks

Index → per-work tab links use openpyxl's `Hyperlink(location="'W...'!A1")`
form (not `target="#'W...'!A1"`). The location form is what Excel,
Numbers, and LibreOffice all follow consistently on open.

## Follow-ups explicitly deferred

- **Ingest back into stage 4.** A `--from-csv` / `--from-xlsx` flag
  on `screening-adjudication` that reads the reviewer columns,
  validates the enum, and writes `AdjudicationVerdict` records. Not
  built because stage 4 is still a pass-through — will land with the
  real adjudication stage.
- **Shared `laglitsynth.review_export` helper.** The same shape will
  apply to stages 4, 7, 8, 9 when each needs a review surface. Don't
  abstract on one stage; lift when a second stage needs it.
- **HTML variant for stage 8.** 30 field/context pairs per work is
  ugly in XLSX; a per-work HTML page would read better. Revisit when
  stage 8 enters review.
- **Concurrent reviewers.** Assume single reviewer at a time — they
  ship us their edited file. If the team grows, option 4 becomes
  worth it.

## Why not extend to stage 7 / 8 now

Each stage's export differs in detail: stage 7 is a binary verdict
with reasoning; stage 8 is a 30-field codebook record with `*_context`
excerpts. A shared export helper should be lifted once we have a
second concrete stage so the abstraction is informed, not guessed.
Until then, the stage-3 module stays local to `screening_abstracts/`
and the plan flags the generalisation as a follow-up.
