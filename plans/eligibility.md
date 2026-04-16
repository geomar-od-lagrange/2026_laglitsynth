# Eligibility (stage 7)

Full-text assessment of whether each work meets the review's inclusion
criteria. Distinct from screening (stage 3), which uses only title and
abstract. Reading the full text may reveal that a paper is not actually
about computational Lagrangian methods, or that it is a review article
rather than primary research.

## Prototype scope

A single LLM pass over the extracted text (or abstract for abstract-only
works). Same pattern as the existing `filter-abstracts` stage: structured
JSON output, Pydantic validation, verdicts stored separately for
re-thresholding.

The eligibility criteria below are provisional placeholders for the
prototype. They are deliberately broad — designed to let papers through,
not to be the final filter. The human turns knobs once the pipeline runs
and produces reviewable output. False positives are acceptable in the
prototype; false negatives are not.

1. The paper describes a computation that tracks particles, tracers, or
   objects in an ocean flow field.
2. The paper is primary research (not a review, editorial, or commentary).
3. The paper contains at least some description of the numerical methods
   used. (Under discussion: lack of method description is poor practice
   but may not disqualify a paper from relevance. This criterion may be
   relaxed in a later iteration.)

## Input

The stage consumes two artifacts:

- The included catalogue (`Work` records with metadata and abstracts).
- The extraction JSONL (`ExtractedDocument` records with structured
  sections) from the full-text extraction step.

For works that have an `ExtractedDocument`, the LLM sees the full text
(concatenated sections). For abstract-only works, the LLM sees the
abstract from the `Work` record.

## Data model

### EligibilityVerdict

```python
class EligibilityVerdict(_Base):
    work_id: str
    eligible: bool
    source_basis: str                    # "full_text" or "abstract_only"
    reason: str                          # LLM's explanation
    confidence: int                      # 0–100
```

### EligibilityMeta

```python
class EligibilityMeta(_Base):
    tool: str = "laglitsynth.eligibility.assess"
    tool_version: str = "alpha"
    assessed_at: str
    total_works: int
    eligible_count: int
    excluded_count: int
    by_source_basis: dict[str, int]
```

## Storage layout

```
data/eligibility/
  eligible.jsonl            # Work records that passed eligibility
  verdicts.jsonl            # EligibilityVerdict for every work
  eligibility-meta.json     # EligibilityMeta
```

The `verdicts.jsonl` file contains all verdicts (eligible and excluded),
mirroring the screened/rejected pattern from stage 3. The `eligible.jsonl`
file contains only the works that passed — this is the eligible corpus
consumed by stage 8.

## CLI interface

```
laglitsynth assess-eligibility \
    --catalogue data/adjudication/included.jsonl \
    --extractions data/fulltext/extraction.jsonl \
    --output-dir data/eligibility/ \
    [--skip-existing]
```

### Arguments

- `--catalogue`: the included catalogue (Work records).
- `--extractions`: the extraction JSONL (ExtractedDocument records). Works
  without a matching ExtractedDocument are assessed on their abstract.
- `--output-dir`: where to write verdicts and the eligible corpus.
- `--skip-existing`: skip works that already have a verdict.

## LLM prompt

The prompt is deliberately simple for the prototype:

```
You are assessing whether a scientific paper meets the inclusion criteria
for a systematic review of numerical methods in Lagrangian oceanography.

Criteria:
1. The paper describes a computation that tracks particles, tracers, or
   objects in an ocean flow field.
2. The paper is primary research (not a review, editorial, or commentary).
3. The paper contains at least some description of the numerical methods
   used.

Respond with JSON: {"eligible": true/false, "reason": "...",
"confidence": 0-100}
```

The prompt will be refined during tuning. The structured output and
validation pattern is identical to `filter-abstracts`.

## Export for human review

Eligibility verdicts are exported as a flat table (e.g. CSV) for
spot-checking. The table contains one row per work: work ID, title,
verdict, reason, confidence, and source basis. This follows the general
pattern that every LLM-driven stage produces output reviewable by a
human without specialised tooling.

## What to defer

- Fine-grained exclusion reasons (enum of why a paper was excluded).
- Multi-criteria assessment (separate verdict per criterion).
- Calibration against human judgments (that is stage 4/9 territory).
- Threshold-based re-eligibility (currently binary; could add a score
  like screening does).
