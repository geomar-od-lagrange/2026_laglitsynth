# Full-text retrieval (Stage 5)

Concrete plan for turning the included catalogue into a corpus by retrieving
full paper text. This is the single hardest stage because the obstacles are
external: heterogeneous access, PDF extraction quality, legal grey areas,
and the inevitability of partial retrieval. The plan picks one approach and
defends it rather than surveying alternatives.

## Scope

Input: the included catalogue (works that passed screening and adjudication).
Output: a full-text corpus where each work has either structured body text or
an explicit `abstract_only` flag. Every downstream stage (eligibility, data
extraction, synthesis) consumes this output.

## Retrieval strategy

### Source priority

Try sources in this order for each work. Stop at the first success.

#### 1. OpenAlex OA locations

The `Work` model already carries `open_access.oa_url` and
`primary_location.pdf_url`. OpenAlex aggregates Unpaywall data, so this
covers the same ground without a separate Unpaywall API key. For
oceanography journals, expect 40-60% OA coverage. This is the cheapest
source: no authentication, no rate-limit pressure, and the URLs are already
in the data.

#### 2. Unpaywall API (fallback)

For works where OpenAlex has no OA URL, query Unpaywall directly by DOI.
Unpaywall sometimes has locations that OpenAlex missed or has not yet
indexed. The API is free, requires only an email address, and has generous
rate limits (100k requests/day).

#### 3. Preprint servers

Check for arXiv or ESSOAr versions via DOI resolution or OpenAlex's
`locations` list. Preprints may differ from the published version, but for
our purposes (extracting numerical methods descriptions) the differences are
rarely material. Tag the retrieved text with `version: preprint` so
downstream stages can flag it if needed.

#### 4. Manual batch

Everything else. Some papers are behind paywalls with no OA or preprint
version. Rather than automating institutional proxy authentication
(Shibboleth/SAML is fragile and non-portable), provide a CLI command that
exports the list of unretrieved DOIs. The human downloads PDFs through their
institutional access (browser, Zotero, or whatever works) and drops them
into a designated directory. The pipeline picks them up on the next run.

This is deliberately low-tech. Automating institutional access for a few
hundred papers is not worth the engineering effort or the legal risk.

### What we skip

- **Sci-Hub and shadow libraries.** Out of scope for a project that will be
  published as a formal systematic review.
- **Publisher APIs (Elsevier, Wiley, Springer).** Each has its own API with
  its own authentication, rate limits, and terms. Not worth building for a
  one-time corpus of a few hundred papers.
- **Google Scholar scraping.** Fragile, against ToS, and adds nothing that
  Unpaywall does not already cover.

## PDF to structured text

Use GROBID for all PDF-to-text conversion. The [GROBID operational
lessons](grobid-lessons.md) from litrag confirm it is the right tool: it
produces TEI XML with section structure, which is substantially more useful
for downstream extraction than raw text.

### GROBID integration

Run GROBID as a Docker container (`lfoppiano/grobid:0.8.0`). The pipeline
code calls the REST API, not GROBID's Java internals.

- Start GROBID before a retrieval run. The CLI does not manage the Docker
  container lifecycle -- it checks the health endpoint and fails fast with a
  clear error if GROBID is not running. A `docker compose` file in the repo
  makes starting it easy.
- Use `processFulltextDocument` with `consolidateCitations=0` (default off,
  per the grobid-lessons recommendation). Citation consolidation is opt-in
  for debugging, not part of the standard pipeline.
- Timeout: 120 seconds per paper. Papers that exceed this are marked as
  failed.

### TEI XML to plain sections

Parse GROBID's TEI XML output into a list of `(section_title, text)` pairs.
This is the representation that downstream stages consume -- not the raw XML
and not a single flattened string.

Fallback rules (from grobid-lessons):

- If a `<div>` has no `<head>` element, use `"Untitled section"` as the
  section title.
- If the body contains no `<div>` elements at all, treat the entire body as
  a single section titled `"Body"`.
- Ignore `<figure>` elements (captions are noisy and rarely contain methods
  information).
- Strip TEI markup within paragraphs; store plain text only.

### Non-PDF sources

Some OA locations return HTML (e.g. PLOS, MDPI). For the initial
implementation, download the HTML and extract body text with a simple
tag-stripping approach (BeautifulSoup, extract `<article>` or `<body>`).
No section structure -- just a single "Body" section. This is worse than
GROBID-parsed PDF but better than nothing, and it avoids the PDF round-trip
for born-HTML papers.

If HTML extraction proves too noisy, fall back to downloading the PDF
version (most OA HTML pages link to a PDF) and running it through GROBID.

## Handling unretrievable papers

A significant fraction of the corpus will be abstract-only. This is not a
failure -- it is the expected outcome, and the pipeline must handle it
cleanly.

### Retrieval status

Each work gets a retrieval status that records what happened:

- `full_text` -- body text successfully retrieved and parsed.
- `abstract_only` -- no full text found; work proceeds with abstract only.
- `failed` -- a source was found but retrieval or parsing failed (network
  error, GROBID timeout, corrupt PDF). These are retryable.

The `source_basis` field on downstream extraction records (already in the
design) derives from this status: `full_text` maps to `source_basis:
full_text`; `abstract_only` and `failed` both map to `source_basis:
abstract_only`.

### Retry and resume

Retrieval is idempotent. Running the command again skips works that already
have a `full_text` status and retries `failed` ones. This makes the
manual-batch workflow natural: run the pipeline, see what failed, drop PDFs
into the manual directory, run again.

## Data model additions

New models in `src/laglitsynth/fulltext/models.py`.

### RetrievedDocument

One per work that has retrieved full text.

```python
class Section(_Base):
    title: str
    text: str

class RetrievedDocument(_Base):
    work_id: str
    sections: list[Section]
    source_url: str | None = None
    source_type: str  # "oa_pdf", "oa_html", "preprint", "manual"
    grobid_used: bool
    retrieved_at: str
```

The `sections` list is the structured representation that downstream stages
consume. For abstract-only works, no `RetrievedDocument` is created -- the
absence of a document is itself the signal.

### RetrievalStatus

One per work in the included catalogue, regardless of outcome.

```python
class RetrievalStatus(_Base):
    work_id: str
    status: str  # "full_text" | "abstract_only" | "failed"
    source_url: str | None = None  # URL attempted, if any
    error: str | None = None  # for failed retrievals
    attempted_at: str
```

### RetrievalMeta

Run-level metadata, following the pattern of `FetchMeta` and `FilterMeta`.

```python
class RetrievalMeta(_Base):
    tool: str = "laglitsynth.fulltext.retrieve"
    tool_version: str = "alpha"
    retrieved_at: str
    total_works: int
    full_text_count: int
    abstract_only_count: int
    failed_count: int
```

### Storage layout

```
data/<run>/
  fulltext/
    documents.jsonl     # RetrievedDocument records
    status.jsonl        # RetrievalStatus records (one per work)
    meta.json           # RetrievalMeta
    pdfs/               # raw PDFs, named by work ID
    manual/             # directory for manually-provided PDFs
```

Raw PDFs are kept so GROBID parsing can be re-run if the parsing logic
improves. They are not committed to git (add `data/*/fulltext/pdfs/` to
`.gitignore`).

### Metadata authority

Per the grobid-lessons: OpenAlex remains the metadata authority. GROBID
output is used only for section-structured body text. Title, authors, DOI,
year -- all stay as they are in the `Work` model.

## CLI interface

A new subcommand: `laglitsynth retrieve`.

```
laglitsynth retrieve \
  --input data/screening-run/included.jsonl \
  --output-dir data/screening-run/fulltext/ \
  --manual-dir data/screening-run/fulltext/manual/ \
  --grobid-url http://localhost:8070 \
  --email user@example.com
```

Arguments:

- `--input` -- path to the included catalogue JSONL (Work records).
- `--output-dir` -- where to write documents, status, and meta files.
- `--manual-dir` -- directory to scan for manually-provided PDFs. Optional;
  defaults to `<output-dir>/manual/`.
- `--grobid-url` -- GROBID API base URL. Required.
- `--email` -- email for Unpaywall API. Required (their only auth mechanism).
- `--retry` -- re-attempt `failed` works only. Default: process all
  unprocessed works.
- `--skip-unpaywall` -- skip the Unpaywall fallback (useful if you know
  OpenAlex OA data is sufficient).

The command:

1. Loads existing `status.jsonl` if present, to know which works to skip.
2. For each unprocessed work, tries sources in priority order.
3. For successful PDF downloads, calls GROBID and parses the TEI XML.
4. Writes `RetrievedDocument` to `documents.jsonl` (append mode).
5. Writes `RetrievalStatus` for every work (append mode, one entry per
   attempt; latest entry wins).
6. Writes `RetrievalMeta` with summary counts.
7. Logs progress to stderr (work ID, source tried, outcome).

### Rate limiting

- Unpaywall: 1 request/second (their stated polite limit). Use a simple
  `time.sleep` throttle.
- PDF downloads: 1 request/second per domain. Group downloads by domain and
  throttle per-group. This avoids hammering a single publisher.
- GROBID: no throttle needed (local service), but respect the 120-second
  timeout.

## What to build vs. what to defer

### Build now

- OpenAlex OA URL extraction (already in the data model, just use it).
- Unpaywall API client (simple REST: `GET
  https://api.unpaywall.org/v2/{doi}?email={email}`).
- PDF download with retry (requests + tenacity, 3 retries with exponential
  backoff).
- GROBID client (POST multipart form to the processFulltextDocument
  endpoint).
- TEI XML parser (extract sections from GROBID output).
- Manual PDF directory scanner.
- The `retrieve` CLI subcommand.
- The Pydantic models described above.
- A `docker-compose.yml` for GROBID.

### Defer

- HTML-to-text extraction for born-HTML papers. Start with PDF-only; add
  HTML support when we see how many papers it would rescue.
- Preprint-specific handling (arXiv LaTeX source, ESSOAr). Start with PDF
  downloads from preprint servers; structured LaTeX parsing is a rabbit hole.
- Any kind of automated institutional proxy authentication.
- Parallelism. Process papers sequentially. The bottleneck is network I/O
  and GROBID processing time, not CPU. For a few hundred papers, sequential
  processing with resume support is sufficient.
- Citation consolidation in GROBID. Not needed for the pipeline; references
  come from OpenAlex.

## Risks and mitigations

### OA coverage is lower than expected

If fewer than 40% of papers have OA full text, the corpus is
disproportionately abstract-only and downstream extraction quality suffers.

Mitigation: the manual-batch escape hatch. After the automated run, export
the unretrieved DOI list and use institutional access for the most important
papers (e.g. highly cited, borderline eligibility). The retrieval status
tracking makes it easy to see what is missing and prioritise.

### GROBID parsing quality varies

Two-column layouts, scanned PDFs, and unusual journal formats produce poor
section structure.

Mitigation: the `Section` model is deliberately simple (title + text). Bad
parsing degrades gracefully -- the text is still there, just in fewer or
wrongly-titled sections. Downstream extraction (stage 8) uses the two-pass
pattern, which is robust to imperfect section boundaries because pass 1
searches all text regardless of section labels.

### Publisher rate-limiting or blocking

Some publishers block programmatic PDF downloads even for OA content.

Mitigation: per-domain throttling (1 req/s), a polite User-Agent header,
and the manual-batch fallback. If a publisher blocks us, those papers go
into the manual queue. Not worth fighting.

### GROBID Docker issues on Apple Silicon

GROBID's Docker image is amd64-only. Rosetta emulation works but is slow.

Mitigation: accept the slowdown for development. For a production run on
hundreds of papers, use a Linux VM or CI runner. The pipeline is designed
for resume, so you can start on a laptop and finish on a server.

### Legal risk of bulk PDF downloading

Downloading OA PDFs for research purposes is generally accepted, but
aggressive scraping is not.

Mitigation: only download papers that are tagged as OA by Unpaywall or
OpenAlex. Respect rate limits. Do not circumvent access controls. The
manual-batch path exists specifically so that paywalled papers are accessed
through legitimate institutional channels, not automated scraping.
