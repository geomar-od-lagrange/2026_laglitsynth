# PR #3 review comments

All inline comments from nvogtvincent on PR #3 ("Planning the pipeline"),
collected 2026-04-16. Replies from willirath included where published.

Items marked [OPEN] have no published reply. Items marked [RESOLVED] have a
reply that settles the point (at least tentatively). Items marked [PARTIAL] have
a reply that reframes or partially addresses the point.

---

## [docs/glossary.md](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/docs/glossary.md)

### [Line 32](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/docs/glossary.md#L32) — OpenAlex vs WoS [RESOLVED]

**nvogtvincent:**
> I'm not familiar with OpenAlex. I'm happy to consider alternatives to a
> traditional database like Web of Science, but I'd like to make sure I
> understand why we are using OpenAlex. The list of journals used by WoS is
> quality controlled and trusted, whereas OpenAlex apparently doesn't screen out
> predatory/low-quality journals. The advantages listed by OpenAlex are its size
> and inclusivity (which, while nice in theory, I worry will in practice make
> things more complex and lower quality), ease-of-use (but WoS/Scopus are both
> easy to use IMO), and free under CC0 (which I accept is good, although the
> list of papers reviewed could be exported from WoS and included as
> supplementary materials). If OpenAlex makes it easier to automate this
> pipeline, that would be a major advantage. Is that the case?

**nvogtvincent (follow-up):**
> OK, I see this last point is addressed later in the document.

**willirath:**
> Yes, let's consider OpenAlex a placeholder for now which works well for the
> pipeline design. I've applied for a Clarivate API key which may allow for
> automated search on WoS. As the OpenAlex data mode looks pretty complete, it's
> easy to swap this out or cross check or whatever later.

**Takeaway:** OpenAlex is a provisional choice for prototyping. The Clarivate API
key application is pending. The data model must document sources and support
merging catalogues from different sources (e.g. OpenAlex + WoS), with
deduplication early in the pipeline. No open decision — revisit once the API key
arrives.

### [Line 78](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/docs/glossary.md#L78) — LLM validation for relevance screening [RESOLVED]

**nvogtvincent:**
> Adding comments as I go along but does this step require training (i.e. how do
> we determine whether the LLM is doing a good job at assigning a relevance)?

**willirath:**
> We review and spot test. First step will be ruling out which LLMs we don't
> trust.
>
> There's many options for local LLMs, we can run decently sized (think last
> year's chatGPT capabilities, for answering specific questions about scientific
> texts or text snippets, these work pretty well) local LLMs on our university
> cluster with huge server GPUs.
>
> While just letting commercial or large local LLM parse all PDFs won't be
> feasible, I'm pretty confident that screening papers and deciding which we
> discard will scale just fine. The existing llmfilter command which is in the
> main branch goes through hundreds of abstracts in a few minutes on my macbook.

**Takeaway:** Approach agreed (spot-test, rule out bad LLMs), but the specifics
are undefined. This raises a general question: how are intermediate results
stored for human inspection? No GUI or detailed CLI — a simple export (e.g.
Excel) of a random sample of N papers (selectable via CLI flags) for human
review, then cycle back. This inspect-and-cycle pattern applies to every
LLM-driven stage.

### [Line 141](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/docs/glossary.md#L141) — Extraction scope bug [RESOLVED]

**nvogtvincent:**
> Why would the extraction step operate on just the abstract? Or does assessing
> relevance also count as extraction?

**willirath:**
> That's an error we need to fix. The extraction will work on the complete paper
> including abstract.

**Takeaway:** Bug in the plan text. Fix the glossary to say extraction operates
on the full paper, not just the abstract.

### [Line 145](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/docs/glossary.md#L145) — Codebook is the key document [RESOLVED]

**nvogtvincent:**
> This is probably the key document that we're all going to have to agree on,
> right?

**willirath:**
> Yes. This is what needs to reflect RQ1.1-3 and WP1.1.

**Takeaway:** Agreement that the codebook is the central artifact requiring full
team sign-off. It must be grounded in RQ1.1-3 and WP1.1. The team should take
effort to ensure everyone is fine with codebook.md before implementation.

### [Line 176](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/docs/glossary.md#L176) — LLM trust for adjudication [RESOLVED]

**nvogtvincent:**
> Do we trust an LLM to do this?

**willirath:**
> No. And I'm not sure we really need this part. It's essential for review-heavy
> fields like medicine, I think, though.

**Takeaway:** Adjudication by less powerful local LLMs is rejected. Plan for
human adjudication. The exact design of the adjudication stage can be deferred —
not urgent at the plumbing stage.

---

## [plans/pipeline.md](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/pipeline.md)

### [Line 60](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/pipeline.md#L60) — Full text needed for RQ1.2 [OPEN]

**nvogtvincent:**
> I think the full text is essential for RQ1.2 (prevalence) as well - it's very
> unlikely that papers will include most of these numerical choices in the
> abstract.

**Takeaway:** Uncontested. The pipeline description should be updated to
acknowledge that full text is needed for RQ1.2 (prevalence of numerical choices),
not only for RQ1.1. This reinforces that full-text retrieval is on the critical
path for the core research questions.

### [Line 95](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/pipeline.md#L95) — Codebook needs proper discussion [PARTIAL]

**nvogtvincent:**
> Yes but bearing in mind that the exact codebook is TBC (these are all sensible
> ideas but needs to be subject to a proper discussion).

**willirath:**
> I think I'd also rephrase this as tagging rather than classification
> (understanding classes as non-overlapping and a priori known). The classes /
> sub-discipline we're interested in are naturally evolving and might even depend
> on what question we're exactly asking. With tags, we can easily cluster tags
> later.

**Takeaway:** Partial agreement. The reframe from classification to tagging is
accepted. But the core request — a proper team discussion before the codebook is
finalized — remains open. Decision needed: schedule a codebook design session.
The tagging-vs-classification reframe should be an input to that session.

FB: See above. Let's make sure everyone's fine with the codebook.md

### [Line 130](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/pipeline.md#L130) — Qualitative analysis expertise [OPEN]

**nvogtvincent:**
> I have zero experience in qualitative analysis - unless you all have experience
> in this area, might be worth chatting to someone who does to see whether this
> sounds like a sensible way of analysing the qualitative data?

**Takeaway:** No one on the team claims qualitative analysis expertise. The
concern is valid and important but not urgent — at this plumbing-focused stage,
later pipeline stages can be deferred. Revisit when the extraction pipeline
produces actual data.

### [Line 250](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/pipeline.md#L250) — General approval

**nvogtvincent:**
> Looks good to me as a whole!

**Takeaway:** Positive signal. The overall pipeline structure is endorsed.

---

## [plans/full-text-retrieval.md](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/full-text-retrieval.md)

### [Line 29](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/full-text-retrieval.md#L29) — Exclude preprints [RESOLVED]

**nvogtvincent:**
> I think we should exclude preprints. We're looking to see whether methodology
> is adequately described and justified, and this is one of the weaknesses of a
> preprint that might be picked up by reviewers and corrected before final
> publication, so I worry that including preprints could introduce a bias towards
> lower quality.

**willirath:**
> As said elsewhere: I'd go for a broad catalogue for now because we need some
> volume for developing the plumbing. But we need to make sure our data model
> makes it easy to filter by, e.g., preprint vs. accepted or by journal quality
> etc.

**Takeaway:** Preprints stay in the catalogue for prototyping and vocabulary
discovery but must be excludable from quantitative RQ analyses. The data model
must carry over all info needed for later assessing trustworthiness — not compute
a score, but preserve the raw metadata (journal, preprint status, etc.) so that
the final result can be filtered by any of these dimensions.

### [Line 208](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/full-text-retrieval.md#L208) — OA coverage and scale [RESOLVED]

**nvogtvincent:**
> I have no evidence for this but purely based on experience, I would have
> expected OA sources to cover <40% (could be wrong ofc). But even if OA sources
> cover a high %, this could still mean 1000s of papers requiring manual
> retrieval, which is probably impractical. I think a priority needs to be
> working out how many papers we can feasibly process later down the pipeline
> based on computational constraints, and should use that to refine search terms
> before retrieval.

**willirath:**
> Parsing thousands of papers is definitely feasible. HPC nodes with server GPUs
> idling through nights and weekends are just one SSH session away. And
> structured extraction (PDF-->XML) for digital age publications is not too slow
> as well. Biggest task will be the actually getting our hands on the PDFs.

**Takeaway:** Compute is not the bottleneck — PDF acquisition is. For the
plumbing stage, use OA papers only. Non-OA acquisition is an important task to
tackle later, widely and exploratively.

### [Line 227](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/full-text-retrieval.md#L227) — Restrictive search terms needed for 1000 papers [RESOLVED]

**nvogtvincent:**
> Getting this down to 1000 papers would require some very restrictive search
> terms.

**willirath:**
> Agreed. These estimates are all off.

**Takeaway:** Drop all fabricated estimates from the plans. Plans must not contain
made-up numbers about paper counts, data volumes, throughput, feasibility, or run
times — that is the job of the humans designing the process. Add a note to
AGENTS.md: stay fact-based, no fabricated numbers in plans.

---

## [plans/full-text-extraction.md](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/full-text-extraction.md)

### [Line 186](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/full-text-extraction.md#L186) — GROBID garbled-text detection [RESOLVED]

**nvogtvincent:**
> Does GROBID flag when extracted text is garbled?

**willirath:**
> As said above, GROBID failures look pretty obvious in my experience. I'll
> investigate if GROBID provides some q.a. flags or scores.

**Takeaway:** GROBID failures are visually obvious (garbled text, broken column
parsing). Action item: investigate whether GROBID exposes quality scores or
flags programmatically. If not, a simple heuristic (sentence length, character
entropy) could serve as an automated quality gate.

---

## [plans/deduplication.md](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/deduplication.md)

### [Line 32](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/deduplication.md#L32) — Drop works without DOI [RESOLVED]

**nvogtvincent:**
> Could we not just remove any works lacking a DOI? All peer-reviewed literature
> will have a DOI and I think limiting the search to peer-reviewed literature
> (thereby potentially discarding a small number of non peer-reviewed reports and
> thesis) is fine.

**willirath:**
> Yes. During prototyping, I think I'm happy with any paper providing content for
> developing the pipeline. Later on and as we have a proper corpus from peer
> reviewed PDFs, we'll become more strict. Most important step now is to have an
> eligibility filter at all with tunable criteria.

**Takeaway:** The Works data model must carry all available info. Filtering to
"only with DOI" or any other criterion is trivial if the data is there. Don't
sort but search. Don't classify but tag.

---

## [plans/eligibility.md](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/eligibility.md)

### [Line 21](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/eligibility.md#L21) — Relax criterion 3 [OPEN]

**nvogtvincent:**
> I would suggest removing criterion 3 (particularly given the point below about
> the initial criteria being broad). No description of the numerical methods
> would be extremely poor practice, but I don't think that disqualifies a paper
> from being relevant.

**Takeaway:** At this plumbing stage, the priority is having a data model for
eligibility with explicit, debatable criteria that can be adapted later.
Eligibility.md needs a thorough look to make it less specific — treat it as a
reasonable placeholder for "here, the human turns knobs once the pipeline runs."
The specific question of criterion 3 is deferred to that review.
---

## [plans/codebook.md](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/codebook.md)

### [Line 54](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/codebook.md#L54) — Wording: "common findings" -> "possible problems" [OPEN]

**nvogtvincent:**
> Change "Common findings" to "possible problems", because presumably we have no
> way of knowing if any of these findings are common yet.

**Takeaway:** Straightforward wording fix. "Common findings" presumes results
that do not yet exist. Rename to "possible problems" or similar neutral framing.
Apply the same audit to other section headings in the codebook — any heading
that implies a conclusion should be reworded.

### [Line 81](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/codebook.md#L81) — Free-text context field for objects [OPEN]

**nvogtvincent:**
> Maybe an additional free text field for more information about the context
> (particularly for `objects`).

**Takeaway:** The `objects` field needs a free-text context annotation. This
should be generalized: every categorical codebook field should have an optional
context/snippet companion field. Defer specifics to the codebook design session.

### [Line 83](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/codebook.md#L83) — Boundary conditions and more thought needed [OPEN]

**nvogtvincent:**
> This is going to require some more thought, e.g. particle tracking boundary
> conditions are important.

**Takeaway:** The numerical-choices section of the codebook is incomplete (e.g.
boundary conditions not captured). Rather than trying to enumerate all fields
upfront, the codebook design must allow for bootstrapping — discover what to
capture through early extraction runs and adapt based on what is found.

### [Line 89](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/codebook.md#L89) — Remove time_step_value [PARTIAL]

**nvogtvincent:**
> I would remove `time_step_value`. It's too context-dependent to be useful. If
> we keep it then we'd also need to gather the ocean model resolution and some
> information about the oceanography to interpret this value.

**willirath:**
> We should consider also extracting short verbatim snippets. Not sure how easy
> it is, but 10k-50k snippets are a lot easier to handle than 10k-50k whole
> papers.

**Takeaway:** Don't fix individual fields — discover what fields make sense
through extraction runs. Always track context for later refinement. The snippet
approach sidesteps the "interpretable in isolation?" problem for all
context-dependent fields.

### [Line 91](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/codebook.md#L91) — Integration scheme inferrable from software [OPEN]

**nvogtvincent:**
> Although can be inferred from the particle tracking software if they only offer
> one option.

**Takeaway:** Some fields are inferrable from others, but the deeper point is
about the extraction philosophy: tag, don't classify. Processing will be
concurrent across papers, across humans, and possibly across multiple
non-deterministic LLM passes over the same item. Consolidation of terms, tags,
and names will be needed anyway. So extract tags and context freely, then
associate and deduplicate tags in a later stage.

### [Line 100](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/codebook.md#L100) — Reporting-quality section redundant? [RESOLVED]

**nvogtvincent:**
> Is this really necessary? Can we not get this information from whether answers
> are provided for the above questions?

**willirath:**
> Yes, and with some context snippet, we could also check this later based on
> the context.

**Takeaway:** Resolved. Reporting-quality as a standalone section is redundant —
it can be inferred from whether other fields have answers. Context snippets make
this verifiable after the fact. Remove the dedicated reporting-quality section;
rely on field completeness + context snippets instead.

### [Line 101](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/codebook.md#L101) — Code-availability needs granularity [OPEN]

**nvogtvincent:**
> Needs to be a bit more specific to differentiate between (i) particle tracking
> software, (ii) scripts used to set up experiments (preprocessing and within
> particle tracking software), and (iii) scripts used for analysis.

**Takeaway:** Use the suggested three-way granularity for now: (i) particle
tracking software, (ii) experiment setup / preprocessing scripts, (iii) analysis
scripts. Extract context alongside each.

### [Line 104](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/codebook.md#L104) — Justification as inline field per choice [RESOLVED]

**nvogtvincent:**
> Could this not just be an additional free text field next to each numerical
> choice (i.e. rather than just having one free text field with the choice,
> having another one with the justification, rather than a separate section)?

**willirath:**
> In a perfect system, having a structure like `time_step: {"value": ...,
> "reason": ...}` etc. would be great. But I doubt we can reliably extract this
> info. So replacing the "method details" and the the "rationale" with some
> context is probably the way to go.

**Takeaway:** Resolved. The default codebook field structure should be
`{value, context_snippet}`. Extracting reliable structured rationale is too hard,
but sweeping context fields with specific questions later will be relatively
easy.

---

## [plans/done/full-text-retrieval-a.md](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/done/full-text-retrieval-a.md) (standalone comments by willirath)

### [Line 36](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/done/full-text-retrieval-a.md#L36) — Preprints: keep but tag [standalone]

**willirath:**
> True. At least for the quantitative RQ's we should not count data from
> preprints. (They otherwise pose a deduplication problem as well.) But for
> gauging the range of methods people use, for building the vocabulary, etc., we
> could still sweep preprints as well. Let's note that our data model needs to
> be able to track peer-reviewed vs. preprint and maybe also a simple classifier
> for journal trustworthiness.

**Takeaway:** Data model requirement: track peer-reviewed vs. preprint status and
journal trustworthiness. Preprints excluded from quantitative RQ counts but
retained for vocabulary and method discovery.

### [Line 130](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/done/full-text-retrieval-a.md#L130) — Abstracts useful for vocabulary even without full text [standalone]

**willirath:**
> For quantitiative RQs I agree we need a homogeneous dataset (ie only count
> those which are available full text). But for discovering methods, vocabulary
> etc, standalone abstracts may help.

**Takeaway:** For quantitative RQs, only full-text papers count. But
abstract-only records are still useful for building vocabulary, discovering
methods, and refining search terms. The data model should track text-availability
level (metadata-only / abstract / full text) so downstream stages can filter
appropriately.

### [Line 316](https://github.com/geomar-od-lagrange/2026_laglitsynth/blob/d15dd94/plans/done/full-text-retrieval-a.md#L316) — GROBID extraction quality [standalone]

**willirath:**
> Yes, we should flag extraction quality.
>
> My experience with grobid is that it either clearly works (ie full sentences
> in paragraphs, clear headlines) or that it clearly breaks (think just reading
> over the column gap in an old OCR'd two-column paper or worse).

**Takeaway:** GROBID extraction quality is binary in practice (clearly works or
clearly breaks). An automated quality gate based on simple heuristics should
suffice. See also extraction:186 above for the action item on GROBID QA flags.
