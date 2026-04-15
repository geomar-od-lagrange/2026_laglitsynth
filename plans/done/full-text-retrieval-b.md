# Full-text retrieval (Stage 5)

Retrieves full paper text for works in the included catalogue. This is where
the catalogue becomes a corpus. The stage is hard because the challenges are
external: access is fragmented across providers, PDF extraction quality is
variable, legal constraints limit automation, and partial retrieval is the
norm rather than the exception.

This plan picks a concrete approach rather than surveying options.

## Retrieval strategy

Try sources in this order for each work. Stop at the first success.

### 1. Unpaywall via OpenAlex

OpenAlex already provides `open_access.oa_url` and `primary_location.pdf_url`
on each `Work` record. These come from Unpaywall data. For a field like
oceanography, this covers roughly 40-60% of the corpus. This is the cheapest
source -- no additional API calls, just HTTP GET on a URL we already have.

### 2. Preprint servers

If no OA PDF URL exists, check for preprint versions. OpenAlex records often
include alternate locations. arXiv and ESSOAr provide clean, freely
accessible PDFs. Prefer the preprint over the published version when it is
the only freely available option -- for our purposes (extracting numerical
methods), preprints are adequate.

### 3. Semantic Scholar API

Semantic Scholar provides an open-access PDF URL endpoint
(`GET /paper/{doi}?fields=openAccessPdf`). This sometimes finds PDFs that
Unpaywall misses, particularly for newer preprints. Free, no API key
required (rate-limited to 100 requests per 5 minutes without a key; apply
for a key if the corpus is large enough to matter).

### 4. Manual batch

Everything else falls into the abstract-only bucket. Do not attempt to
automate institutional access (Shibboleth/SAML) or publisher scraping. The
complexity and brittleness are not worth it for a research tool. Instead,
export a list of unretrieved DOIs. The human operator can batch-download
these through their institution's library portal or Zotero and drop PDFs
into a designated directory. The pipeline picks them up on the next run.

This is a deliberate design choice: accept a manual step for the long tail
rather than building fragile publisher-specific scrapers.

## PDF to structured text

### GROBID for section-structured extraction

Use GROBID to convert PDFs to TEI XML. GROBID is the gold standard for
scientific PDF parsing and the only tool that reliably recovers section
structure (Introduction, Methods, Results, etc.). Section structure is
essential for stage 8 (data extraction) -- the two-pass extraction pattern
from [two-pass-extraction.md](two-pass-extraction.md) depends on being able
to identify the methods section.

Operational details (from [grobid-lessons.md](grobid-lessons.md)):

- Run GROBID as a Docker container (`lfoppiano/grobid:0.8.0`).
- Use `processFulltextDocument` endpoint only. Do not use GROBID's metadata
  extraction -- OpenAlex remains the metadata authority.
- Set `consolidateCitations=0` (default off; opt-in if citation enrichment
  is needed later).
- Timeout at least 120 seconds per paper.
- Wait for the `/api/isalive` health check before submitting papers.

### TEI XML to internal representation

Parse GROBID's TEI XML output into a flat list of sections. Each section has
a title (from `<head>` elements) and body text (from `<p>` elements within
`<div>` elements). Handle GROBID's known failure modes:

- Missing `<head>` elements: use "Untitled Section" as the section title.
- No `<div>` sections at all: treat the entire `<body>` as a single "Body"
  section.
- Abstract outside `<body>`: ignore it (we already have the abstract from
  OpenAlex).
- Figure captions interleaved with body text: strip `<figure>` elements
  before extracting paragraph text.

### Fallback: raw text extraction

If GROBID fails on a PDF (malformed PDF, scanned document, GROBID timeout),
fall back to PyMuPDF for raw text extraction. Raw text loses section
structure but is still usable for keyword search and basic extraction. Mark
these records so downstream stages know the text is unstructured.

## Handling unretrieved papers

Papers that cannot be retrieved at all (no OA source, not manually provided)
remain in the corpus as abstract-only records. This is not an error -- it is
the expected state for a significant fraction of the corpus. The design must
make this a first-class path, not an edge case.

Every downstream stage already needs to handle two tracks (full text vs.
abstract only) via the `source_basis` field. The retrieval stage's job is to
set that field correctly and move on.

Export a retrieval report after each run: counts by retrieval status, list of
unretrieved DOIs with reasons. This report is the input for the manual batch
step and for reporting PRISMA flow numbers.

## Data model additions

New models in `src/laglitsynth/fulltext/models.py`.

### RetrievalStatus enum

```python
class RetrievalStatus(str, Enum):
    retrieved_oa = "retrieved_oa"
    retrieved_preprint = "retrieved_preprint"
    retrieved_semantic_scholar = "retrieved_semantic_scholar"
    retrieved_manual = "retrieved_manual"
    abstract_only = "abstract_only"
    extraction_failed = "extraction_failed"
```

The `retrieved_*` variants record which source succeeded. `abstract_only`
means no full text was found. `extraction_failed` means a PDF was found but
GROBID and PyMuPDF both failed to extract usable text.

### TextSection

```python
class TextSection(_Base):
    title: str
    text: str
```

A single section of extracted text. The `title` is the section heading (e.g.
"Methods", "Results") or a synthetic label ("Body", "Untitled Section").

### FullText

```python
class FullText(_Base):
    work_id: str
    retrieval_status: RetrievalStatus
    source_url: str | None = None
    pdf_path: str | None = None
    structured: bool
    sections: list[TextSection]
    raw_text: str | None = None
    retrieved_at: str
```

- `work_id`: OpenAlex ID, foreign key to the `Work` record.
- `source_url`: the URL the PDF was fetched from (for provenance).
- `pdf_path`: relative path to the stored PDF file (if retained).
- `structured`: `True` if GROBID succeeded and sections are meaningful;
  `False` if raw text fallback was used.
- `sections`: the parsed section list. Empty if `abstract_only`.
- `raw_text`: concatenated full text for convenience (full-text search,
  token counting). `None` if `abstract_only`.
- `retrieved_at`: ISO timestamp.

### RetrievalMeta

```python
class RetrievalMeta(_Base):
    tool: str = "laglitsynth.fulltext.retrieve"
    tool_version: str = "alpha"
    retrieved_at: str
    total_works: int
    retrieved_count: int
    abstract_only_count: int
    extraction_failed_count: int
    by_source: dict[str, int]
```

Following the same pattern as `FetchMeta` and `FilterMeta`.

### Storage layout

```
data/
  corpus/
    fulltext.jsonl        # one FullText record per work
    retrieval-meta.json   # RetrievalMeta
    pdfs/                 # raw PDFs, named by OpenAlex ID
      W1234567890.pdf
    manual/               # drop zone for manually downloaded PDFs
```

PDFs are stored for reproducibility and re-extraction. The `manual/`
directory is where users place PDFs downloaded through institutional access.
File names must be the OpenAlex ID (e.g. `W1234567890.pdf`) so the pipeline
can match them to work records. The CLI prints the expected file names when
reporting unretrieved works.

The `fulltext.jsonl` file is the artifact consumed by stage 7 and beyond.
PDFs are retained but not consumed by downstream stages -- all downstream
access goes through the parsed `FullText` records.

## CLI interface

```
laglitsynth retrieve --input data/included/works.jsonl \
                     --output data/corpus/ \
                     [--manual-dir data/corpus/manual/] \
                     [--skip-grobid] \
                     [--grobid-url http://localhost:8070] \
                     [--timeout 120] \
                     [--dry-run]
```

### Arguments

- `--input`: path to the included catalogue JSONL (Work records).
- `--output`: output directory (will contain `fulltext.jsonl`,
  `retrieval-meta.json`, and `pdfs/`).
- `--manual-dir`: directory to scan for manually placed PDFs. Defaults to
  `<output>/manual/`.
- `--skip-grobid`: skip GROBID extraction, use PyMuPDF only. Useful when
  GROBID is not available.
- `--grobid-url`: GROBID service URL. Default `http://localhost:8070`.
- `--timeout`: per-paper timeout in seconds for both download and GROBID.
- `--dry-run`: report what would be retrieved without downloading anything.

### Resumability

The command is idempotent. On re-run, it reads the existing `fulltext.jsonl`,
skips works that already have a `retrieved_*` status, and processes only new
or `abstract_only` works (which may now have a manual PDF). This is critical
because retrieval is slow (network I/O, GROBID processing) and the corpus
is hundreds of papers.

Implementation: load existing `FullText` records into a `dict[str, FullText]`
keyed by `work_id`. Process only works not in the dict or with status
`abstract_only` (to pick up newly added manual PDFs). Write the full updated
JSONL at the end.

### Progress reporting

Print progress to stderr: `[42/350] W1234567890 -- retrieved_oa (GROBID ok)`.
At completion, print the summary counts from `RetrievalMeta`.

## What to build now

### Build first

- The `FullText`, `TextSection`, `RetrievalStatus`, and `RetrievalMeta`
  models.
- PDF download from OA URLs (Unpaywall via OpenAlex fields).
- GROBID TEI XML parsing into `TextSection` list.
- PyMuPDF raw-text fallback.
- Manual PDF pickup from the drop directory.
- The `retrieve` CLI command with resumability.
- Retrieval report (unretrieved DOIs list for manual download).

### Defer

- Semantic Scholar API integration. The Unpaywall + manual path covers the
  critical cases. Semantic Scholar is an optimisation that adds a few
  percentage points of coverage. Add it when the manual download burden
  becomes annoying.
- Citation consolidation (`consolidateCitations=1`). Not needed for the
  pipeline's purposes. GROBID references are not consumed by any current
  stage.
- PDF deduplication or version reconciliation (preprint vs. published). If
  both exist, just use whichever was retrieved first.
- Parallel GROBID processing. Process papers sequentially for the initial
  implementation. GROBID is I/O-bound anyway, and the corpus is hundreds of
  papers, not thousands. If throughput matters, add a thread pool later.
- GROBID container lifecycle management (auto-start/stop). The operator
  starts GROBID manually. Document the `docker run` command in a README
  or CLI help text.

## Risks and mitigations

### OA coverage is lower than expected

If Unpaywall covers fewer than 40% of the included catalogue, the manual
download burden becomes the bottleneck. Mitigation: add Semantic Scholar as
source 3 (deferred above). If that is still not enough, consider contacting
corresponding authors for a batch of key papers, or accepting a larger
abstract-only fraction and noting it as a limitation in the review.

### GROBID fails on many PDFs

Some sub-fields produce PDFs that GROBID handles poorly (scanned older
papers, unusual layouts). Mitigation: the PyMuPDF fallback ensures we always
get raw text. Track the `structured` flag to know what fraction of the
corpus has section structure. If it is too low, consider marker or nougat as
alternative PDF extractors, but do not build this speculatively.

### GROBID infrastructure friction

GROBID requires Docker, takes 30-60 seconds to start, uses 2 GB of memory,
and is amd64-only (runs under Rosetta on Apple Silicon). This is acceptable
for a research tool but will cause friction. Mitigation: the `--skip-grobid`
flag allows the pipeline to run without GROBID at all, using PyMuPDF only.
This is worse quality but zero infrastructure. Good enough for development
and testing.

### Rate limiting on PDF downloads

Some publisher CDNs rate-limit or block programmatic downloads. Mitigation:
add a configurable delay between downloads (default 1 second). Use a
standard `User-Agent` header identifying the tool. On HTTP 429 or 403,
log the failure, mark as `abstract_only`, and include the DOI in the
manual download list.

### Legal concerns with bulk PDF download

Downloading OA PDFs for text mining is legal in most jurisdictions
(EU text-and-data-mining exception, US fair use for research). Downloading
paywalled PDFs programmatically is not. Mitigation: only download from
URLs flagged as OA by OpenAlex or from preprint servers. Institutional
downloads are the operator's responsibility and go through the manual path.

### Corpus grows beyond expectations

If the included catalogue is much larger than expected (say 2000+ papers),
retrieval time and storage become concerns. Mitigation: JSONL and per-file
PDFs scale fine to thousands. GROBID processing time is the bottleneck --
at ~10 seconds per paper, 2000 papers take ~5.5 hours. This is long but
tractable as a batch job. Add a progress bar and make sure resumability
works.
