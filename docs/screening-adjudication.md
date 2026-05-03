# Adjudication — screening

Human review of LLM screening decisions. A stratified sample of accepted
and rejected records is inspected, and the screening threshold is adjusted
if needed.

## Prototype scope

**Pass-through.** The prototype implementation accepts every work whose
`relevance_score` meets `--screening-threshold` *and* every work whose
`relevance_score` is `None` (the screening sentinels: `no-abstract`,
`llm-parse-failure`, `llm-timeout`). A null score is not evidence of
irrelevance — a missing abstract or a wedged LLM call should not
foreclose full-text retrieval. Only an explicit numeric score below
threshold excludes. The stage exists so that:

- The pipeline has the correct stage sequence and input/output contracts.
- Downstream stages consume `included.jsonl`, not the screened verdicts
  directly.
- Replacing the pass-through with real adjudication later is a local
  change — nothing downstream needs to know.

## CLI interface

```
laglitsynth screening-adjudication \
    --input data/screening-abstracts/verdicts.jsonl \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --screening-threshold 50 \
    --output-dir data/screening-adjudication/
```

Stage 4 reads stage 3's `verdicts.jsonl` and the deduplicated catalogue,
applies the threshold, and writes three output files.

## Output files

| Path | Description |
|---|---|
| `verdicts.jsonl` | One `AdjudicationVerdict` per accepted work. |
| `adjudication-meta.json` | Threshold, counts, timestamp. |
| `included.jsonl` | Work records above threshold (convenience for stage 5). |

### AdjudicationVerdict

```python
class AdjudicationVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    decision: Literal["accept", "reject", "skip"]
    reviewer: str
    adjudicated_at: str  # per-verdict wall-clock timestamp
    reason: str | None = None
```

The pass-through MVP sets `decision="accept"` and `reviewer="pass-through"`
for every work with `relevance_score >= screening-threshold` and for
every work with a null score (sentinel verdicts). For sentinels,
`reason` carries the screening sentinel string (`no-abstract`,
`llm-parse-failure`, `llm-timeout`) so an operator can see why the
work rode through without a numeric score. Real human adjudication
will populate `reviewer` with a user identifier and `reason` with a
justification when overriding the LLM verdict.

### AdjudicationMeta

```python
class AdjudicationMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta      # tool, tool_version, run_at, validation_skipped
    threshold: int
    input_count: int
    accepted_count: int
    rejected_count: int
    missing_in_catalogue: int
    accepted_null_score_count: int   # of accepted_count, how many were sentinels
```

## What the real implementation will add

When human QA begins, replace the pass-through with:

- Stratified sampling across the relevance-score distribution.
- A CLI or notebook that presents title + abstract + LLM verdict and asks
  accept/reject/skip.
- An adjudication log (JSONL of human decisions).
- Agreement statistics (human vs. LLM).
- Threshold adjustment based on the review.

None of this is needed for the prototype. The pass-through is the
deliberate choice: get the pipeline end-to-end first, tune later.
