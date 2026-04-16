# GROBID operational lessons

Notes from a prior prototype ([litrag](https://github.com/willirath/litrag))
that used GROBID to ingest ~200 PDFs. These are practical observations, not a
plan for a specific pipeline stage. Relevant whenever full-text extraction
enters scope.

## What GROBID is good at

GROBID parses born-digital scientific PDFs into TEI XML with structured
sections, references, and figure metadata. On a clean journal article it
extracted 27 body sections and 42 structured references. Section titles
(`<head>` elements in TEI) are preserved, which makes downstream chunking and
search meaningfully better than raw text extraction.

## Metadata headers are unreliable

GROBID extracts title, authors, DOI, and year from PDF headers. In practice
these fields were frequently missing or wrong — especially DOI and year. The
litrag prototype switched to BibTeX (from a Zenodo export) as the
authoritative metadata source and used GROBID only for body text and
references.

For this pipeline the implication is: **keep OpenAlex as the metadata
authority.** If GROBID is used, use it for section-structured body text only.
Do not overwrite OpenAlex metadata with GROBID-extracted metadata.

## TEI output varies by paper type

Conference papers, book chapters, and scanned PDFs produce substantially
different TEI structures. Common failure modes:

- Missing `<head>` elements (sections exist but have no title).
- Abstract in `<abstract>` outside `<body>` rather than as a body section.
- Figure captions interleaved with body paragraphs.
- Two-column layouts cause paragraph-level text reordering.

The TEI parser must fall back gracefully — if no `<div>` sections are found,
treat the full body as a single "Body" section rather than crashing.

## Infrastructure weight

GROBID is a Java application, best run as a Docker container
(`lfoppiano/grobid:0.8.0`). Operational details:

- The Docker image is amd64-only. On Apple Silicon it runs under Rosetta
  emulation — functional but slower.
- Startup takes 30-60 seconds (model loading). The health-check endpoint is
  `GET /api/isalive`.
- Memory footprint is ~2 GB.
- The processing endpoint is `POST /api/processFulltextDocument` with the PDF
  as multipart form data. Timeout should be at least 120 seconds per paper.

## Docker networking gotcha

In litrag, direct host calls to `localhost:8070` were blocked (likely
sandboxing on macOS). The workaround was running a curl container in the
GROBID container's network namespace:

```bash
docker run --rm --network container:litrag-grobid \
  -v "$PWD/pdfs":/pdfs curlimages/curl \
  -s -S -f -o - \
  -F input=@"/pdfs/files/sample.pdf" \
  http://localhost:8070/api/processFulltextDocument
```

This may or may not reproduce on other machines but is worth knowing about
before assuming GROBID is reachable from the host.

## Citation consolidation requires internet

`consolidateCitations=1` calls Crossref and Semantic Scholar APIs to enrich
references with DOIs. This is useful but requires internet during ingest and
slows processing. Default to `consolidateCitations=0`; make it opt-in.

## Practical throughput

No systematic benchmarks, but single-paper processing (upload + parse +
response) took several seconds per PDF on emulated amd64. For a corpus of
hundreds of papers, expect the GROBID step alone to take tens of minutes.
