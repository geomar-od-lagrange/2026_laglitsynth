# Pipeline restructure: acquire, then analyse

Mission statement. It records the direction agreed for restructuring the
pipeline; a full implementation plan follows from it.

## Goal

Make the pipeline smaller and split it into two halves that meet at one
on-disk artifact, the **corpus**.

The first half finds the literature and acquires the PDFs. Acquisition has
been the hardest part so far, and it runs on several laptops and VMs
operated by collaborators with different institutional access.

The corpus is the durable product. It has value independent of any
analysis built on it, and the analysis half reads only the corpus. The
analysis pipeline may change; the corpus stays the base it builds on.

This is a literature overview in the natural sciences, not a medical or
humanities systematic review. Conventions borrowed from stricter traditions
(adjudication, consensus runs, dual screening, reviewer workbooks) stay out
unless they earn their keep here.

## Principle: one branch per paper in analysis

Once the corpus is assembled, each paper moves through analysis
independently. No analysis stage looks across papers, and no per-paper
record is rewritten from cross-paper results. Synthesis, at the end, is the
only aggregation. Cross-paper work (dedup, import matching, vocabulary term
frequencies) happens before the corpus.

## First half: identify and acquire

1. **catalogue-fetch** and **catalogue-dedup**: unchanged.
2. **fulltext-retrieval**: open-access retrieval over the whole
   deduplicated catalogue, trying every OpenAlex location of a work plus
   Semantic Scholar and arXiv. Screening never gates it.
3. **screening-abstracts**: runs independently of retrieval, before, after
   or in parallel. It annotates works.
4. **Export/import loop**: the export lists missing PDFs for collaborators
   and may be filtered by screening score, since collaborator time is the
   cost worth saving. Collaborators fetch paywalled PDFs by a documented
   procedure on their own network, in this order: Zotero desktop
   retrieval on an RIS import of the export, then the Zotero browser
   connector on doi.org tabs, then clicking PDF links by hand. See
   [pdf-store.md](../docs/pdf-store.md).
   The export therefore includes an RIS file carrying the work id. There
   is no paid-access fetcher in the CLI, and publisher scraping stays out
   of scope. Every PDF obtained off the central machine comes back through the
   one central import. Import matches files against the whole deduplicated
   catalogue, not only the last export's manifest, so any PDF for a known
   work lands, including a bare folder sent without a prior export. Stores
   and corpora are never merged.
5. **corpus-assemble**: a new step and the only place the screening
   threshold is applied. Works without a score (no abstract, LLM timeout,
   parse failure) are included, because stage 7 still judges them on full
   text. It writes `data/corpus/` and replaces the screening joins that
   retrieval and eligibility each do today.

## The corpus

`data/corpus/` is self-contained. It holds the selected works, their
screening verdicts, **copies** of their PDFs, per-work PDF provenance
(works without a PDF included) and a sidecar recording how the corpus was
made. Disk space is not the constraint; independence from the store is.
Re-running `corpus-assemble` rewrites the corpus completely. Analysis
outputs (TEI, verdicts, codebook records) live outside it in their stage
directories, and their metas record which corpus they ran on. Re-assembling
therefore never destroys analysis work, and stale outputs are detectable.

## Second half: analyse

1. **fulltext-extraction** (GROBID): runs on the corpus PDFs only, no
   longer the whole store. GROBID belongs here because the PDF is the
   scarce artifact and its text can always be re-extracted, so acquisition
   machines need no Docker.
2. **fulltext-eligibility**: stays a separate, cheap full-text pass in
   front of extraction. It moves to a decisions model (a yes/no probability
   per work, no reason string). It drops the "describes numerical methods"
   criterion: papers without a methods description stay in and count as
   non-reproducible for RQ1.1. Its two remaining criteria overlap abstract
   screening, so its rejection rate on the first run decides whether it
   keeps paying for itself.
3. **extraction-codebook**: runs on works with a positive eligibility
   verdict. It already joins verdicts at read time; what is new is
   refusing verdicts from a different corpus or eligibility criteria. It
   gains a free-text `rationale` field with verbatim context for RQ1.3.
   This reverses [codebook.md](../docs/codebook.md), where context snippets
   replaced a rationale section.
4. **synthesis**: one step, replacing the separate quantitative, thematic
   and narrative stages 10–12. Whether the RQ1.3 rationale grouping keeps
   human labelling is decided when synthesis is designed.

## LLM backend

Local LLMs go entirely. Every LLM stage talks to one OpenAI-compatible API,
with `base_url`, key and model taken from the review config; the docs name
no default provider. Screening (3) and eligibility (7) use the decisions
endpoint (`/v1/decisions`); codebook extraction (8) uses chat completions.
Sampling parameters are left at the provider's defaults: no explicit
temperature, no per-call seed. With Ollama go `bake-model`, `num_ctx`, the
model-pulled preflight, the `CHAR_BUDGET` sized to `num_ctx`, and the
Ollama parts of the NESH job; GROBID on NESH stays. The Ollama explorations
stay as history.

## What else goes

Stage 9 (extraction adjudication) is dropped, along with the plan for
multi-run consensus and the codebook export's `reviewer_correction`
column. Abstract lookup (2b) is dropped because nothing calls it or reads
its output. The screening and eligibility XLSX exports are dropped; the
screening threshold is checked ad hoc on `verdicts.jsonl`. `--pass-all`,
the `unscreened` sentinel and the retrieval threshold go once retrieval no
longer needs verdicts. Both feedback loops and the citation-network and
author-review extensions are removed from [pipeline.md](../docs/pipeline.md):
refining the search, criteria, codebook or vocabulary is ordinary editing
of the configs followed by a re-run.

[vocabulary.md](../docs/vocabulary.md) (keyword screening, not yet
implemented) stays, loses its dependence on adjudication, and is refined
once the first codebook results exist. [glossary.md](../docs/glossary.md)
stays the single terminology reference. It redefines *corpus* (now
assembled before eligibility and including works without a PDF) and drops
the PRISMA terms the pipeline no longer uses. The
[roadmap](roadmap.md) gets an entry for this plan, and its items that this
plan contradicts are updated.

## Left to the implementation plan

- Request format and client for the decisions endpoint across providers
  (the current code targets OpenRouter's alpha path).
- How the corpus identity is computed and checked.
- Run-id handling now that the halves no longer share a run.
- The commit sequence and tests.
