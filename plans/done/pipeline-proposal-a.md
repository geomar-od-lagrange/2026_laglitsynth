# Proposal A: Linear Pipeline

## Overview

A strictly linear pipeline from broad discovery through progressive narrowing
and enrichment, ending in structured synthesis. Each stage produces a
persistent artifact that feeds the next, with human review at three natural
checkpoints.

## Stages

### 1. fetch-publications *(exists)*

Keyword searches against OpenAlex retrieve the retrieved corpus of publication
metadata and abstracts. The goal is high recall: it is better to pull too many
records than to miss relevant work. Output records are validated against a
schema and written to JSONL, providing a stable, reproducible snapshot of the
retrieved corpus.

- **Consumes:** keyword lists, OpenAlex API
- **Produces:** `retrieved-corpus.jsonl` — validated Work records (title,
  abstract, authors, date, topics, DOI, referenced_works)

### 2. screen-abstracts *(exists)*

A local LLM reads each abstract and assigns a relevance score and verdict
against the project's scope (computational Lagrangian oceanography; numerical
method choices). This stage drastically reduces the retrieved corpus without
requiring human effort on every record. Records above a score threshold are
promoted; borderline cases are flagged for adjudication.

- **Consumes:** `retrieved-corpus.jsonl`
- **Produces:** `screened.jsonl` — records annotated with relevance score,
  verdict, and flag for adjudication

### 3. adjudication

A human reviewer inspects the flagged borderline cases from screening, and
also spot-checks a random sample of accepted and rejected records to assess
screening quality. This quality gate prevents systematic LLM errors from
propagating downstream. Reviewers record accept/reject decisions and any
notes; the result is an included corpus, the trusted set ready for full-text
retrieval.

- **Consumes:** `screened.jsonl` (borderline and sampled records)
- **Produces:** `included-corpus.jsonl` — accepted records with provenance
  (auto-accepted, human-confirmed, human-overridden)

### 4. fetch-full-text

For each record in the included corpus, the pipeline attempts to retrieve the
full text of the paper (via open-access routes using the DOI). Full text is
necessary to extract methodological detail that is absent from or underreported
in abstracts. Where full text is unavailable, the abstract alone is retained
with a flag indicating source basis.

- **Consumes:** `included-corpus.jsonl` (DOIs)
- **Produces:** `fulltext.jsonl` — included corpus records augmented with
  full-text content or a source basis flag

### 5. data-extraction

An LLM reads each paper's available text and extracts a structured set of
attributes relevant to the research questions: sub-discipline (water parcels /
tracers / objects), whether numerical methods are described reproducibly (RQ1.1),
which numerical choices are made (e.g. integration scheme, interpolation order,
time step) (RQ1.2), and any stated rationale for those choices (RQ1.3). A
codebook is used to keep outputs comparable across papers. Confidence scores are
attached to each extracted field.

- **Consumes:** `fulltext.jsonl`, codebook
- **Produces:** `extracted.jsonl` — per-paper structured extraction records

### 6. adjudication (extraction)

A human reviewer audits a stratified sample of extraction records — checking
that attributes are correctly assigned and that the codebook is applied
consistently. Systematic errors found here are used to revise the extraction
prompt and re-run the stage on affected records. This iterative adjudication
loop is the primary quality control on the structured data.

- **Consumes:** `extracted.jsonl` (sampled records)
- **Produces:** validated `extracted.jsonl` with any corrections applied;
  updated codebook if needed

### 7. quantitative-synthesis

The validated extraction records are used to compute the statistics that answer
each research question. RQ1.1 is addressed by counting reproducibility flags
per sub-discipline. RQ1.2 is addressed by tabulating the prevalence of each
numerical choice, broken down by sub-discipline. RQ1.3 is addressed by
collating and thematically grouping the extracted rationale text. The outputs
of this stage are summary tables and grouped rationale excerpts — not prose.

- **Consumes:** validated `extracted.jsonl`
- **Produces:** `summary-tables/` — structured tables (counts, proportions,
  distributions); `rationale-excerpts/` — grouped verbatim rationale text

### 8. narrative-synthesis

An LLM (or human author) drafts narrative synthesis sections for each research
question, grounded in the summary tables and rationale excerpts. The narrative
synthesis interprets patterns, notes gaps, and flags where evidence is thin or
contradictory. This is the stage that produces the text destined for the review
manuscript.

- **Consumes:** `summary-tables/`, `rationale-excerpts/`
- **Produces:** `synthesis-drafts/` — narrative sections per research question,
  with citations to source records

### 9. author-review

The human authors read the synthesis drafts, verify claims against source
records, and finalise the manuscript sections. This is not an automated
stage — it is the final scholarly accountability step.

- **Consumes:** `synthesis-drafts/`, `extracted.jsonl` (for spot-checking
  claims)
- **Produces:** finalised manuscript sections

## Human review checkpoints

| Checkpoint | Stage | Purpose |
|---|---|---|
| Screening QA | adjudication | Catch LLM over/under-inclusion before expensive full-text fetch |
| Extraction QA | adjudication (extraction) | Ensure structured data quality before quantitative synthesis |
| Scholarly sign-off | author-review | Final accountability for published claims |

## Pipeline flowchart

```mermaid
graph TD
    KW[("keyword lists")]
    OA[("OpenAlex API")]
    RETR[("retrieved-corpus.jsonl")]
    SCR[("screened.jsonl")]
    INC[("included-corpus.jsonl")]
    FT[("fulltext.jsonl")]
    EX[("extracted.jsonl")]
    ST[("summary-tables/")]
    RE[("rationale-excerpts/")]
    SD[("synthesis-drafts/")]

    FETCH[fetch-publications]
    SCREEN[screen-abstracts]
    ADJUD[adjudication]
    RETRIEVE[fetch-full-text]
    EXTRACT[data-extraction]
    ADJUD2[adjudication (extraction)]
    QSYNTH[quantitative-synthesis]
    NARR[narrative-synthesis]
    AUTHOR[author-review]

    KW --> FETCH
    OA --> FETCH
    FETCH --> RETR
    RETR --> SCREEN
    SCREEN --> SCR
    SCR --> ADJUD
    ADJUD --> INC
    INC --> RETRIEVE
    RETRIEVE --> FT
    FT --> EXTRACT
    EXTRACT --> EX
    EX --> ADJUD2
    ADJUD2 --> EX
    EX --> QSYNTH
    QSYNTH --> ST
    QSYNTH --> RE
    ST --> NARR
    RE --> NARR
    NARR --> SD
    SD --> AUTHOR
```
