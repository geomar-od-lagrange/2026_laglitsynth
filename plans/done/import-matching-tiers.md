# Import matching tiers

## Goal

Raise the share of collaborator-returned PDFs that
[`fulltext-retrieval-import`](../../src/laglitsynth/fulltext_retrieval/import_.py)
files without any filename discipline. A measured run over 26 real publisher
PDFs, recorded in
[contributor-pdf-matching.md](../../docs/explorations/contributor-pdf-matching.md),
filed 16. The three failures that measurement explains are addressed here; the
rest need a matching strategy that does not exist yet. Re-measured after
landing: 19 of 26, with no PDF any longer producing DOI candidates that omit
the correct one.

## Non-goals

Title matching stays out. Seven of the 26 PDFs carry their DOI in no
inspectable location, and those are the ones title matching would rescue, but
choosing a similarity measure and an acceptance threshold needs a corpus of
real returns to tune against rather than one library's PDFs. This plan makes
the DOI path as good as it gets first, so a later title-matching decision is
measured against a correct baseline rather than a broken one.

## Target state

### Candidates, not the first match

`_embedded_doi` returns the first DOI-shaped string it finds, checking the Info
dictionary before the first-page text. For AGU and Wiley PDFs the Info
dictionary holds the journal's ISSN-DOI, so the function returns
`10.1002/(ISSN)1942-2466` and never reaches the article DOI printed on page 1.
A wrong metadata hit beats a correct text hit.

It becomes `_embedded_dois`, returning every distinct candidate it finds, in
trust order: Info dictionary, XMP metadata, title-page link annotations, then
first-page text. `_resolve_stem` accepts the first candidate that lands in the
manifest's DOI map, and no candidate otherwise. This is the closed-set
principle the design note argues for, applied where it was not: import holds
the list of works in play, so membership decides, not position.

Trust order still matters, because two candidates can both be in the manifest
when a paper cites another paper in the same review. The reference-DOI trap in
the design note is unchanged: candidates come only from the Info dictionary,
XMP, title-page annotations, and the first page, never from the reference list
or deep body text.

XMP and annotations win nothing on the measured corpus — every PDF carrying the
DOI there also carries it in the Info dictionary or the page text. They are
added because they cost one function each and are strictly more correct, not
because they are expected to move the number.

### A DOI pattern that can represent AMS DOIs

`_DOI_RE` uses the Crossref-recommended class `[-._;()/:a-z0-9]`, which
excludes angle brackets. Two AMS DOIs in the sample are
`10.1175/1520-0485(1997)027<1038:KOTPEU>2.0.CO;2`, so the pattern truncates
them even where the text carries them whole. The class gains `<` and `>`.

Neither AMS PDF carries its DOI anywhere inspectable, so this fixes no case in
the measured sample. It is here because the manifest DOI itself flows through
the same normalisation, and a DOI the pattern cannot represent can never match
however well it is extracted.

## Implementation sequence

Two commits. `pixi run typecheck` and `pixi run test` pass between each.

1. `_DOI_RE` gains `<>`. Tests: an AMS-style DOI with angle brackets survives
   `_normalise_doi` intact and matches a manifest entry carrying the same DOI;
   the existing trailing-punctuation stripping is unaffected.
2. `_embedded_doi` becomes `_embedded_dois`, yielding candidates in trust order
   across the Info dictionary, XMP, first-and-second-page link annotations, and
   first-page text; `_resolve_stem` takes the first candidate present in the
   manifest. Tests: a PDF whose Info dictionary holds a journal DOI absent from
   the manifest and whose page text holds the article DOI present in it
   resolves to the article; a PDF with no candidate in the manifest is still
   reported and skipped rather than guessed; a PDF carrying only an XMP DOI
   resolves; candidate order puts an Info-dictionary hit ahead of a page-text
   hit when both are in the manifest.

## Follow-ups

Title matching for the seven PDFs that embed no DOI, using the `title` and
`year` columns the export now writes. It needs real returns to set a threshold
against, and inventing one would be inventing an unmeasured number.

Re-running the measurement after these two commits gives the new baseline. The
harness is not committed: it reads a personal Zotero library, so it is not
reproducible for anyone else and does not belong in `tests/`.

## Critical files

- [src/laglitsynth/fulltext_retrieval/import_.py](../../src/laglitsynth/fulltext_retrieval/import_.py)
- [tests/test_fulltext_retrieval_import.py](../../tests/test_fulltext_retrieval_import.py)
- [docs/explorations/contributor-pdf-matching.md](../../docs/explorations/contributor-pdf-matching.md)
- [docs/fulltext-retrieval.md](../../docs/fulltext-retrieval.md)
