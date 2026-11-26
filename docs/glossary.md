# Glossary — pipeline terminology

Terms for the literature-review pipeline. They follow established usage
in systematic-review methodology, NLP and social-science content
analysis where that usage fits; the pipeline produces a literature
overview in the natural sciences, so terms it does not use are left out.

## Pipeline structure

### stage

A discrete step in the pipeline. Each stage reads one or more artifacts
and writes one or more artifacts. Stages keep their original numbers
(1–3, 5–8); `corpus-assemble` sits between stage 5 and stage 6 and has
no number.

### artifact

A file produced by a stage. The pipeline's data flow is defined by which
stages produce and consume which artifacts.

### half

The pipeline has two halves that meet at the **corpus**. The first half
(fetch, dedup, screening, retrieval) identifies the literature and
acquires PDFs; the second half (extraction, eligibility, codebook
extraction, synthesis) analyses the papers in the corpus. See
[pipeline.md](pipeline.md).

### run-id

The leaf directory name an LLM stage writes under. The screening run-id
is kept in `data/manifest.json`; the analysis run-id of stages 7 and 8
is kept in `data/corpus/corpus-meta.json`. See
[run-manifest.md](run-manifest.md).

## Catalogue, corpus, and records

### work

A single publication record, modelled after the OpenAlex `Work`
schema. Contains metadata (title, authors, date, DOI, topics,
referenced works) and an abstract, no full text.

### catalogue

The collection of work records: metadata and abstracts, no full text.
Fetch, deduplication and screening operate on it. Qualifiers indicate
the step: *retrieved catalogue* (everything fetched) and *deduplicated
catalogue* (after deduplication), the single source of work records for
the first half.

### PDF store

The persistent, work-keyed collection of PDFs in `data/pdfs/`, one PDF
per work at most, with a provenance record per work. It is filled by
retrieval and by the import of PDFs returned by collaborators, and spans
every search a review runs. See [pdf-store.md](pdf-store.md).

### corpus

The self-contained set of works selected for analysis, written by
`corpus-assemble` to `data/corpus/`: the selected works, their screening
verdicts, copies of their PDFs, per-work PDF provenance and a record of
how it was made. Works without a PDF are members too. The corpus is
assembled after screening and retrieval and before full-text
extraction and eligibility, and the analysis half reads only the
corpus. See [corpus.md](corpus.md).

### corpus id

The content hash that identifies a corpus: the sha256 of each member's
work id and PDF hash. Two assemblies with the same members and PDF bytes
share it. Analysis outputs record it so stale output is detected.

### full text

The complete text of a paper, as parsed by GROBID from its PDF.
Eligibility and codebook extraction read only the full text; a corpus
work without a usable extraction is skipped by both.

## Search

### search strategy

The complete specification of a database query: search terms, Boolean
operators, field tags, date limits.

### deduplication

Removing duplicate records retrieved from the same or multiple
searches.

## Screening and eligibility

### screening

Assessing works for relevance against the review scope from title and
abstract. Here a decisions model answers one yes/no question per
abstract. The systematic-review community calls this *title-and-abstract
screening*; the NLP community calls the underlying task *text
classification*.

### verdict

The per-work output of a gate stage, stored in a `verdicts.jsonl`
sidecar. A screening verdict carries a relevance score; an eligibility
verdict carries a probability and the `eligible` flag. Either carries a
sentinel reason instead when no answer was obtained.

### relevance score

A number from 0 to 100, the decisions model's probability that a work is
relevant, times 100 and rounded.

### threshold

The relevance-score cutoff applied by `corpus-assemble`: works scoring
below it are left out of the corpus, works without a score are kept.
Works near the threshold are **borderline**.

### sentinel

A reason code recorded in place of an answer when a work could not be
assessed, for example `no-abstract`, `llm-timeout`, `llm-rejected`,
`llm-parse-failure` or `tei-parse-failure`. A sentinel row has no score
or value.

### eligibility

The full-text assessment of whether a work meets the review's inclusion
criteria. Distinct from screening, which uses only title and abstract.

### eligibility criteria

The inclusion rules that eligibility checks, given as the `decision`
block of the eligibility-criteria YAML.

## Data extraction

### data extraction

Reading a work's full text and producing a structured record of facts
relevant to the research questions. The NLP community calls the
underlying technique *information extraction*. In the pipeline it is
stage 8, `extraction-codebook`.

### codebook

The document that defines what to extract and how: field definitions
and decision rules. Called *annotation scheme* in NLP and *coding
scheme* in social-science content analysis. See [codebook.md](codebook.md).

### extraction record

One structured record per work, produced by data extraction. Contains
the codebook fields, each with a verbatim context snippet, plus the work
id, a sentinel reason and the raw response.

### context snippet

A short verbatim excerpt from the paper stored next to an extracted
value (`<field>_context`), so a reader can check the value without
re-reading the paper.

## Domain terms

### sub-discipline

A thematic grouping within computational Lagrangian oceanography. The
project proposal names three illustrative sub-disciplines (*water
parcels*, *tracers*, *objects*), but these are starting points, not an
exhaustive taxonomy. Extraction tags each work with whatever
sub-discipline terms best fit, in the paper's own vocabulary.

### numerical choice

A specific decision about how to set up a Lagrangian computation:
integration scheme, time-step strategy, interpolation method, etc. The
codebook enumerates the numerical choices tracked.

### methodological transparency

The degree to which a paper describes its methods in enough detail to
assess and reproduce them. Mapped to RQ1.1.

### reproducibility

Whether a paper describes its numerical methods (or provides code) in
enough detail that someone else could reproduce the computation. A
specific dimension of **methodological transparency**. Mapped to RQ1.1.
A paper without a methods description counts as non-reproducible.

### rationale

The reason a paper gives (if any) for its numerical choices. Extracted
as free text into the codebook's `rationale` field. Mapped to RQ1.3.

### prevalence

How common a given numerical choice is across the corpus, possibly
broken down by sub-discipline or year. Mapped to RQ1.2.

## Synthesis

### synthesis

The planned final step and the pipeline's only aggregation across
papers: summarising the extraction records into answers to the research
questions, with counts and proportions for RQ1.1 and RQ1.2 and a
grouping of the stated rationales for RQ1.3. Every claim it makes
traces back to the extraction records and the works behind them.
