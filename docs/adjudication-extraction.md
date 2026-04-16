# Adjudication — extraction (stage 9)

Human review of LLM extraction records. A sample of records is
spot-checked against source text, corrections are logged, and inter-rater
agreement is measured.

## Prototype scope

**Pass-through.** The prototype implementation copies the extraction
records to validated extraction records unchanged. Same rationale as the
screening adjudication pass-through (stage 4): wire the stage into the
pipeline with correct contracts, replace with real review later.

## CLI interface

```
laglitsynth adjudicate-extraction \
    --input data/extraction/records.jsonl \
    --output-dir data/adjudication-extraction/
```

The command copies the input to
`data/adjudication-extraction/validated.jsonl` and writes an
`ExtractionAdjudicationMeta` sidecar.

### ExtractionAdjudicationMeta

```python
class ExtractionAdjudicationMeta(_Base):
    tool: str = "laglitsynth.adjudication.extraction"
    tool_version: str = "alpha"
    adjudicated_at: str
    mode: str = "pass_through"
    input_count: int
    output_count: int
    human_reviewed: int = 0
```

## What the real implementation will add

When human QA begins:

- Random sampling of extraction records for review.
- A CLI or notebook that presents the extraction record alongside the
  source text and the codebook, and lets the reviewer correct fields.
- A corrections log (JSONL of field-level corrections with original and
  corrected values).
- Inter-rater agreement metrics (per field: exact match, Cohen's kappa
  for categorical fields, or simple agreement percentage).
- Feedback to the codebook if systematic extraction errors reveal schema
  problems.

None of this is needed for the prototype.
