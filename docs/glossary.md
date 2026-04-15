# Glossary — pipeline terminology

Terms for the literature-review pipeline, aligned with established usage from
systematic-review methodology (PRISMA / Cochrane), NLP / corpus linguistics,
bibliometrics, and social-science content analysis. Where these communities
disagree, the dominant convention for systematic reviews wins — since the
pipeline's output is a formal systematic review (WP1.1).

## Pipeline structure

### stage

A discrete step in the pipeline. Each stage reads one or more
artifacts and writes one or more artifacts. Stages are either *automated*
(run by a tool) or *manual* (performed by a human).

### artifact

A file produced by a stage. The pipeline's data flow is defined
by which stages produce and consume which artifacts.

### protocol

The pre-registered plan for the entire review: search strategy,
eligibility criteria, data-extraction plan, and synthesis approach. In
systematic-review methodology this is a required document (PRISMA-P).

## Corpus and records

### work

A single publication record, modelled after the OpenAlex `Work`
schema. Contains metadata (title, authors, date, DOI, topics,
referenced_works) and an abstract. Does *not* include full text until the
retrieval stage.

### corpus

The collection of works under consideration, including whatever
text content we have for them. In NLP and corpus linguistics, a corpus is a
collection of *texts*, not just metadata — so our corpus includes abstracts
and, where available, full text. The corpus is enriched as it moves through
the pipeline. Qualifiers indicate the pipeline phase: *retrieved corpus*
(everything fetched), *screened corpus* (after automated + human screening),
*included corpus* (after eligibility, ready for data extraction).

### full text

The complete text of a paper, retrieved after screening. Not
all works will have retrievable full text; those that don't are
abstract-only works. Full text is part of the corpus — it enriches the
work record rather than living in a separate artifact.

## Search

### search strategy

The complete specification of a database query: search
terms, Boolean operators, field tags, date limits. PRISMA-S requires this
to be reported in full.

### deduplication

Removing duplicate records retrieved from the same or
multiple sources.

## Screening and adjudication

### screening

Assessing works for relevance against the review scope. In
our pipeline, title-abstract screening is performed by an LLM. The
systematic-review community calls this *title-and-abstract screening*; the
NLP community calls the underlying task *text classification*. Either is
acceptable; "screening" is the stage name.

### verdict

The output of automated screening for a single work:
a relevance score (0–100), an accept/reject decision, and optionally a
reason string.

### relevance score

A numeric score (0–100) assigned by the LLM screener,
expressing how well a work's abstract matches the review scope.

### threshold

The relevance-score cutoff that separates accepted from
rejected works. Works near the threshold are **borderline**.

### adjudication

Resolving disagreements or uncertainty in decisions
produced by the pipeline. In screening, adjudication means a human reviewer
inspects borderline cases and spot-checks accepted/rejected works. In data
extraction, it means resolving disagreements between the LLM and a human
reviewer to produce a final extraction record. In traditional systematic
reviews, two independent human reviewers screen and a third adjudicates;
in our semi-automated pipeline, the LLM acts as the first reviewer and a
human adjudicates.

### eligibility

The full-text assessment of whether a work meets the
review's inclusion criteria. Distinct from screening (which uses only title
and abstract). In our pipeline this happens after full-text retrieval.

### eligibility criteria

The formal inclusion/exclusion rules that determine
whether a work enters the included corpus. Defined in the protocol.

### included corpus

Works that have passed screening, adjudication, and
eligibility. The authoritative input to data extraction.

## Data extraction

### data extraction

Reading a work's text (full text or abstract) and
producing a structured record of facts relevant to the research questions.
The NLP community calls the underlying technique *information extraction*.

### codebook

The document that defines what to extract and how: field
definitions, allowed values, decision rules, worked examples. Called
*annotation scheme* in NLP, *coding scheme* in social-science content
analysis. "Codebook" is the most portable term across communities.

### extraction record

One structured record per work, produced by data
extraction. Contains the fields defined by the codebook, plus metadata
(work ID, source basis, confidence).

### source basis

Whether an extraction record was produced from the full
text or from the abstract alone. Downstream stages use this to weight
confidence.

### confidence score

A per-field or per-record indicator of how certain
the extraction is. Low-confidence fields are prioritised for human
adjudication.

## Quality assessment

Required for a publishable systematic review.

### critical appraisal

Assessing the methodological quality and risk of
bias of included studies. Cochrane prefers "risk-of-bias assessment" using
structured tools; the broader term is "critical appraisal."

### methodological transparency

The degree to which a paper describes its
methods in enough detail to assess and reproduce them. The term used by the
research-on-research and open-science communities. Mapped to RQ1.1.

## Domain terms

### sub-discipline

One of the three branches of computational Lagrangian
oceanography identified in the project proposal: *water parcels*, *tracers*,
and *objects*. Each work is classified into one or more of these.

### numerical choice

A specific decision about how to set up a Lagrangian
computation: integration scheme, time-step strategy, interpolation method,
etc. The codebook enumerates the numerical choices we track.

### reproducibility

Whether a paper describes its numerical methods (or
provides code) in enough detail that someone else could reproduce the
computation. A specific dimension of **methodological transparency**. Mapped
to RQ1.1. The codebook defines the operational criteria for judging
reproducibility.

### rationale

The reason a paper gives (if any) for a particular numerical
choice. Free text extracted from the source. Mapped to RQ1.3.

### prevalence

How common a given numerical choice is across the corpus,
possibly broken down by sub-discipline or year. Mapped to RQ1.2.

## Synthesis

### quantitative synthesis

Computing summary statistics over the extraction
records: counts, proportions, distributions, breakdowns by sub-discipline.
Answers RQ1.1 and RQ1.2.

### thematic synthesis

Identifying recurring themes in qualitative data
(here: the free-text rationale fields) and organising them into a taxonomy.
Answers RQ1.3. Also called *thematic analysis* in qualitative research.

### rationale taxonomy

The set of thematic categories (e.g. "computational
cost", "accuracy", "code availability", "convention") produced by thematic
synthesis of the rationale field.

### narrative synthesis

The final interpretive step: combining quantitative
and thematic results into a structured narrative that addresses each research
question. Produces draft text for the review manuscript. This is the
systematic-review community's term for structured qualitative summary when
statistical meta-analysis is not appropriate.

### evidence

A claim in the narrative synthesis traced back to one or more
extraction records and ultimately to source works. Synthesis should be
evidence-grounded: every claim references the records that support it. (Not
to be confused with **source basis**, which describes the documentary basis
for an individual extraction.)

## Bibliometric analysis

### co-citation analysis

Clustering papers that are frequently cited
together by later work, revealing intellectual groupings.

### bibliographic coupling

Clustering papers that share overlap in their
reference lists, indicating similarity of intellectual basis.

### citation graph

The network of references between works in the corpus,
built from the `referenced_works` field.

### centrality

A measure of a work's importance within the citation graph
(e.g. in-degree = how many corpus works cite it). High-centrality works are
candidate foundational methodological papers.
