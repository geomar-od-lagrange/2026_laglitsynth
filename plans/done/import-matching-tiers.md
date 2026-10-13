# Import matching tiers

## Goal

Raise the share of collaborator-returned PDFs that
[`fulltext-retrieval-import`](../../src/laglitsynth/fulltext_retrieval/import_.py)
files with no filename discipline, by fixing what a measured run over real
publisher PDFs showed was broken
([contributor-pdf-matching.md](../../docs/explorations/contributor-pdf-matching.md)
has the numbers).

## Non-goals

Title matching stays out. The PDFs it would rescue carry their DOI nowhere
inspectable, and choosing a similarity threshold needs a corpus to tune
against; this plan makes the DOI path correct first so that later decision is
measured against a sound baseline.

## Target state

`_embedded_doi` returned the first DOI-shaped string it found, Info dictionary
first; for AGU and Wiley PDFs that is the journal's ISSN-DOI, so the article
DOI on page 1 was never reached. It becomes `_embedded_dois`, gathering every
candidate from the Info dictionary, XMP, link annotations on the first two
pages, and first-page text; `_resolve_stem` keeps the first metadata candidate
present in the manifest. First-page text is accepted only when it names
exactly one manifest work, since a correction notice or a cited companion
paper puts two on page 1, and import never guesses. `_DOI_RE` gains `<>` so
AMS DOIs such as `10.1175/1520-0485(1997)027<1038:KOTPEU>2.0.CO;2` can be
represented at all.

## Implementation sequence

Landed as one squash. Tests: a journal DOI in metadata does not beat the
article DOI in text; a metadata hit wins when both candidates are in the
manifest; no candidate in the manifest is skipped, not guessed; XMP-only and
annotation-only PDFs resolve, including an annotation on the second page; two
manifest DOIs on page 1 are reported and skipped unless metadata settles it;
an AMS DOI round-trips.

## Follow-ups

Title matching, per the exploration note, tuned against the measured corpus.

## Critical files

- [src/laglitsynth/fulltext_retrieval/import_.py](../../src/laglitsynth/fulltext_retrieval/import_.py)
- [tests/test_fulltext_retrieval_import.py](../../tests/test_fulltext_retrieval_import.py)
- [docs/explorations/contributor-pdf-matching.md](../../docs/explorations/contributor-pdf-matching.md)
