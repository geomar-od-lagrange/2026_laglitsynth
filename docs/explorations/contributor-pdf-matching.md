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
   "no sidecar even for DOI-less works." *(Implemented, with whole-candidate
   `fuzz.ratio` rather than token-sort, and the threshold chosen against
   the PDF store; see the measurement below.)*

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

## Title matching, measured against the PDF store

Measured 2026-11-27 with pypdf 6 and rapidfuzz 3.14, against the PDF store
under `data/pdfs/` (5936 PDFs) and the deduplicated catalogue (9149 works).
The 26-PDF sample above is too small to tune a threshold on; the store is
the corpus of publisher PDFs whose work is known.

### Ground truth

A store PDF's file name says which work it was fetched for, but that is not
always the paper inside it. The importer's DOI tiers, run over every store
PDF, resolve 3992 to their own work, 135 to a different catalogue work, and
1774 to nothing (35 fail to parse). The 3992 *DOI-confirmed* PDFs are the
tuning set: the PDF names itself, so a title match that disagrees is a
matcher error. The 1774 *DOI-unmatched* PDFs are what the title tier is for,
but their labels are only the store's. The rule was chosen on the tuning
set, so its zero wrong is an in-sample figure; the DOI-unmatched result,
which played no part in choosing score and margin, is the closer to
out-of-sample number.

Each rule below runs with the DOI hidden: it sees only the metadata titles
and the page-1 text. *Wrong* means filed under a work other than the
label.

### Whole-page matching fails on quoted titles

Searching the whole page-1 text for catalogue titles with
`fuzz.partial_ratio`, a score threshold, a margin over the runner-up, and a
minimum title length produced wrong matches on the DOI-confirmed set at
every setting tried: 90 to 100 for the score, 0 to 25 for the margin, 15 to
40 characters for the length. The fewest was one wrong, while accepting
fewer than a quarter of the set. (Exact containment of the title key in
the page key, tried first on the whole store, fared no better.) The wrong
matches are pages that quote another work's title: a referee report on a
discussion paper, a book review, an agency name in an affiliation that is
also the title of a catalogued item.

### Title-block windows plus metadata titles

Comparing whole candidates with `fuzz.ratio` instead keeps the title from
being found inside a longer quote. Candidates are the Info `/Title`, the
XMP `dc:title`, and every run of one to eight consecutive lines among the
first 40 non-blank lines of page 1; each catalogue title keeps its best
score over all candidates. A match is accepted when the best title scores
at least *T*, beats the runner-up by at least *M*, has a key of at least
*L* characters, and belongs to one work.

| Evidence | Wrong on DOI-confirmed, best setting |
|---|---|
| Metadata titles only | 0 (for *T* ≥ 90, *M* ≥ 5), but only metadata-titled PDFs are reached |
| Page-1 windows only | at least 1 at every setting tried |
| Both | 0 for *T* ≥ 95, every *M* in 5–15 and *L* in 15–30; 1 at *T* = 90 |

With both sources, the setting *T* = 95, *M* = 10, *L* = 30 sits inside the
zero-wrong region in every direction measured except toward lower *T*.
*L* = 30 does not change the DOI-confirmed result; on the DOI-unmatched set
it stops two short generic titles (a 26-character "Geophysical fluid
dynamics", a 23-character book title met on an erratum page) from filing
anything. The importer ships this setting.

| At *T* = 95, *M* = 10, *L* = 30 | DOI-confirmed (3992) | DOI-unmatched (1774) |
|---|---|---|
| Filed under the labelled work | 3581 | 1223 |
| Filed under another work | 0 | 32 |
| Refused as a near-tie or shared title | 210 | 121 |
| Best title below 95, or shorter than 30 characters | 201 | 398 |

These are the counts of the shipped
[title_match.py](../../src/laglitsynth/fulltext_retrieval/title_match.py),
run over the extracted store evidence, before the metadata-DOI gate
described below, which takes 20 of the 1223 off.

The 32 on the DOI-unmatched side were read one by one; 30 are the store's
label, not the matcher, being off. 22 are EGU "Reply on RC1" and
"Comment on …" peer-review records whose stored PDF is the discussed
manuscript, and the tier files it under the manuscript. The others are a
preprint and its article under different titles, the same thesis or paper
catalogued once per language, and catalogue duplicates whose titles differ
by an appended note. The remaining two are 1934 Nature pages that carry
several short letters: the PDF stored for one letter is filed under another
letter on the same page.

The 135 store PDFs whose DOI names another catalogue work are left out of
both sets; whether the store holds the wrong PDF for those works is a store
question this measurement does not settle.

The refused count is where the cost is: a title that both a preprint and
its article carry, or that a conference abstract repeats, is refused rather
than guessed, and those PDFs stay unmatched for a contributor to name.

### Gate: no title matching for a PDF that names another DOI

A PDF whose Info dictionary or XMP carries a DOI outside the catalogue is
some other document, even when page 1 prints a catalogue title: an erratum
printing "Erratum" over the corrected paper's title scores 100 against that
paper. The measurement above never saw such a PDF in the tuning set, since
every tuning PDF's own DOI is in the catalogue. So the title tier runs only
when Info and XMP carry no DOI at all.

On the DOI-unmatched set, re-read with the importer's own extraction, 27
PDFs carry an Info or XMP DOI and are gated off. Of the title tier's 1223
filings under the labelled work there, the gate loses 20, mostly arXiv
preprints (13 carry an arXiv `10.48550` DOI) of works the catalogue holds as
journal articles; of the 32 filings under another work it loses none. None
of the 27 carries only a journal-level ISSN DOI, so treating those
separately changes nothing here. Gating on link-annotation DOIs as well
would have cost 32 correct filings from 44 PDFs: those links point at cited
and corrected works as often as at the PDF itself, so they do not gate.

An erratum or reply that carries no DOI in its metadata is not caught; the
tuning set gives no measurement of how often that happens.

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
  [AGENTS.md](../../AGENTS.md) tells us not to invent. **This half has
  landed too** ([import-title-matching.md](../../plans/done/import-title-matching.md)),
  measured against the PDF store rather than against returns; import
  matches against the whole catalogue, so no manifest columns were needed.

## Residual

Even fully built, a residual remains: scanned or OCR-less PDFs and old
metadata-stripped files with no embedded DOI and no extractable title text
cannot be matched no-hands. The honest claim is "contributors can ignore
naming and sidecars for essentially all born-digital publisher PDFs," not
"100% of anything." The right behaviour for the residual is what import
already does — report each by filename, count it in "still missing," and
never guess — so the gap stays operator-visible and a contributor can be
asked for a hint on just those few.
