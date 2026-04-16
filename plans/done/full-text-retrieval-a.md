# Full-text retrieval (proposal A)

Stage 5 of the pipeline: retrieving full paper text for works in the included
catalogue. This is where the catalogue becomes a corpus. The plan is opinionated
--- it picks a concrete approach rather than surveying alternatives.

## Problem

The included catalogue after adjudication contains several hundred works
identified by DOI and OpenAlex metadata. To answer RQ1.1 (Reproducibility) and
RQ1.3 (Rationale), we need the methods sections of these papers, not just
abstracts. But full text lives behind a patchwork of access mechanisms, and PDF
extraction is lossy. The pipeline must handle partial retrieval gracefully ---
some fraction of the corpus will remain abstract-only, and every downstream
stage must cope with that.

## Source cascade

Try sources in this order. Stop at the first success for each work.

### 1. OpenAlex open-access URLs

The `Work` model already carries `open_access.oa_url` and
`primary_location.pdf_url`. Many of these are direct links to publisher or
repository PDFs. Try these first --- they are free, legal, and already in our
data.

### 2. Unpaywall

For works with a DOI but no usable OpenAlex OA link, query the Unpaywall API
(`api.unpaywall.org/v2/{doi}?email=...`). Unpaywall returns the best available
OA location. Rate limit is 100k requests/day, which is far more than we need.

### 3. Preprint servers via DOI redirects

Some DOIs resolve to preprint versions on arXiv, ESSOAr, or EarthArXiv. If
Unpaywall returns nothing, resolve the DOI and check whether the landing page
is a known preprint server. For arXiv specifically, construct the PDF URL
directly from the arXiv ID (`arxiv.org/pdf/{id}.pdf`).

### 4. Manual batch

For works not available through the above, produce a list of DOIs and titles
for manual retrieval. The human operator retrieves PDFs through institutional
access (library proxy, interlibrary loan, direct request to authors) and drops
them into a designated directory. The pipeline picks them up by filename
convention (`{openalex_id}.pdf` or `{doi_slug}.pdf`).

This is deliberately not automated. Shibboleth/SAML authentication is
institutionally specific, fragile to automate, and a maintenance burden out of
proportion to the corpus size (hundreds of papers, not thousands). A
spreadsheet of missing DOIs and a manual afternoon at the library is the
pragmatic choice.

### What we skip

- Sci-Hub and shadow libraries. Legal risk, ethical ambiguity, and unnecessary
  given the OA coverage in oceanography.
- Publisher APIs (Elsevier, Springer, Wiley). Each has its own authentication
  and terms. Not worth the integration cost for a one-off review.
- Zotero translators. Interesting in principle but add a dependency on
  Zotero's infrastructure and translation server for marginal gain.

## PDF to structured text

### GROBID for section-structured extraction

Use GROBID to parse PDFs into TEI XML. GROBID produces structured sections
(introduction, methods, results, etc.), which is far more useful for
downstream extraction than raw text. The [GROBID operational
lessons](../../docs/grobid-lessons.md) from the litrag prototype apply directly.

Key decisions:

- **GROBID for body text only.** Metadata (title, authors, DOI, year) stays
  from OpenAlex. GROBID metadata headers are unreliable --- this was a
  concrete finding from the litrag prototype.
- **Docker deployment.** Run `lfoppiano/grobid:0.8.0` as a container. Accept
  the Rosetta emulation penalty on Apple Silicon --- correctness matters more
  than speed for a corpus of this size.
- **`consolidateCitations=0` by default.** Citation consolidation calls
  external APIs and slows processing. We already have `referenced_works` from
  OpenAlex.
- **Timeout of 120 seconds per paper.** Some papers (large PDFs, complex
  layouts) take a long time. Fail gracefully after the timeout rather than
  blocking the whole batch.

### TEI parsing

Parse GROBID's TEI XML output into a list of sections, each with a heading and
body text. The parser must handle the failure modes documented in
[grobid-lessons.md](../../docs/grobid-lessons.md):

- Missing `<head>` elements: use "Untitled section" or infer from position.
- No `<div>` sections at all: treat the full `<body>` as a single section.
- Abstract outside `<body>`: extract from `<abstract>` element separately.

The output is plain text organized by section --- not XML, not HTML. Downstream
stages (eligibility, data extraction) consume text, not markup.

### Fallback: PyMuPDF raw extraction

If GROBID fails on a PDF (timeout, crash, empty output), fall back to PyMuPDF
(`fitz`) for raw text extraction. This produces unstructured text --- no
section headings, no semantic structure --- but it is better than nothing. The
retrieval record flags this as `extraction_method: pymupdf` so downstream
stages know the text quality is lower.

### Non-PDF formats

Some preprint servers (arXiv) offer LaTeX source or HTML. For the first
implementation, ignore these and retrieve PDFs only. LaTeX source would
produce better text, but the parsing is a separate project. Defer unless PDF
extraction quality proves inadequate.

## Handling unretrievable papers

Some papers will not be retrievable at all. This is expected and must be
handled explicitly, not swept under the rug.

- **Retrieval status** tracks what happened: did we get the full text, or not,
  and why not?
- **Abstract-only works** proceed through the pipeline with `source_basis:
  abstract_only`. Every downstream stage already needs to handle this
  (the field is in the glossary and pipeline design).
- **The retrieval report** summarizes coverage: how many works retrieved via
  each source, how many abstract-only, how many failed entirely. This goes
  into the PRISMA flow diagram.

There is no retry logic beyond the source cascade. If all sources fail, the
work is abstract-only. A human can always add a PDF manually later and re-run
extraction for that work.

## Data model additions

New models in `src/laglitsynth/retrieval/models.py`.

### RetrievalStatus (enum)

```python
class RetrievalStatus(str, Enum):
    retrieved_oa = "retrieved_oa"         # from OpenAlex OA link
    retrieved_unpaywall = "retrieved_unpaywall"  # from Unpaywall
    retrieved_preprint = "retrieved_preprint"    # from preprint server
    retrieved_manual = "retrieved_manual"        # manually supplied PDF
    abstract_only = "abstract_only"              # no full text available
    failed = "failed"                            # retrieval attempted, error
```

### ExtractionMethod (enum)

```python
class ExtractionMethod(str, Enum):
    grobid = "grobid"
    pymupdf = "pymupdf"
```

### TextSection

```python
class TextSection(_Base):
    heading: str | None = None
    text: str
```

### RetrievedWork

```python
class RetrievedWork(_Base):
    work_id: str                                 # OpenAlex ID
    retrieval_status: RetrievalStatus
    extraction_method: ExtractionMethod | None = None
    source_url: str | None = None                # URL the PDF was fetched from
    pdf_path: str | None = None                  # relative path to stored PDF
    sections: list[TextSection] | None = None    # structured text, if available
    full_text: str | None = None                 # concatenated plain text
    retrieved_at: str | None = None              # ISO timestamp
    error: str | None = None                     # error message if failed
```

`sections` and `full_text` are both stored. `sections` is the structured
version (from GROBID); `full_text` is the concatenation (or the raw PyMuPDF
output). Downstream stages that need section structure use `sections`;
stages that just need text use `full_text`. Both are `None` for
abstract-only works.

### RetrievalMeta

```python
class RetrievalMeta(_Base):
    tool: str = "laglitsynth.retrieval.retrieve"
    tool_version: str = "alpha"
    retrieved_at: str
    total_works: int
    retrieved_count: int
    abstract_only_count: int
    failed_count: int
    by_source: dict[str, int]                    # counts per RetrievalStatus value
```

### Storage

- **PDFs** stored in `data/pdfs/{openalex_id}.pdf`. The OpenAlex ID is a URL
  (`https://openalex.org/W...`); use the `W...` suffix as the filename.
- **Retrieval records** stored as JSONL in
  `data/retrieval/{run_id}_retrieved.jsonl`, one `RetrievedWork` per line.
- **Retrieval metadata** in `data/retrieval/{run_id}_meta.json`.

PDFs are stored because re-running GROBID is expensive and because the human
may want to inspect specific papers. They are not checked into git (add
`data/pdfs/` to `.gitignore`).

## CLI interface

Register a `retrieve` subcommand in the unified CLI:

```
laglitsynth retrieve \
    --catalogue data/screening/included.jsonl \
    --output-dir data/retrieval/ \
    --pdf-dir data/pdfs/ \
    --email user@example.com \
    --grobid-url http://localhost:8070 \
    --skip-existing \
    --manual-dir data/pdfs/manual/
```

#### Required arguments

- `--catalogue`: path to the included catalogue JSONL (list of `Work` records).
- `--output-dir`: where to write retrieval records and metadata.
- `--email`: required by Unpaywall API (identifies the requester; no API key needed).

#### Optional arguments

- `--pdf-dir`: where to store/find downloaded PDFs. Default: `data/pdfs/`.
- `--grobid-url`: GROBID API endpoint. Default: `http://localhost:8070`. If
  GROBID is not running, skip structured extraction and use PyMuPDF only.
- `--skip-existing`: do not re-retrieve works that already have a
  `RetrievedWork` record in the output directory. Enables resumability.
- `--manual-dir`: directory to scan for manually supplied PDFs. Matched to
  works by filename (`W{id}.pdf` or DOI slug).

### Resumability

The command writes one JSONL line per work as it processes. If interrupted,
re-running with `--skip-existing` picks up where it left off. This is the
same resumability pattern used by the fetch and filter stages.

### Reporting

After the run, print a summary table to stdout:

```
Retrieval summary:
  Total works:        312
  Retrieved (OA):     148  (47.4%)
  Retrieved (Unpaywall): 41  (13.1%)
  Retrieved (preprint):  23   (7.4%)
  Retrieved (manual):    17   (5.4%)
  Abstract-only:        78  (25.0%)
  Failed:                5   (1.6%)
```

## What to build first

### Phase 1 (MVP)

- Source cascade: OpenAlex OA URLs and Unpaywall only.
- PDF download with retries and polite rate limiting.
- GROBID integration (assume container is running).
- TEI parser for section-structured text.
- PyMuPDF fallback.
- `RetrievedWork` model and JSONL output.
- CLI with `--skip-existing`.
- Summary report.

This alone should cover 50-70% of the corpus given OA rates in oceanography.

### Phase 2

- Preprint-server detection and arXiv PDF construction.
- Manual-PDF ingestion (`--manual-dir`).
- A `retrieve-report` subcommand that reads retrieval JSONL and produces a
  PRISMA-compatible retrieval flow (counts by status, list of abstract-only
  DOIs for manual follow-up).

### Deferred

- LaTeX source parsing (arXiv).
- HTML full-text extraction (some publishers offer this and it is cleaner than
  PDF).
- Publisher API integrations.
- Automated institutional proxy authentication.

## Risks and mitigations

### OA coverage may be lower than expected

If Unpaywall + OpenAlex OA links cover less than 40% of the corpus, the
abstract-only fraction becomes large enough to undermine RQ1.1 and RQ1.3.

Mitigation: run retrieval early on a sample of 50 works to measure actual OA
coverage before committing to the full pipeline. If coverage is poor, invest
time in the manual batch path sooner.

### GROBID extraction quality varies

Two-column layouts, equations, and non-standard formatting cause GROBID to
produce garbled or reordered text. For some papers the extracted text will be
unusable.

Mitigation: the PyMuPDF fallback catches total GROBID failures. For partial
garbling, downstream stages (data extraction) should be tolerant of imperfect
text --- the LLM can usually handle minor extraction artifacts. For critical
papers where extraction is clearly wrong, the human can manually correct or
supply clean text.

### Rate limiting and transient failures

Unpaywall and publisher servers may rate-limit or return transient errors.

Mitigation: exponential backoff with jitter on HTTP requests. Cap at 3 retries
per URL. Failed downloads become `abstract_only`, not pipeline errors.

### GROBID container availability

GROBID is a heavyweight dependency (Docker, 2 GB RAM, amd64 emulation on
Apple Silicon). If the operator cannot run Docker, structured extraction is
unavailable.

Mitigation: the pipeline works without GROBID --- it falls back to PyMuPDF for
all PDFs. The extraction quality is worse (no section structure), but the
pipeline does not break. GROBID is recommended, not required.

### Legal exposure from bulk downloading

Programmatic downloading of publisher PDFs may violate terms of service even
for OA content.

Mitigation: polite rate limiting (1 request per second to any single domain),
respect `robots.txt`, use only OA and preprint sources for automated
retrieval. Paywalled content goes through the manual path where the human
exercises their institutional access rights normally.

### PDF storage size

Several hundred PDFs at ~1-5 MB each means 0.5-1.5 GB of local storage.
Manageable but must stay out of git.

Mitigation: `.gitignore` the PDF directory. Document the storage expectation.
Add a `--no-store-pdf` flag (deferred) for environments where disk is tight
--- process the PDF and discard it, keeping only the extracted text.
