# Adjudication — extraction

Stage 9 (`extraction-adjudication`) is not implemented and has no plan. This
file records the input the stage will read and the contract questions still
open. See [interfaces.md](interfaces.md) for the stage boundaries around it.

## Input the stage will read

Stage 8 writes an XLSX review workbook on demand through
[`extraction-codebook-export`](../src/laglitsynth/extraction_codebook/export.py).
Its per-work tab lists every codebook field in three columns: `value`,
`context`, and `reviewer_correction`. A reviewer reads the verbatim `context`
the model cited for a field and writes a correction in the third column when
the value does not match it. `--n-subset` and `--subset-seed` draw the
reproducible sample of works to review.
[`fulltext-eligibility-export`](../src/laglitsynth/fulltext_eligibility/export.py)
writes the same shape of workbook for stage 7.

Stage 9 reads the filled `reviewer_correction` columns back and pairs them
with `records.jsonl` from stage 8. The workbook is therefore the human
interface, and stage 9 is the ingestion side of it. See
[extraction-codebook.md](extraction-codebook.md) for the workbook layout.

## Withdrawn pass-through design

An earlier version of this file specified stage 9 as a pass-through that
copies stage 8 records to a `validated.jsonl` unchanged, on the same reasoning
as the stage 4 pass-through. The
[verdicts-only cutover](../plans/done/verdicts-only-cutover.md) deleted stage 4
and every derived record copy it wrote. A gate stage now emits a sidecar
covering every input record, and each downstream stage joins that sidecar
against the deduplicated catalogue. Stage 9 follows the sidecar pattern. The
`ExtractionAdjudicationMeta` shape and the `validated.jsonl` path that the
withdrawn design named are not part of any current contract.

## Open contract questions

- Whether one correction sidecar keyed by work id and field name is enough, or
  whether a reviewer needs to record a verdict on the record as a whole.
- Whether stage 9 reads the workbook directly, or whether the reviewer's
  corrections are first converted to JSONL by a separate importer.
- Which agreement measures the stage records per field, given that a
  categorical field and a free-text field do not admit the same measure.
- Whether stage 3, stage 7, and stage 9 share one reviewer-ingestion helper.
  The three stages emit workbooks from three separate `export.py` modules
  today, and the
  [full-text-only plan](../plans/done/fulltext-stages-full-text-only-and-review.md)
  declined to force a shared abstraction before stage 9 shows what it needs.
