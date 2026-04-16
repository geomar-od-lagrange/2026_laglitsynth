# Adjudication — screening

Human review of LLM screening decisions. A stratified sample of accepted
and rejected records is inspected, and the screening threshold is adjusted
if needed.

## Prototype scope

**Pass-through.** The prototype implementation copies the screened
catalogue to the included catalogue unchanged. No human review, no
threshold adjustment. The stage exists so that:

- The pipeline has the correct stage sequence and input/output contracts.
- Downstream stages consume the included catalogue artifact, not the
  screened catalogue directly.
- Replacing the pass-through with real adjudication later is a local
  change — nothing downstream needs to know.

## CLI interface

```
laglitsynth adjudicate-screening \
    --input data/screening/screened.jsonl \
    --output-dir data/adjudication/
```

The command copies the input to `data/adjudication/included.jsonl` and
writes an `AdjudicationMeta` sidecar recording that no human review was
performed.

### AdjudicationMeta

```python
class AdjudicationMeta(_Base):
    tool: str = "laglitsynth.adjudication.screening"
    tool_version: str = "alpha"
    adjudicated_at: str
    mode: str = "pass_through"
    input_count: int
    output_count: int
    human_reviewed: int = 0
```

The `mode` field distinguishes `pass_through` from future modes
(`sampled_review`, `full_review`). Downstream stages do not inspect this
field — they just consume the included catalogue.

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
