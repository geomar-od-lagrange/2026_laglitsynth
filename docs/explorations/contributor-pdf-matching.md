# Matching contributor-returned PDFs to works, no naming or sidecar

Design note for a deferred enhancement to
[fulltext-retrieval-import](../../src/laglitsynth/fulltext_retrieval/import_.py),
following the collaboration model in
[fulltext-retrieval-diversified.md](../../plans/done/fulltext-retrieval-diversified.md).

The goal we want to reach: a contributor fetches PDFs through their own
institutional access, dumps them in a folder named however is convenient
(`asdf.pdf` is fine), ships the folder back, and `fulltext-retrieval-import`
files each one under the right work with no filename discipline and no
sidecar. This note records *how* we'd do that and *why we are not building
it yet*. It is a design capture, not an authoritative reference — the part
that needs deciding can only be decided against real returned PDFs.

## The reframe: closed-set matching, not open-world identification

Tools like `pdf2doi`, Zotero's metadata recogniser, or "send the PDF to an
LLM" solve the hard problem: *given an arbitrary PDF, which paper in the
world is this?* That is the expensive, doesn't-scale part.

We do not have that problem. At import time we hold the
**`pdf-manifest.csv`** written by the matching export — a closed list of the
specific works still missing a PDF, each with a DOI (when one exists) and,
once we add it, a title and year. So the only question is *"which of these N
known works is this PDF, or none of them?"* That is far easier and fully
local: no network, no model, microseconds-to-milliseconds per PDF. It scales
to any contribution volume.

## Withdrawn Zotero export question

The [diversified-retrieval plan](../../plans/done/fulltext-retrieval-diversified.md)
left one question open: whether a PDF returned from Zotero carries its DOI in
the export filename, or whether import has to round-trip a CSV mapping
instead. A probe note and a `scripts/probe_zotero.py` helper existed to
answer it against a live Zotero library, and neither was ever run. Both are
removed as of this note.

The closed-set reframe above answers the question without the probe. Import
never trusts a filename to carry meaning: it reads identifiers out of the PDF
itself and accepts a match only when the identifier lands in
`pdf-manifest.csv`. Filename-stem and sidecar matching stay as last-resort
tiers for any source, Zotero included, so what Zotero happens to name its
attachments changes nothing about the design. A contributor may use Zotero,
a browser, or a shared drive; the import path is the same.

Resolve each returned PDF to a manifest stem by harvesting identifiers from
the cleanest source first, accepting a match only when it lands in the
closed manifest set, and never guessing:

1. **XMP metadata** (`prism:doi`, `dc:identifier`). Publisher-stamped, exact,
   immune to text-extraction corruption. The current importer reads only the
   legacy Info dictionary (`reader.metadata`) and misses XMP
   (`reader.xmp_metadata`) — this is the single biggest cheap gap.
2. **Title-page link annotations** — the clickable `doi.org` link in
   `/Annots` → `/A` → `/URI`. Machine-clean, and dodges the line-break and
   ligature corruption that breaks DOIs pulled from extracted text. pypdf
   exposes annotations directly.
3. **First-page text regex** — what the importer already does. Keep as the
   next tier.
4. **Title fuzzy-match against the manifest** — for DOI-less works (the
   WoS-only slice) and for born-digital PDFs that embed no DOI anywhere.
   Extract the first-page title block, fuzzy-match (e.g. `rapidfuzz`
   token-sort) against manifest titles, and accept only a single clear
   winner above a high threshold. This is the tier that delivers
   "no sidecar even for DOI-less works."

Filename-stem and sidecar matching (the importer's current strategies (b)
and (c)) stay as last-resort safety nets, but stop being anything a
contributor has to think about.

## The reference-DOI trap

This corpus is topically clustered, so a paper's bibliography very likely
cites *other works that are also in our manifest*. That makes a broad
full-text DOI scan actively dangerous: work X's PDF can match work Y because
Y's DOI appears in X's reference list, and X gets silently misfiled as Y.

So the rule is **self-identifying DOIs only** — XMP, Info, the title-page
link annotation, and the first-page top region — and **never** DOIs
harvested from the references section or deep body text. "Just regex every
page" is the wrong instinct precisely *because* the corpus is clustered.

## Why this is deferred, and what splits cleanly

Nothing is broken while we wait: the current importer already matches
embedded-DOI + filename + sidecar and reports the unmatched residual
honestly, so deferring costs nothing.

The two halves defer differently:

- **Data-independent, no-regret:** broadening DOI extraction to XMP and
  title-page link annotations (tiers 1–2). Strictly-more-correct DOI reading
  with no thresholds to tune; it helps any real publisher PDF and can't be
  "wrong." If we do anything before contributions arrive, it is this.
- **Data-dependent, defer until real returns exist:** the title fuzzy-match
  fallback (tier 4), its acceptance threshold, and how to pull the title
  block off a first page. Choosing those without real PDFs to measure
  against would mean inventing exactly the unmeasured numbers
  [AGENTS.md](../../AGENTS.md) tells us not to invent. Tier 4 also needs a
  precursor: add `title` and `year` columns to `pdf-manifest.csv` (export
  already has both — it writes titles into `missing.ris`).

## Residual

Even fully built, a residual remains: scanned or OCR-less PDFs and old
metadata-stripped files with no embedded DOI and no extractable title text
cannot be matched no-hands. The honest claim is "contributors can ignore
naming and sidecars for essentially all born-digital publisher PDFs," not
"100% of anything." The right behaviour for the residual is what import
already does — report each by filename, count it in "still missing," and
never guess — so the gap stays operator-visible and a contributor can be
asked for a hint on just those few.
