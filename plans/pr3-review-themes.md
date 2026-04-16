# PR #3 review — themes and generalizations

Thematic analysis of the 23 inline comments from nvogtvincent and 17 replies
from willirath on PR #3 ("Planning the pipeline"). Derived 2026-04-16. See
[pr3-review-comments.md](pr3-review-comments.md) for the verbatim comments.

---

## Theme 1: LLM trust is a spectrum — validate via human-in-the-loop inspection cycles

**Comments:** glossary:78, glossary:176, pipeline:130

The replies reveal a clear spectrum: LLM screening of abstracts (binary, high
volume) is acceptable with spot-checking. LLM adjudication by less powerful local
models is explicitly rejected — plan for human adjudication instead. Qualitative
analysis expertise is absent on the team and can be deferred (not urgent at the
plumbing stage).

**The pattern:** The more judgment-heavy and nuanced the task, the less
appropriate an LLM is. The plans describe *what* each LLM does but not *how it is
validated*.

**Resolution:** The validation mechanism is a simple inspect-and-cycle loop:
export a random sample of N intermediate results (e.g. to Excel, selectable via
CLI flags), have a human review them, then feed corrections back. No GUI or
detailed CLI needed. This pattern applies to every LLM-driven stage (screening,
extraction, tagging). Later pipeline stages (adjudication, qualitative synthesis)
are deferred — valid concerns, but not blocking the plumbing work.

---

## Theme 2: "Don't sort but search. Don't classify but tag." — the data model must carry everything

**Comments:** glossary:32, retrieval:29, deduplication:32, eligibility:21

The replies are remarkably consistent: OpenAlex is a placeholder (swap to WoS
later). Preprints are kept for now (filter later). Works without DOI are accepted
for prototyping (tighten later). The eligibility criterion 3 question is the same
kind of boundary.

**The pattern:** Every inclusion/exclusion boundary should be a queryable
attribute in the data model, not a hard deletion. The data model must carry over
all info needed for later reasoning — not compute scores or make decisions, but
preserve the raw metadata so that the final result can be filtered by any
dimension. The reviewer keeps asking "why this choice?" and the answer is always
"it's provisional" — but the plans present these as fixed decisions.

**Resolution:** The design principle is: annotate and retain, never discard.
Filtering is trivial if the data is there. Concretely, the data model needs
fields for: source database, preprint vs. accepted, DOI presence, journal
metadata, text-availability level (metadata-only / abstract / full text). The
data model must also support merging catalogues from different sources (e.g.
OpenAlex + WoS), with deduplication early in the pipeline. Eligibility criteria
should be explicit and debatable parameters — treat eligibility.md as a
placeholder for "human turns knobs once the pipeline runs."

---

## Theme 3: No fabricated numbers — scale is discovered, not estimated

**Comments:** pipeline:60, retrieval:208, retrieval:227

The replies clarify that compute is not the bottleneck (HPC is available) — PDF
acquisition is. The existing corpus-size estimates are acknowledged as wrong and
must be dropped.

**The pattern:** Plans must not contain made-up numbers about paper counts, data
volumes, throughput, feasibility, or run times. Estimating scale is the job of
the humans designing the process, not of the plan documents. This should be
enshrined in AGENTS.md: stay fact-based, no fabricated numbers in plans.

**Resolution:** For the plumbing stage, use OA papers only — non-OA acquisition
is an important but separate task to tackle later. Full text is needed for RQ1.2
(prevalence of numerical choices), not just RQ1.1, reinforcing that full-text
retrieval is on the critical path. Scale will be discovered empirically once
search terms are tested against the actual catalogue.

---

## Theme 4: Codebook design is bootstrapped, not specified upfront

**Comments:** codebook:54, codebook:81, codebook:83, codebook:89, codebook:91,
codebook:100, codebook:101, codebook:104, pipeline:95 — 9 of 23 comments, the
largest cluster.

The individual codebook comments (missing fields, redundant fields, wrong
structure, wrong wording) all point to a deeper issue: trying to fully specify
the codebook before running extraction is premature. The codebook design must
allow for bootstrapping — discover what to capture through early extraction runs
and adapt based on what is found.

**Design principles for the codebook:**

1. **Tag, don't classify.** Processing will be concurrent across papers, across
   humans, and possibly across multiple non-deterministic LLM passes over the
   same item. Consolidation of terms, tags, and names is needed anyway, so
   extract tags and context freely and associate them in a later stage.

2. **`{value, context_snippet}` as the default field structure.** Extracting
   reliable structured rationale is too hard, but sweeping context fields with
   specific questions later will be relatively easy. Every field gets an optional
   context companion.

3. **No premature conclusions in framing.** "Common findings" presumes results
   that do not exist — rename to neutral framing ("possible problems" or
   similar). Audit all codebook headings for implied conclusions.

4. **Accept domain-specific granularity where already known.** Code availability
   uses the three-way split (tracking software / setup scripts / analysis
   scripts) with context. Other granularity decisions are deferred to the
   codebook design session.

**Where else this applies:** The "tagging not classification" principle applies to
sub-discipline categorization, method classification, and eligibility itself (tag
why a paper is borderline rather than making a binary include/exclude call). The
codebook must be treated as a living document — the team should ensure everyone
is fine with codebook.md, but accept that it will evolve as extraction produces
data.

---

## Theme 5: Plumbing first — defer what is not urgent

This theme is not a single comment thread but emerges from the replies
collectively. Multiple concerns are acknowledged as valid and important but
explicitly deferred: LLM adjudication design, qualitative analysis methodology,
final eligibility criteria, exact codebook field enumeration.

**The principle:** The current priority is a working pipeline with tunable
parameters, not final decisions on those parameters. The plans should
distinguish: decisions that are fixed (e.g., PRISMA-aligned methodology, human
adjudication over LLM adjudication), decisions that are provisional and will be
revisited (e.g., OpenAlex as source, broad inclusion criteria, OA-only
retrieval), and decisions that require a team session before implementation (e.g.,
final codebook sign-off).

**Concrete actions:**
- Add a note to AGENTS.md: no fabricated numbers in plans.
- Treat eligibility.md as a tunable-parameter placeholder, not a final spec.
- Build the inspect-and-cycle loop (random sample export) into the plumbing.
- Use OA papers for the plumbing stage; defer non-OA acquisition strategy.
- Ensure the codebook is bootstrappable — start extracting, then refine fields.
