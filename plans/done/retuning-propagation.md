# Plan: Re-tuning propagation

## Problem

The pipeline has gate stages (3, 4, 7, 9) that decide which works proceed
downstream. The current [interfaces.md](../docs/interfaces.md) design
**filters** records at each gate -- stage 3 splits works into
`screened.jsonl` and `rejected.jsonl`, stage 4 copies accepted works to
`included.jsonl`, stage 7 copies eligible works to `eligible.jsonl`.

Stage 3 already writes `verdicts.jsonl` with relevance scores for every work.
Changing the screening threshold from 50 to 40 does not require re-running
the LLM -- the scores exist. But in the current design, the threshold change
means re-splitting Work records between files and re-running every downstream
stage that consumes them.

## Proposed design: flag, don't filter

Gate stages write **verdict sidecars** but never copy Work records. Downstream
stages determine their input set at read time by joining the work catalogue
against upstream verdicts and thresholds.

### What each gate stage produces

| Stage | Sidecar file | Content per work |
|---|---|---|
| 3. screen-abstracts | `data/screening/verdicts.jsonl` | `FilterVerdict` (score, reason) |
| 4. adjudication | `data/adjudication/verdicts.jsonl` | `AdjudicationVerdict` (override, reason) |
| 7. eligibility | `data/eligibility/verdicts.jsonl` | `EligibilityVerdict` (decision, reason) |
| 9. adjudication (extr.) | `data/adjudication-extraction/verdicts.jsonl` | corrections to `ExtractionRecord` |

No `screened.jsonl`, `rejected.jsonl`, `included.jsonl`, or `eligible.jsonl`.
The deduplicated catalogue (`data/dedup/deduplicated.jsonl`) is the single
source of Work records for the entire pipeline.

### Where thresholds live

A single `data/params.json` file holds every tunable threshold:

```json
{
  "screening_threshold": 50,
  "eligibility_threshold": null
}
```

`null` means no numeric threshold (eligibility is a binary LLM verdict).
This file is human-edited, version-controlled, and read by every stage that
needs to resolve which works are active.

### How a stage determines its input set

Each stage resolves its active work set by joining the deduplicated catalogue
against verdict sidecars, applying thresholds from `params.json`:

```
stage 5 input = works from deduplicated.jsonl
                WHERE screening score >= params.screening_threshold
                AND adjudication verdict != excluded
```

This resolution logic lives in a shared module (`laglitsynth.resolve`), not
duplicated per stage.

### Data flow

```
deduplicated.jsonl ─── single Work catalogue, never copied
screening/verdicts.jsonl ─── scores per work
adjudication/verdicts.jsonl ─── human overrides
params.json ─── thresholds
       │
       ▼
  resolve() ── active work IDs ──► stages 5, 7, 8, ...
```

Stages 5-6 and 8-9 already write records keyed by work ID, not copies of
Work records. The change affects stages 3, 4, and 7.

### What happens on re-thresholding

#### Lowering a threshold (50 to 40)

New works enter the active set. Their verdict scores already exist.

- **Stages 5-8:** `--skip-existing` processes only newly-included works.
  No LLM or GROBID re-runs for works already processed.
- **Stage 4:** the reviewer sees only newly-included works not yet
  adjudicated.
- **Stages 10-12:** must re-run fully -- they aggregate across the entire
  active set.

#### Raising a threshold (50 to 60)

Works leave the active set. Their downstream artifacts (PDFs, extractions,
verdicts) remain on disk but are excluded from resolved results. No
deletion needed. Lowering the threshold again brings them back.

### Interaction with --skip-existing

`--skip-existing` checks whether an output artifact exists for a work ID,
regardless of whether that work is currently active. After lowering a
threshold, it skips already-processed works and handles only new ones.
After raising, all active works already have artifacts. No semantic change
needed.

## Changes from current interfaces.md

### Removed artifacts

- `data/screening/screened.jsonl`
- `data/screening/rejected.jsonl`
- `data/adjudication/included.jsonl`
- `data/eligibility/eligible.jsonl`

### New artifacts

- `data/params.json` -- tunable thresholds
- `data/adjudication/verdicts.jsonl` -- human overrides (replaces the
  accept-by-copy-to-included.jsonl pattern)

### CLI changes

Stages that currently take `--input data/adjudication/included.jsonl` or
`--catalogue data/eligibility/eligible.jsonl` instead take `--data-dir
data/` and resolve the active set internally. Stages 3, 4, and 7 stop
writing `Work` records (only verdict/meta sidecars). Every stage from 5
onward gains a dependency on the resolve module and `params.json`.

## Risks and open questions

### Resolve module complexity

The resolve function joins across multiple verdict files and applies
thresholds. Straightforward for two gates but grows if more are added.
Keep it in one place and test it well.

### Human adjudication workflow

In the flag model, the adjudication tool must itself resolve which works
need review (score above threshold, not yet adjudicated). This is more
logic in the adjudication tool but avoids the copy-and-split problem.

### Stale synthesis after re-thresholding

After re-thresholding, old synthesis files are stale but still on disk.
Start by relying on the human to re-run synthesis.

### params.json scope

Keep `params.json` narrow: only values that affect which works are active.
LLM model names, prompts, and codebook versions are stage-specific config.
