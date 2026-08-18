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
   immune to text-extraction corruption. *(Implemented. The measurement below
   shows it wins nothing this corpus does not already get elsewhere — the
   prediction that it was "the single biggest cheap gap" was wrong.)*
2. **Title-page link annotations** — the clickable `doi.org` link in
   `/Annots` → `/A` → `/URI`. Machine-clean, and dodges the line-break and
   ligature corruption that breaks DOIs pulled from extracted text.
   *(Implemented; same result as XMP.)*
3. **First-page text regex** — what the importer already did. *(Kept, and it
   turns out to be the single most productive source: 17 of 26.)*
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

## Measured against real PDFs

The note above was written before any real return existed. It has now been run
against 26 publisher PDFs from a local Zotero library — AGU, Copernicus,
Nature, Elsevier, APS, AMS, AIP, Wiley, IOP, Springer, Cambridge, and arXiv —
copied into a folder under names carrying no information (`download (3).pdf`)
and fed to `fulltext-retrieval-import` with a manifest built from their true
DOIs. 16 of 26 were filed correctly with no filename discipline.

Where the true DOI actually lives, counted over those 26 PDFs:

| Location | PDFs whose true DOI appears there |
|---|---|
| First-page text | 17 |
| Info dictionary | 10 |
| XMP metadata | 9 |
| Title-page link annotation | 6 |
| None of the above | 7 |

Three findings, in the order they matter.

**The first-match rule loses three PDFs that carry their DOI twice.** For the
AGU and Wiley files, `_embedded_doi` returns the first regex hit in the Info
dictionary, which is the journal's ISSN-DOI (`10.1002/(ISSN)1942-2466`), and
never looks at the first-page text where the article DOI sits. The metadata hit
short-circuits a correct text hit. This is the closed-set reframe not being
applied: import knows the manifest, so it should gather every candidate and
accept the one that lands in the manifest, rather than trusting the first
string that looks like a DOI.

**XMP is not the gap the note predicted.** Every PDF whose XMP carries the true
DOI also carries it in the Info dictionary or the first-page text, so XMP wins
nothing on this corpus. The same holds for link annotations. Both remain
correct to read, but neither is the "single biggest cheap gap" this note
claimed before measuring.

**Two AMS DOIs cannot be represented by the DOI pattern at all.**
`10.1175/1520-0485(1997)027<1038:KOTPEU>2.0.CO;2` contains angle brackets,
which the Crossref-recommended character class excludes, so the regex truncates
the DOI even where the text contains it in full.

Seven PDFs carry the DOI in none of the four locations. For those, title
matching is the only route left, and it now has a measured reason to exist
rather than an assumed one.

### After gathering candidates instead of taking the first match

[import-matching-tiers.md](../../plans/done/import-matching-tiers.md) applied
the closed-set rule to extraction: gather every self-identifying DOI and keep
the first one that appears in the manifest. The same 26 PDFs, re-run:

| | Before | After |
|---|---|---|
| Filed with no filename discipline | 16 | 19 |
| Candidates found, none of them correct | 3 | 0 |
| No candidate found anywhere | 7 | 7 |

The three AGU and Wiley files now file correctly. No PDF any longer produces
candidates without the right one among them, so the DOI path is exhausted:
everything still unmatched embeds no DOI in any inspected location, and the
remainder is the title-matching question.

## Why this is deferred, and what splits cleanly

Nothing is broken while we wait: the current importer already matches
embedded-DOI + filename + sidecar and reports the unmatched residual
honestly, so deferring costs nothing.

The two halves defer differently:

- **Data-independent, no-regret:** broadening DOI extraction to XMP and
  title-page link annotations (tiers 1–2). Strictly-more-correct DOI reading
  with no thresholds to tune; it helps any real publisher PDF and can't be
  "wrong." **This half has landed**, together with the candidate-set fix the
  measurement turned up, which mattered more than either new source.
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
