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
- Body sections placed in `<back><div type="annex">` and body paragraphs
  placed in `<figure><figDesc>` (for example when a running page header is
  taken for a figure label). Seen with both 0.8.0 and 0.9.0. `sections()`
  reads only `<body>` and drops figures, so this text does not reach
  stages 7 and 8.

The TEI parser must not crash on these shapes. When no top-level `<div>`
sections are found, `sections()` returns an empty list (there is no
synthetic single-"Body" fallback); stages 7/8 record that empty result
as a `tei-parse-failure` sentinel.

## Infrastructure weight

GROBID is a Java application, best run as a Docker container. litrag used
`lfoppiano/grobid:0.8.0`; this pipeline uses `grobid/grobid:0.9.0-crf`.
Operational details:

- `lfoppiano/grobid:0.8.0` is amd64-only and runs under Rosetta emulation
  on Apple Silicon. `grobid/grobid:0.9.0-crf` is multi-arch (amd64 and
  arm64) and runs natively there.
- Startup loads the models before the service answers. The health-check
  endpoint is `GET /api/isalive`.
- Memory footprint was about 2 GB in litrag with 0.8.0; larger footprints
  under batch load are recorded in
  [explorations/run-cost-and-throughput.md](explorations/run-cost-and-throughput.md).
- The processing endpoint is `POST /api/processFulltextDocument` with the PDF
  as multipart form data. Timeout should be at least 120 seconds per paper.
- `GET /api/version` answers with plain text in 0.8.0 and with JSON
  (`{"version": …, "revision": …}`) in 0.9.0.

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
before assuming GROBID is reachable from the host. It did not reproduce with
`grobid/grobid:0.9.0-crf` on Docker Desktop 4.99.0 for macOS.

## Citation consolidation requires internet

`consolidateCitations=1` calls Crossref and Semantic Scholar APIs to enrich
references with DOIs. This is useful but requires internet during ingest and
slows processing. Default to `consolidateCitations=0`; make it opt-in.

## Practical throughput

In litrag, single-paper processing (upload + parse + response) with
`lfoppiano/grobid:0.8.0` took several seconds per PDF on emulated amd64.
Measured rates for both images are in
[explorations/run-cost-and-throughput.md](explorations/run-cost-and-throughput.md).
