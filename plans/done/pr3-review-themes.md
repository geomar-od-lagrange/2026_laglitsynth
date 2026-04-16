# PR #3 review — themes and generalizations

Thematic analysis of the 23 review comments on PR #3 ("Planning the
pipeline"). Source material:
[pr3-review-comments.md](pr3-review-comments.md).

---

## Preserve raw data, defer decisions

The single strongest thread across the review is that the data model should
capture everything available and push filtering, classification, and
scoring to later stages.

- **Preprints** should stay in the catalogue for prototyping and vocabulary
  discovery but must be excludable from quantitative RQ analyses.
  (retrieval:29, done/retrieval-a:36)
- **Works without DOIs** should not be dropped at ingest — the eligibility
  stage applies tunable criteria instead. (deduplication:32)
- **Abstract-only records** are useful for vocabulary and method discovery
  even when only full-text papers count for quantitative RQs.
  (done/retrieval-a:130)
- **Journal trustworthiness** and **peer-review status** are metadata to
  carry, not scores to compute up front. (retrieval:29,
  done/retrieval-a:36)
- **OpenAlex vs. WoS** is a source choice that can be swapped or merged
  later, as long as the data model documents provenance and supports
  deduplication. (glossary:32)

The principle: the data model stores everything; downstream stages filter.
No irreversible discards during ingestion. Every decision point in the
pipeline should operate on metadata that was preserved, not on information
that was already thrown away.

**Actions.** Ensure the Works data model carries: DOI (nullable),
peer-review/preprint status, journal metadata, text-availability level
(metadata-only / abstract / full text), and catalogue source provenance.

## Tag, don't classify

Multiple comments converge on the idea that extraction should produce
open-ended tags rather than fixed categories, and that consolidation
happens in a later pass.

- The reframe from "classification" to "tagging" for sub-disciplines.
  (pipeline:95)
- Free-text fields preferred over enums during the pilot — premature
  enumeration is the main codebook risk. (codebook.md design decisions;
  codebook:89)
- Some fields are inferrable from others (e.g. integration scheme from
  software), but the extraction stage should not attempt that inference —
  it should tag what the paper says and leave consolidation to a later
  stage. (codebook:91)
- Processing is concurrent across papers, humans, and non-deterministic
  LLM passes; term consolidation and deduplication of tags is an explicit
  later step. (codebook:91)

The principle: extraction captures what is there, in the vocabulary the
paper uses. Normalization, inference, and categorization are separate
pipeline concerns. This keeps extraction simple and auditable, and avoids
baking in assumptions that later turn out to be wrong.

**Actions.** Keep all codebook fields as free text during the pilot. Plan
an explicit tag-consolidation step between extraction and synthesis.

## Context snippets over structured sub-fields

Several exchanges arrive at the same field-level design: pair each
extracted value with a verbatim context snippet rather than trying to
extract structured rationale, justification, or quality scores.

- The `{value, context_snippet}` pattern replaces separate "method
  details" and "rationale" sections. (codebook:104)
- `time_step_value` is too context-dependent to interpret in isolation; a
  snippet preserves the interpretive context. (codebook:89)
- Reporting-quality as a standalone section is redundant — field
  completeness plus context snippets make it verifiable after the fact.
  (codebook:100)
- A free-text context field should accompany every categorical codebook
  field, not just `objects`. (codebook:81)

The principle: context travels with the value. If an extracted fact
cannot be understood without reading the surrounding paragraph, the
extraction is incomplete. Snippets are cheap to store and make human
adjudication possible without re-reading whole papers.

**Actions.** Adopt `{value, context_snippet}` as the default codebook
field structure. Remove the standalone reporting-quality section. Add a
context companion to every categorical field.

## The codebook is discovered, not designed

The codebook — acknowledged as the central artifact requiring full team
sign-off (glossary:145) — cannot be fully specified before extraction
runs.

- Boundary conditions are missing from the numerical-choices section, and
  the reviewer flags that "more thought" is needed. (codebook:83)
- Rather than trying to enumerate all fields upfront, the codebook must
  allow for bootstrapping: run extraction, see what emerges, adapt.
  (codebook:83, codebook:89)
- The code-availability field needs finer granularity (particle tracking
  software / experiment setup scripts / analysis scripts), which only
  became apparent through domain review. (codebook:101)
- Wording like "common findings" presumes results — rename to neutral
  framing. (codebook:54)

The principle: the seed codebook is a hypothesis about what to extract.
Early extraction runs test that hypothesis. The extract-review-revise
loop (codebook.md phase 3) is the mechanism for convergence. Planning
documents must not presume outcomes.

**Actions.** Schedule a team codebook design session before
implementation (pipeline:95). Use the three-way code-availability
granularity. Audit all codebook headings for language that presumes
results. Accept that the codebook will change after pilot extraction.

## Human review is essential; its mechanics are deferred

The review repeatedly surfaces points about validation and expertise, but
consistently agrees that the specifics can wait until the plumbing
produces actual data.

- LLM screening validation: spot-test and rule out bad LLMs, but the
  inspect-and-cycle pattern applies to every LLM-driven stage.
  (glossary:78)
- Adjudication by local LLMs is rejected; plan for human adjudication.
  (glossary:176)
- No one on the team claims qualitative-analysis expertise — an external
  consultation may be needed, but not at the plumbing stage. (pipeline:130)
- Adjudication design, eligibility criteria specifics, and the exact
  codebook can all be deferred until the pipeline produces reviewable
  output. (glossary:176, eligibility:21, pipeline:130)

The principle: every LLM-driven stage needs human inspection, but the
review format is "export a sample, human reviews, feed corrections back"
rather than a heavyweight GUI or formal inter-rater protocol. Design the
plumbing first; the review workflow crystallizes once there is data to
review.

**Actions.** Each LLM-driven stage should produce output that can be
exported as a flat table (e.g. CSV/Excel) for human spot-checking.
Defer adjudication workflow design. Flag qualitative-analysis expertise
as a gap to address before thematic synthesis (stage 11).

## Plumbing first, then precision

A meta-theme across the review: the current priority is getting the
pipeline to run end to end on real data. Precision in criteria,
thresholds, and field definitions comes later.

- Eligibility criteria should be treated as tunable placeholders, not
  final rules. (eligibility:21)
- Preprints, works without DOIs, and abstract-only records are kept for
  prototyping volume. (retrieval:29, deduplication:32,
  done/retrieval-a:130)
- Compute is not the bottleneck — PDF acquisition is. For now, use OA
  papers only. (retrieval:208)
- The pipeline structure as a whole is endorsed. (pipeline:250)

The principle: the pipeline is infrastructure, and infrastructure must
exist before it can be tuned. The plumbing stage uses permissive
defaults (broad catalogue, all document types, free-text fields) to
maximize the volume of data flowing through. Precision is introduced
when humans can see what the pipeline produces and make informed
decisions about tightening criteria.

**Actions.** Use OA-only PDFs for prototyping. Keep eligibility criteria
broad and documented as provisional. Treat all threshold and filter
values as parameters to be tuned once the pipeline runs.

## No fabricated numbers

A narrow but important point: plan documents contained made-up estimates
for paper counts and feasibility.

- The "1000 papers" estimate was acknowledged as wrong. (retrieval:227)

The principle is already codified in AGENTS.md: plans must not contain
fabricated numbers for paper counts, data volumes, throughput, or run
times. If a number has not been measured, it does not belong in a
planning document.

**Actions.** Audit existing plan documents and remove any remaining
unsourced estimates.

## Full text is on the critical path

Two comments reinforce that full-text retrieval is not optional for the
core research questions.

- Full text is essential for RQ1.2 (prevalence of numerical choices),
  not only RQ1.1. Numerical choices are rarely stated in abstracts.
  (pipeline:60)
- The glossary incorrectly stated that extraction operates on abstracts;
  this is a bug. (glossary:141)

The principle: the pipeline from stage 5 onward is the critical path.
Anything that blocks full-text retrieval blocks the core research
questions. Pipeline stages 1-4 (catalogue stages) are useful for
vocabulary and screening, but the review's quantitative answers depend
on full text.

**Actions.** Fix the glossary to state that extraction operates on full
papers. Update pipeline.md stage 5 to list RQ1.2 alongside RQ1.1 and
RQ1.3 as requiring full text.

## Extraction quality needs automated gates

GROBID extraction quality is discussed in two comments and converges on
a practical approach.

- GROBID failures are binary in practice: clearly works or clearly
  breaks. (done/retrieval-a:316, extraction:186)
- Whether GROBID exposes programmatic quality flags is an open
  investigation. Simple heuristics (sentence length, character entropy)
  could serve as a fallback quality gate. (extraction:186)

The principle: automated quality gates should catch the obvious failures
so that human review time is spent on genuinely ambiguous cases, not on
garbled text. This generalizes beyond GROBID to any stage that produces
text: if the output is clearly broken, flag it before it flows
downstream.

**Actions.** Investigate GROBID quality flags. Implement a simple
heuristic quality gate for extracted text. Consider generalizing the
pattern to other LLM-output stages.
