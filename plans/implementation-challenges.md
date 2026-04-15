# Implementation challenges

Per-stage analysis of implementation challenges, ideas, and relative
difficulty. Stages 1 (search) and 3 (screen-abstracts) already exist and are
not covered here.

## Tier 1 — Hard problems

### Stage 5: full-text-retrieval

The single hardest stage. The challenges are fundamentally external, not
algorithmic.

#### Challenges

##### Access heterogeneity

Full text lives behind a patchwork: open-access PDFs (Unpaywall, publisher
OA), institutional proxies (Shibboleth/SAML), preprint servers (arXiv,
ESSOAr), and sometimes nowhere. There is no single API.

##### PDF-to-text extraction

Scientific PDFs are hostile to text extraction — two-column layouts,
equations inline with text, figure captions interleaved with body text,
supplementary materials. Tools like GROBID or PyMuPDF each fail differently.
Key decision: extract structured sections (intro/methods/results) or raw
text? Structured is far more useful for stage 7 but far harder.

##### Legal/licensing constraints

Bulk downloading from publishers is a grey area. Some publishers block
programmatic access. Institutional access requires credential handling that
is hard to automate portably.

##### Partial retrieval is the norm

A significant fraction of the corpus will be abstract-only. The
`source_basis` flag is already in the design, but every downstream stage
must handle the two-track reality gracefully.

#### Ideas

- Start with Unpaywall (free API, returns OA PDF URLs for DOIs). This alone
  may cover 40–60% of the corpus for a field like oceanography.
- Use GROBID for PDF to structured XML (title, abstract, sections,
  references). Gold standard for scientific papers, available as a Docker
  image.
- For institutional access, consider Zotero's translators or a manual
  batch-download step rather than automating Shibboleth.
- Define a retrieval-status enum early: `open_access | institutional |
  preprint | abstract_only | failed`. This drives downstream logic.

### Stage 7: data-extraction

The intellectual core of the pipeline — and the hardest LLM task.

#### Challenges

##### Codebook design is the real problem

Before extraction, the codebook must define what "numerical integration
scheme" means operationally. Is "4th-order Runge-Kutta" one category or
does the order matter? What about adaptive schemes? The taxonomy of
numerical choices in Lagrangian oceanography is not standardised — it must
be built iteratively.

##### Long-context LLM reliability

Full papers are 5k–15k words. The LLM must find the methods section,
identify specific numerical choices, and map them to codebook categories.
This is harder than screening (a binary relevance judgment on a short
abstract).

##### Structured output fidelity

The extraction record has many fields. LLMs are unreliable at consistently
filling structured schemas from unstructured text — they hallucinate values,
conflate fields, or miss information stated implicitly.

##### Abstract-only extraction is qualitatively different

Abstracts rarely mention integration schemes or time-step strategies.
Abstract-only extraction records will be mostly `None` fields, which is
correct but makes the two tracks very different in practice.

#### Ideas

- Build the codebook iteratively: extract from 10–20 papers manually first,
  let the categories emerge, then encode them.
- Two-pass extraction: (1) LLM locates relevant passages (methods section,
  numerical setup), (2) LLM extracts structured fields from those passages
  only. Reduces context and improves accuracy.
- Few-shot prompting with worked examples from the manual extraction round.
- Pydantic model for the extraction record from day one — force LLM output
  through validation. Reject and retry on schema failures.

### Stage 10: thematic-synthesis

#### Challenges

##### Qualitative analysis is inherently subjective

Thematic clustering of free-text rationales requires judgment calls: when is
"we used RK4 because it's standard" the same theme as "we followed the
default in OceanParcels"? Both are "convention" but one names a tool and one
names a scheme.

##### Sparse signal

Many papers will not state a rationale at all. The rationale field will be
`None` for a large fraction of records. Synthesis over sparse data is tricky
— characterising what a minority says and also characterising the silence of
the majority.

##### LLM-assisted but human-owned

The design correctly says humans assign final taxonomy labels. But the
tooling to support this workflow (present clusters, let humans
rename/merge/split) needs a UI or at least a well-designed interactive CLI.

#### Ideas

- Embed rationale texts and cluster with sentence-transformers + HDBSCAN.
  Present clusters to the human with representative quotes.
- Track "no rationale given" as an explicit category — its prevalence is
  itself a finding for RQ1.3.

## Tier 2 — Moderate challenges

### Stage 2: deduplication

Straightforward in concept, tricky in edge cases.

#### Challenges

- DOI matching handles most cases but OpenAlex has records without DOIs.
- Fuzzy title matching needed for preprints vs. published versions (slightly
  different titles).
- The same work can appear under different OpenAlex IDs (merged/split
  entities).

#### Ideas

- Match on DOI first (exact), then fall back to normalised-title +
  first-author + year.
- Flag fuzzy matches for human review rather than auto-deduplicating.

### Stage 4: adjudication (screening)

The challenge is workflow design, not algorithms.

#### Challenges

##### Stratified sampling

Must sample across the relevance-score distribution, not just borderline
cases. A uniform random sample of accepts and rejects catches systematic LLM
biases.

##### Feedback representation

How do adjudication decisions feed back? As overrides on verdict records? As
a separate adjudication-log artifact?

##### Convergence

If adjudication reveals the screening prompt is poor, the pipeline
re-screens — but when to stop?

#### Ideas

- Simple CLI that presents work title + abstract + LLM verdict and asks
  accept/reject/skip.
- Record decisions as a JSONL adjudication log.
- Compute agreement rate (human vs. LLM) as a summary statistic.

### Stage 6: eligibility

#### Challenges

- Full-text eligibility requires reading and understanding entire papers,
  not just abstracts. Harder LLM task than screening.
- Eligibility criteria must be precise enough to be operationalised. "Not
  about computational Lagrangian methods" is clear; "review article vs.
  primary research" is fuzzier.
- May reject very few papers if screening was good, making it feel
  low-value — but methodologically required for PRISMA compliance.

### Stage 8: adjudication (extraction)

#### Challenges

- Inter-rater reliability metrics for LLM vs. human on structured fields.
  Cohen's kappa per field? Simple agreement percentage?
- Correction workflow: human corrects a field, correction logged with the
  original LLM output for traceability.

## Tier 3 — Relatively straightforward

### Stage 9: quantitative-synthesis

Aggregation and counting over structured records. The data model does the
heavy lifting. Main challenge is propagating uncertainty from `source_basis`
— how much less to trust abstract-only extractions. This is a
methodological decision more than a technical one.

### Stage 11: narrative-synthesis

Templated prose generation from statistics and taxonomy. The LLM is good at
this. The challenge is editorial — ensuring every claim is traceable to
specific records (evidence grounding). Needs a citation/reference system
linking narrative claims to extraction records to source works.

## Priority order

| Priority | Stage | Rationale |
|----------|-------|-----------|
| 1 | 5. full-text-retrieval | Unblocks everything from stage 6 onward; longest lead time due to access issues |
| 2 | 7. data-extraction | Requires the codebook, which needs iterative design with domain expertise |
| 3 | 2. deduplication | Quick win; unblocks clean screening runs on merged search results |
| 4 | 4. adjudication | Needed before trusting the screened catalogue enough to proceed |
