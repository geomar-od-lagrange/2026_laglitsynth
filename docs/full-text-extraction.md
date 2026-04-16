# Full-text extraction (stage 6)

Parse retrieved PDFs into structured sections. Stage 5 (retrieval)
produces the input (PDFs on disk); downstream stages (eligibility, data
extraction) consume the output. Separating extraction from retrieval
means:

- Retrieval can run without GROBID (just get the PDFs).
- Extraction can be re-run when parsing logic or tools improve.
- Alternative consumers (e.g. feeding raw PDFs to a long-context LLM) can
  bypass extraction entirely and work with the PDFs directly.

## Approach: GROBID only

Use GROBID for all PDF-to-text conversion. GROBID is the only tool that
reliably recovers section structure (Introduction, Methods, Results, etc.)
from scientific PDFs. Section structure matters: the
[two-pass extraction](two-pass-extraction.md) pattern depends on being able
to identify the methods section for targeted data extraction.

If GROBID fails on a subset of the corpus, widen out this stage to
additional tools at that point — not speculatively. The retrieval status
and extraction status are tracked separately, so adding a fallback later
does not require re-retrieving anything.

### Operational details

From [grobid-lessons.md](grobid-lessons.md):

- Run GROBID as a Docker container (`lfoppiano/grobid:0.8.0`).
- amd64-only image; runs under Rosetta on Apple Silicon (slower but
  functional).
- Startup takes 30–60 seconds. Health check: `GET /api/isalive`.
- Memory footprint: ~2 GB.
- Processing endpoint: `POST /api/processFulltextDocument` with the PDF as
  multipart form data.
- Timeout: 120 seconds per paper.
- `consolidateCitations=0` (default off). We already have references from
  OpenAlex.

### TEI XML parsing

Parse GROBID's TEI XML output into a flat list of sections. Each section
has a title and body text. Handle known failure modes:

- Missing `<head>` elements: use `"Untitled section"` as the title.
- No `<div>` sections at all: treat the entire `<body>` as a single
  section titled `"Body"`.
- Abstract outside `<body>`: skip it — the abstract is already in the
  `Work` record from OpenAlex.
- Figure captions interleaved with body: strip `<figure>` elements before
  extracting paragraph text.
- TEI namespace: all elements are in
  `{http://www.tei-c.org/ns/1.0}` — use this prefix throughout.

Output is plain text organized by section. No XML, no HTML. Downstream
stages consume text.

### Extraction quality gate

GROBID failures are binary in practice: the output is either clearly
usable (full sentences in paragraphs, recognisable headings) or clearly
broken (garbled text from column-gap misreads, OCR artefacts, or
non-standard layouts). An automated quality gate catches the obvious
failures before they flow downstream.

Action: investigate whether GROBID exposes quality scores or flags
programmatically (e.g. confidence on parsed elements). If GROBID does
not provide usable quality signals, implement simple heuristics as a
fallback: median sentence length, character-class entropy, ratio of
non-ASCII characters. Documents that fail the quality gate are flagged
in the extraction record and excluded from downstream LLM stages. This
is flagged for implementation alongside the TEI parser, not deferred.

### Metadata authority

Per grobid-lessons: **OpenAlex remains the metadata authority.** Do not
extract or use title, authors, DOI, or year from GROBID output. GROBID
metadata headers are frequently wrong or incomplete. GROBID is used for
body text only.

## Data model

New models in [`src/laglitsynth/fulltext/models.py`](../src/laglitsynth/fulltext/models.py) (same module as
retrieval models).

### TextSection

```python
class TextSection(_Base):
    title: str
    text: str
```

### ExtractedDocument

One per successfully extracted PDF.

```python
class ExtractedDocument(_Base):
    work_id: str                           # OpenAlex ID
    sections: list[TextSection]
    raw_text: str                          # concatenated sections
    extracted_at: str                      # ISO timestamp
```

`sections` is the structured representation. `raw_text` is the
concatenation (for full-text search, token counting, or stages that do not
need section structure). For works where GROBID fails, no
`ExtractedDocument` is created — the absence is the signal.

### ExtractionMeta

```python
class ExtractionMeta(_Base):
    tool: str = "laglitsynth.fulltext.extract"
    tool_version: str = "alpha"
    grobid_version: str
    extracted_at: str
    total_pdfs: int
    extracted_count: int
    failed_count: int
```

## Storage layout

```
data/fulltext/
  extraction.jsonl        # one ExtractedDocument per successfully parsed PDF
  extraction-meta.json    # ExtractionMeta
  tei/                    # raw GROBID TEI XML output (retained for debugging)
    W1234567890.tei.xml
```

TEI XML files are retained so parsing can be re-run without re-calling
GROBID. They are not committed to git.

The `extraction.jsonl` file is the artifact consumed by downstream stages
(eligibility, data extraction). It sits alongside `retrieval.jsonl` in the
same `data/fulltext/` directory.

## CLI interface

```
laglitsynth extract \
    --pdf-dir data/fulltext/pdfs/ \
    --output-dir data/fulltext/ \
    --grobid-url http://localhost:8070 \
    [--skip-existing]
```

### Arguments

- `--pdf-dir`: directory containing retrieved PDFs.
- `--output-dir`: where to write extraction records, metadata, and TEI
  files.
- `--grobid-url`: GROBID API endpoint. Required — there is no non-GROBID
  path for now.
- `--skip-existing`: do not re-extract PDFs that already have an
  `ExtractedDocument` record.

### Flow

1. List all PDFs in `--pdf-dir`.
2. Load existing `extraction.jsonl` if present (for `--skip-existing`).
3. For each unprocessed PDF:
   a. Submit to GROBID.
   b. Save TEI XML to `tei/`.
   c. Parse TEI into sections.
   d. Write `ExtractedDocument` to `extraction.jsonl`.
4. Write `ExtractionMeta`.
5. Print summary: extracted count, failed count.

### GROBID availability

The CLI checks the GROBID health endpoint before starting. If GROBID is
not running, fail fast with a clear error message and the `docker run`
command to start it.

## What to build now

- GROBID client (POST PDF, receive TEI XML).
- TEI XML parser with the fallback rules above.
- `ExtractedDocument` / `ExtractionMeta` models and JSONL output.
- The `extract` CLI subcommand with `--skip-existing`.

## What to defer

- Alternative extraction tools (PyMuPDF, marker, nougat). Add when GROBID
  failures on the actual corpus justify it.
- HTML-to-text for born-HTML papers.
- LaTeX source parsing for arXiv papers.
- Parallel GROBID processing. Sequential is sufficient for hundreds of
  papers.
- GROBID container lifecycle management. The operator starts GROBID
  manually; the CLI just checks and uses it.

## Risks

### GROBID fails on a significant fraction of PDFs

Some sub-fields produce PDFs that GROBID handles poorly (scanned older
papers, unusual layouts, non-standard fonts).

Mitigation: track the failure rate on the actual corpus and decide based
on observed numbers whether a fallback is needed. If the rate justifies
it, evaluate PyMuPDF or marker as a fallback and add a second extraction
path. The `ExtractedDocument` model does not encode the extraction tool —
widening out is a code change, not a schema change.

### GROBID infrastructure friction

Docker, 2 GB RAM, amd64 emulation, 60-second startup. Acceptable for a
research tool but requires a machine that can run Docker.

Mitigation: document the setup clearly. Provide a `docker compose` file.
The retrieval/extraction split means development and testing of other
pipeline stages can proceed with pre-extracted data — GROBID is only
needed during the extraction step itself.

### TEI output variance

GROBID's TEI output varies across paper types. The parser must be defensive.

Mitigation: the fallback rules above handle the known failure modes. Test
the parser against a sample of 10–20 papers from the actual corpus early
to surface unknown failure modes before processing the full corpus.
