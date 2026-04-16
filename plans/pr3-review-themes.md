# PR #3 review — themes and generalizations

Thematic analysis of the 23 inline comments from nvogtvincent and 17 replies
from willirath on PR #3 ("Planning the pipeline"). Derived 2026-04-16. See
[pr3-review-comments.md](pr3-review-comments.md) for the verbatim comments.

---

## Theme 1: LLM trust is a spectrum — each stage needs an explicit validation plan

**Comments:** glossary:78, glossary:176, pipeline:130

The replies reveal a clear spectrum: LLM screening of abstracts (binary, high
volume) is considered acceptable with spot-checking. LLM adjudication of
disagreements is explicitly rejected. For qualitative analysis, no one on the
team has the expertise to judge whether an LLM-based approach is sound.

**The pattern:** The more judgment-heavy and nuanced the task, the less
appropriate an LLM is. But the plans do not classify stages along this axis.

**Where else this applies:** Codebook extraction sits in the middle of this
spectrum — structured output but requiring contextual judgment. The two-pass
extraction plan, the eligibility filtering, and any future synthesis steps all
need an explicit answer to: "How do we know this stage did its job?" The plans
currently describe *what* each LLM does but never *how it is validated*. Each
stage should specify: validation method (spot-check, gold-standard set,
inter-rater agreement), failure mode (what does a bad result look like), and
fallback (what happens when the output is not trusted).

---

## Theme 2: "Broad now, filter later" is the design philosophy — the data model must support it

**Comments:** glossary:32, retrieval:29, deduplication:32, eligibility:21

The replies are remarkably consistent: OpenAlex is a placeholder (swap to WoS
later). Preprints are kept for now (filter later). Works without DOI are accepted
for prototyping (tighten later). The eligibility criterion 3 question (still
open) is the same kind of boundary.

**The pattern:** Every inclusion/exclusion boundary should be a queryable tag or
flag in the data model, not a hard deletion. The reviewer keeps asking "why this
choice?" and the answer is always "it's provisional" — but the plans present
these as fixed decisions.

**Where else this applies:** This philosophy needs to be stated explicitly and
then enforced systematically. Every filtering stage (keyword screening,
deduplication, eligibility, relevance) should *annotate and retain* rather than
*discard*. That way the team can tighten criteria later without re-running
upstream stages. Concretely, the data model needs fields for: preprint vs.
accepted, source database, DOI presence, journal quality tier, extraction-text
completeness (metadata-only / abstract / full text). The eligibility criteria
should be versioned so results can be reproduced under different threshold sets.

---

## Theme 3: Scale constraints propagate backwards — need a throughput estimate per stage

**Comments:** pipeline:60, retrieval:208, retrieval:227

The replies clarify that compute is not the bottleneck (HPC is available) — PDF
acquisition is. The existing corpus-size estimates are acknowledged as incorrect.
The open item (pipeline:60, full text needed for RQ1.2) reinforces that full text
is required more broadly than the plans assume.

**The pattern:** The plans do not have a stage-by-stage throughput estimate, so a
reader cannot tell whether the pipeline is feasible end-to-end.

**Where else this applies:** Beyond retrieval, the extraction stage (GROBID +
LLM) and the codebook-filling stage both have throughput implications that are
not estimated. If 5,000 papers enter extraction, how long does GROBID take? How
many LLM calls for codebook extraction? What is the cost? The pipeline plan
should include a back-of-envelope sizing table: expected input count, processing
time, and cost per stage. This also connects to theme 2 — if the broad-now
approach yields 10,000 works initially, the team needs to know which stages
become bottlenecks at that scale.

---

## Theme 4: Codebook fields need a "snippets + context" redesign, not piecemeal fixes

**Comments:** codebook:54, codebook:81, codebook:83, codebook:89, codebook:91,
codebook:100, codebook:101, codebook:104, pipeline:95 — 9 of 23 comments, the
largest cluster.

The replies converge on a key insight: instead of extracting structured values
for context-dependent fields, the pipeline should **extract short verbatim
snippets**. This addresses multiple complaints at once — `time_step_value` is too
context-dependent (extract the snippet instead), reporting-quality is redundant
(inferable from context), justification should be inline (attach context to each
field).

**The pattern:** The individual complaints point to three systemic issues that
apply to *every* codebook field, not just the ones commented on:

1. **"Is this interpretable in isolation?"** — `time_step_value` was called out,
   but the same test should be applied to every numerical/categorical field. Any
   field that requires ocean model resolution, domain context, or other fields to
   interpret is a candidate for snippet-based extraction instead.

2. **"Is this redundant?"** — Reporting-quality was called out, but the codebook
   should be audited for any field derivable from other fields. If
   presence/absence of answers implies reporting quality, what else is implied
   rather than independently informative?

3. **"Is the structure right?"** — The justification-inline-vs-separate question
   and the code-availability-granularity question are both structural. The
   pattern of `{value, context_snippet}` per field (as proposed in the reply on
   codebook:104) should be the default codebook field structure, applied
   uniformly.

**Where else this applies:** The "tagging not classification" reframe (reply on
pipeline:95) is the same idea at a higher level — do not force things into rigid
categories, capture the raw signal and cluster later. This applies to
sub-discipline categorization, to method classification, and arguably to
eligibility itself (tag why a paper is borderline rather than making a binary
include/exclude call). The boundary-conditions gap (codebook:83) and the
code-availability granularity gap (codebook:101) suggest the team should do a
**domain walkthrough** of the codebook: trace a real Lagrangian tracking paper
through the extraction and ask "what would we actually want to record for each
section?"

---

## Theme 5: Prototyping vs. production is undeclared

This theme is not a single comment thread but emerges from the replies
collectively. The replies repeatedly distinguish between what is acceptable "for
now" / "during prototyping" and what the production system needs. But the plans
do not mark which decisions are provisional and which are settled.

**Where this applies:** The plans should have a clear marker (or a separate
section) distinguishing: decisions that are fixed (e.g., PRISMA-aligned
methodology), decisions that are provisional and will be revisited (e.g.,
OpenAlex as source, broad inclusion criteria, corpus size estimates), and
decisions that require a team session before implementation (e.g., final
codebook, qualitative analysis method). Without this, every reader will ask the
same "is this final?" questions that came up in this review.
