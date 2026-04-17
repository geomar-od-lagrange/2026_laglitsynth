# Full-text extraction

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

### TEI XML as the canonical artefact

GROBID's TEI XML output is saved to disk as the canonical extraction
artefact — one `.tei.xml` per PDF. The stored `ExtractedDocument`
record carries only `work_id`, `tei_path` (relative to the output
directory), `content_sha256` of the TEI bytes, and the
`extracted_at` timestamp. Section text, figures, citations, and
bibliography are not persisted in the JSONL; they are parsed lazily
from the TEI on demand (see "Reading TEI" below).

Known TEI shape details the parser handles:

- Missing `<head>` elements on a `<div>`: `Section.title` is `None`.
- Figure captions interleaved with body: stripped before building
  `Section.paragraphs` so captions don't leak into section text.
  Figures remain accessible via `figures()` on the same document.
- Abstract outside `<body>`: ignored — the abstract is already in
  the `Work` record from OpenAlex.
- TEI namespace: all elements are in
  `{http://www.tei-c.org/ns/1.0}`.

Nested `<div>` hierarchy is preserved via `Section.children`, so
sub-sections (Methods → Sub-methods) remain reachable.

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

Models in [`src/laglitsynth/fulltext_extraction/models.py`](../src/laglitsynth/fulltext_extraction/models.py).

### ExtractedDocument

One per successfully extracted PDF.

```python
class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str                 # OpenAlex ID
    tei_path: str                # relative to output_dir, e.g. "tei/W123.tei.xml"
    content_sha256: str          # sha256 hex digest of the TEI bytes on disk
    extracted_at: str            # per-record wall-clock timestamp

    def open_tei(self, output_dir: Path) -> TeiDocument:
        ...
```

`tei_path` is stored as the relative string `"tei/<work_id>.tei.xml"`,
mirroring [`RetrievalRecord.pdf_path`](../src/laglitsynth/fulltext_retrieval/models.py).
Consumers join with their `output_dir` at read time — typically via
`ExtractedDocument.open_tei(output_dir)`, which returns a
`TeiDocument` wrapper over the TEI file on disk.

`content_sha256` is a sha256 of the TEI bytes, computed at extract
time. The hash becomes the stable identity for a given extraction
across re-runs (a newer GROBID version may emit different TEI for the
same `work_id`).

For works where GROBID fails, no `ExtractedDocument` is created — the
absence is the signal.

### Reading TEI

The [`fulltext_extraction/tei.py`](../src/laglitsynth/fulltext_extraction/tei.py)
module exposes lazy typed views over a TEI file. Parsing happens on
the first accessor call and is cached on the instance.

```python
doc = extracted.open_tei(output_dir)
for section in doc.sections():           # list[Section]
    ...
for figure in doc.figures():             # list[Figure]
    ...
for cite in doc.citations():             # list[Citation] — inline <ref type="bibr">
    ...
for bib in doc.bibliography():           # list[BibReference]
    ...
```

Return-type fields (all `extra="forbid"`):

- [`Section`](../src/laglitsynth/fulltext_extraction/tei.py) —
  `id`, `title`, `paragraphs: list[str]`, `children: list[Section]`
  (recursive).
- [`Figure`](../src/laglitsynth/fulltext_extraction/tei.py) —
  `id`, `label`, `caption`.
- [`Citation`](../src/laglitsynth/fulltext_extraction/tei.py) —
  `target_id` (the bib id the inline ref points at, sans leading
  `#`), `text` (the citation string as it appears inline).
- [`BibReference`](../src/laglitsynth/fulltext_extraction/tei.py) —
  `id`, `authors: list[str]` (`"Surname, F."` form), `title`, `year`,
  `doi`, `raw` (full text of the `<biblStruct>`, whitespace-collapsed).

`TeiDocument(path)` raises `FileNotFoundError` eagerly on missing
files. XML parse errors surface lazily as `lxml.etree.XMLSyntaxError`
on the first accessor call.

### ExtractionMeta

```python
class ExtractionMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: _RunMeta      # tool, tool_version, run_at, validation_skipped
    grobid_version: str
    total_pdfs: int
    extracted_count: int
    failed_count: int
    invalid_stem_count: int
```

## Storage layout

```
data/fulltext-extraction/
  extraction.jsonl        # one ExtractedDocument per successfully parsed PDF,
                          # carrying tei_path + content_sha256
  extraction-meta.json    # ExtractionMeta
  tei/                    # canonical GROBID TEI XML output — the extraction
                          # artefact, read lazily via ExtractedDocument.open_tei
    W1234567890.tei.xml
```

TEI XML files are the canonical extraction artefact; the JSONL record
is a thin index over them. They are not committed to git.

The `extraction.jsonl` file plus the `tei/` subdirectory together are
consumed by downstream stages (eligibility, data extraction).

## CLI interface

```
laglitsynth fulltext-extraction \
    --pdf-dir data/fulltext-retrieval/pdfs/ \
    --output-dir data/fulltext-extraction/ \
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
   b. Save TEI XML to `tei/<work_id>.tei.xml`.
   c. Hash the TEI bytes (`sha256`).
   d. Write `ExtractedDocument(work_id, tei_path, content_sha256,
      extracted_at)` to `extraction.jsonl`.
4. Write `ExtractionMeta`.
5. Print summary: extracted count, failed count.

### GROBID availability

The CLI checks the GROBID health endpoint before starting. If GROBID is
not running, fail fast with a clear error message and the `docker run`
command to start it.

## What to build now

- GROBID client (POST PDF, receive TEI XML, write to `tei/`).
- `ExtractedDocument` / `ExtractionMeta` models and JSONL output.
- The `fulltext-extraction` CLI subcommand with `--skip-existing`.
- `TeiDocument` wrapper for downstream TEI access (see
  [`fulltext_extraction/tei.py`](../src/laglitsynth/fulltext_extraction/tei.py)).

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
