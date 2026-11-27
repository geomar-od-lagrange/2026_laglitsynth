# Import title matching

## Goal

Let [`fulltext-retrieval-import`](../../src/laglitsynth/fulltext_retrieval/import_.py)
file a returned PDF that carries no DOI it can use, by the catalogue title
the PDF carries, with an acceptance rule chosen against the PDF store rather
than invented. The DOI path is exhausted
([contributor-pdf-matching.md](../../docs/explorations/contributor-pdf-matching.md)):
what is still unmatched embeds no DOI in any inspected location.

## Non-goals

No title-block layout analysis (font sizes, GROBID header parsing), no
author or year cross-check, no keyword test for errata or replies, and no
title evidence from beyond page 1. Each would need its own measurement.

## Target state

A new module
[`title_match.py`](../../src/laglitsynth/fulltext_retrieval/title_match.py)
holds a `TitleIndex` built once per import from the catalogue's titles.
Titles and PDF text are compared as keys: inline markup dropped, NFKC,
casefolded, every non-alphanumeric character removed, so line breaks,
hyphenation and dropped spaces do not matter. Candidates are the PDF's Info
`/Title` and XMP `dc:title`, plus every run of one to eight consecutive
lines among the first 40 non-blank lines of page 1. Each candidate is scored
against every catalogue key with `rapidfuzz`'s `fuzz.ratio` (a new
dependency), and each title keeps its best score. A title is accepted only
when it scores at least 95, beats the next-best title by at least 10, has a
key of at least 30 characters, and belongs to exactly one work. A near-tie
or a title shared by two works is reported with the contenders and skipped;
nothing is guessed. A title shorter than 30 characters is never accepted
but still counts as the next-best, which costs recall, never a wrong filing.

Import reads each PDF once into a `_PdfEvidence` (DOI candidates split into
Info/XMP, link annotations and page text; metadata titles; page-1 text) and
tries the title tier last: after the DOI tiers, the `<stem>.pdf` filename
and the sidecar, so any explicit identifier wins. The tier runs only when
the Info dictionary and XMP carry no DOI at all: a DOI there that is not in
the catalogue says the PDF is another document (an erratum, a reply, a
review) even when its page 1 prints a catalogue title. Link-annotation and
page-text DOIs do not gate, because they also point at cited or corrected
works. `CatalogueEntry` gains `title`, and Info values are read by indexing
so that indirect objects resolve.

The rule comes from the store measurement recorded in the exploration: hide
the DOI of every store PDF whose own DOI confirms its work, and keep the
region of (score, margin, key length) that files none of them wrong. That
zero is on the tuning set; the DOI-unmatched store PDFs are the closer to
out-of-sample result. Matching the whole page text by containment or
`partial_ratio` was measured and rejected, because a page that quotes
another work's title (a referee report, a book review, an affiliation)
files under that work at every margin tried.

## Implementation sequence

1. This plan, and the measurement added to
   [contributor-pdf-matching.md](../../docs/explorations/contributor-pdf-matching.md).
2. `title_match.py`, the `rapidfuzz` dependency and the importer wiring.
   Tests in [test_title_match.py](../../tests/test_title_match.py): keys
   ignore case, spacing, punctuation, ligatures, inline markup and
   end-of-line hyphenation, and keep comparison signs; a title over three
   lines is one candidate; lines below the top 40 are not read; exact and
   slightly misspelt titles are accepted; a distant title and a related
   title differing in a few words are not; an untitled work is not indexed;
   a shared title names both works; score 95 is accepted and 94.9 is not,
   margin 10 is accepted and 9.8 is not, a key of 30 is accepted and 29 is
   not, a title over eight lines is matched and over nine is not. In
   [test_fulltext_retrieval_import.py](../../tests/test_fulltext_retrieval_import.py):
   a DOI-less PDF files by a title broken over page-1 lines, by an Info
   title alone and by an XMP title alone; a DOI and a `<stem>.pdf` name each
   beat another work's title; two works with one title and two near-same
   titles are refused; a short title met in an affiliation is not accepted;
   an erratum with its own Info DOI, or a PDF with an XMP DOI outside the
   catalogue, is not filed by title, while link and page-text DOIs outside
   the catalogue do not block it; indirect Info DOIs and titles resolve.
3. Docs ([fulltext-retrieval.md](../../docs/fulltext-retrieval.md),
   [pdf-store.md](../../docs/pdf-store.md)) and the roadmap; this plan moves
   to `plans/done/`.

## Follow-ups

Records whose title is not the paper's (EGU "Reply on RC1" and "Comment on"
peer-review works) cannot be filed by title; they stay unmatched until a
contributor names them. The store holds such PDFs under those records,
which the measurement surfaced; cleaning that up is a store question, not
an import one. An erratum or reply with no DOI in its metadata that prints
the corrected work's title is still filed under that work; a keyword test
would need its own measurement.

## Critical files

- [src/laglitsynth/fulltext_retrieval/import_.py](../../src/laglitsynth/fulltext_retrieval/import_.py)
- [tests/test_fulltext_retrieval_import.py](../../tests/test_fulltext_retrieval_import.py)
- [docs/explorations/contributor-pdf-matching.md](../../docs/explorations/contributor-pdf-matching.md)
- [docs/fulltext-retrieval.md](../../docs/fulltext-retrieval.md)
