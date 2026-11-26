# Full-text extraction

Parse the corpus PDFs into structured sections. `corpus-assemble`
produces the input (the PDF copies in `data/corpus/pdfs/`, listed in
`data/corpus/provenance.jsonl`); downstream stages (eligibility, data
extraction) consume the output. Separating extraction from retrieval
means:

- Retrieval can run without GROBID (just get the PDFs).
- Extraction can be re-run when parsing logic or tools improve.
- Alternative consumers (e.g. feeding raw PDFs to a long-context LLM) can
  bypass extraction entirely and work with the PDFs directly.

## Prerequisites

Start the GROBID container before running this stage:

```bash
docker run --rm -p 8070:8070 lfoppiano/grobid:0.8.0
```

GROBID takes ~30–60 seconds to start.
Health check: `GET http://localhost:8070/api/isalive`.
See [external-services.md](external-services.md) for the full setup (Apple
Silicon Rosetta workaround, memory requirements, crash recovery).

The stage reads only the [corpus](corpus.md), so the machines that
acquire PDFs need no GROBID or Docker. The PDF is the scarce artifact;
its text can always be re-extracted.

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
- Startup takes ~30–60 seconds. Health check: `GET /api/isalive`.
- Memory footprint: ~2 GB.
- Processing endpoint: `POST /api/processFulltextDocument` with the PDF as
  multipart form data.
- Timeout: 120 seconds per paper.
- `consolidateCitations` defaults to `0` (off) and is settable via the
  `--consolidate-citations {0,1}` flag. We already have references from
  OpenAlex, so the default leaves them off.

### TEI XML as the canonical artefact

GROBID's TEI XML output is saved to disk as the canonical extraction
artefact — one `.tei.xml` per PDF. The stored `ExtractedDocument`
record carries only `work_id`, `tei_path` (relative to the output
directory), `content_sha256` of the TEI bytes, `pdf_sha256` of the corpus
PDF it came from, and the `extracted_at` timestamp. Section text, figures, citations, and
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

### GROBID failures

A GROBID failure produces no record at all: the PDF is logged as failed
and counted in `ExtractionMeta.failed_count`, but no `ExtractedDocument`
is written, so the absence is the only signal. `ExtractedDocument`
carries no quality field — there is no extraction-time quality gate.
The check for empty or malformed TEI lives downstream: stages 7/8 record
a document whose TEI has no parseable sections as a `tei-parse-failure`
sentinel.

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
    pdf_sha256: str              # CorpusPdf.content_sha256 of the source PDF
    extracted_at: str            # per-record wall-clock timestamp

    def open_tei(self, output_dir: Path) -> TeiDocument:
        ...
```

`tei_path` is stored as the relative string `"tei/<work_id>.tei.xml"`,
mirroring [`PdfProvenanceRecord.pdf_path`](../src/laglitsynth/fulltext_retrieval/models.py).
Consumers join with their `output_dir` at read time — typically via
`ExtractedDocument.open_tei(output_dir)`, which returns a
`TeiDocument` wrapper over the TEI file on disk.

`content_sha256` is a sha256 of the TEI bytes, computed at extract
time. The hash becomes the stable identity for a given extraction
across re-runs (a newer GROBID version may emit different TEI for the
same `work_id`).

`pdf_sha256` is the corpus copy's `content_sha256` at extraction time.
`extraction.jsonl` is appended to, so a work re-extracted after its PDF
changed has several records; the last one counts. Stages 7 and 8 use a
record only when its `pdf_sha256` matches the current corpus copy.

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
    run: RunMeta      # tool, tool_version, run_at, validation_skipped
    corpus_id: str    # CorpusMeta.corpus_id of the corpus the PDFs came from
    grobid_version: str
    total_pdfs: int   # corpus works with a PDF
    extracted_count: int
    failed_count: int
```

Stages 7 and 8 exit when `extraction-meta.json` is missing or names
another `corpus_id` than the current corpus.

`grobid_version` is read from the GROBID `/api/version` probe at startup;
if that probe fails it falls back to the literal string `"unknown"`.

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
    [--corpus-dir data/corpus/] \
    [--output-dir data/fulltext-extraction/] \
    [--data-dir data/] \
    [--grobid-url http://localhost:8070] \
    [--timeout 120] \
    [--skip-existing] \
    [--consolidate-citations 0] \
    [--concurrency 1]
```

### Arguments

- `--corpus-dir`: the corpus written by `corpus-assemble`. Defaults to
  `<data-dir>/corpus/`; exits naming `corpus-assemble` when its
  `corpus-meta.json` is absent.
- `--output-dir`: where to write extraction records, metadata, and TEI
  files. Defaults to `<data-dir>/fulltext-extraction/`.
- `--data-dir`: bucket root the two defaults above hang off (default:
  `data/`).
- `--grobid-url`: GROBID API endpoint. Optional; defaults to
  `http://localhost:8070`.
- `--timeout`: per-paper GROBID timeout in seconds. Optional; defaults to
  `120.0`.
- `--skip-existing`: do not re-extract a work whose latest
  `ExtractedDocument` has the `pdf_sha256` of its current corpus copy. A
  work whose PDF changed in a re-assembly is extracted again.
- `--consolidate-citations`: GROBID's `consolidateCitations` form field,
  one of `0` or `1`. Optional; defaults to `0` (off). `1` instructs GROBID
  to enrich extracted references with DOIs via Crossref / Semantic Scholar
  (needs internet during processing and is slower); since OpenAlex is the
  metadata authority, `0` is recommended.
- `--concurrency`: number of in-flight GROBID requests (default: `1`).
  See [interfaces.md](interfaces.md#grobid-concurrency).

### Flow

1. Read the `CorpusPdf` rows of `<corpus-dir>/provenance.jsonl` that
   carry a `pdf_path`; works without a PDF are not listed.
2. Load existing `extraction.jsonl` if present (for `--skip-existing`).
3. For each work not skipped, with up to `--concurrency` requests in
   flight:
   a. Submit `<corpus-dir>/<pdf_path>` to GROBID.
   b. Save TEI XML to `tei/<stem>.tei.xml` (the main thread does this
      and step d, in completion order).
   c. Hash the TEI bytes (`sha256`).
   d. Append `ExtractedDocument(work_id, tei_path, content_sha256,
      pdf_sha256, extracted_at)` to `extraction.jsonl`.
4. Write `ExtractionMeta` with the corpus's `corpus_id`.
5. Print summary: extracted count, failed count.

### GROBID availability

The CLI checks the GROBID health endpoint before starting. If GROBID is
not running, fail fast with a clear error message and the `docker run`
command to start it.

### Recovery after a GROBID crash

If GROBID goes down mid-corpus, restart the container and re-run with
`--skip-existing` to continue from where extraction stopped:

```bash
docker run --rm -p 8070:8070 lfoppiano/grobid:0.8.0  # restart GROBID
laglitsynth fulltext-extraction --skip-existing
```

Works whose latest `ExtractedDocument` in `extraction.jsonl` matches the
current corpus PDF are skipped. See [external-services.md](external-services.md)
for the macOS/Apple Silicon Rosetta workaround if GROBID fails to start.

## Limits

- GROBID is the only extraction tool. There is no fallback (PyMuPDF,
  marker, nougat), no HTML-to-text for born-HTML papers and no LaTeX
  parsing for arXiv sources. `ExtractedDocument` does not encode the
  tool, so adding one is a code change, not a schema change.
  `ExtractionMeta.failed_count` shows how often GROBID fails on the
  corpus.
- The operator starts and stops GROBID; the CLI only checks and uses it.
- GROBID's TEI varies across paper types. The parser handles the known
  shapes listed above; other shapes surface downstream as
  `tei-parse-failure`.
