# Full-text retrieval

Retrieve PDFs for works that pass the screening threshold. This is where the
catalogue becomes a corpus. The output is PDFs on disk and a retrieval
status record per work — nothing more. Text extraction (parsing PDFs into
structured sections) is a separate concern handled by
[fulltext-extraction.md](fulltext-extraction.md).

The stage joins the deduplicated catalogue against the stage 3 screening
verdict sidecar at a caller-supplied `--screening-threshold` to determine
the active work set.

## Source cascade

Try sources in this order for each work. Stop at the first success.

### 1. Manual batch

Manual files live on local disk and are deliberately placed by a human;
checking them first means re-runs pick them up cheaply and manual placement
always wins. Export a list of unretrieved DOIs and filenames. The human
downloads PDFs through institutional access (library proxy, interlibrary
loan, Zotero, whatever works) and drops them into a designated directory.
The pipeline picks them up on the next run, matched by filename
(`W<OpenAlex-ID>.pdf`).

This is deliberately not automated. Shibboleth/SAML is institutionally
specific and fragile. A list of DOIs and an afternoon at the library is the
pragmatic choice for a corpus of a few hundred papers.

### 2. OpenAlex open-access URLs

The `Work` model already carries `open_access.oa_url` and
`primary_location.pdf_url`. These come from Unpaywall data aggregated by
OpenAlex. OA coverage for oceanography is unknown and should be measured
on a sample before committing to a retrieval strategy. This is the
cheapest source: no additional API calls, URLs already in the data.

### 3. Unpaywall API

For works with a DOI but no usable OpenAlex OA link, query the Unpaywall
API (`api.unpaywall.org/v2/{doi}?email=...`). Unpaywall sometimes has
locations that OpenAlex has not yet indexed. Free, requires only an email
address, rate limit 100k requests/day.

### 4. Preprint servers

Check for arXiv or ESSOAr versions via DOI resolution or OpenAlex's
`locations` list. For arXiv, construct the PDF URL directly from the arXiv
ID. Preprints may differ from the published version but for our purposes
(extracting numerical methods) the differences are rarely material.

Preprints are retained for prototyping and vocabulary discovery. For
quantitative RQ analyses, preprints must be excludable — the data model
carries peer-review status so that downstream stages can filter by
publication type. Preprint retrieval does not imply preprint inclusion in
quantitative results.

### What we skip

- **Sci-Hub and shadow libraries.** Legal risk, not appropriate for a formal
  systematic review.
- **Publisher APIs.** Each has its own authentication and terms. Not worth
  the integration cost for a one-off review.
- **Google Scholar scraping.** Fragile, against ToS.

### Future sources

The PDF-on-disk artifact is just files in a directory. Adding new retrieval
sources (Zotero translators, Semantic Scholar, institutional bulk exports)
later means writing a new downloader and merging PDFs into the same
directory. No special design is needed to support this — it falls out of
the decoupled retrieval/extraction split.

## Data model

New models in [`src/laglitsynth/fulltext_retrieval/models.py`](../src/laglitsynth/fulltext_retrieval/models.py).

### RetrievalStatus (enum)

```python
class RetrievalStatus(str, Enum):
    retrieved_oa = "retrieved_oa"
    retrieved_unpaywall = "retrieved_unpaywall"
    retrieved_manual = "retrieved_manual"
    abstract_only = "abstract_only"
    failed = "failed"
```

The `retrieved_*` variants record which source succeeded. Useful for PRISMA
flow reporting and for understanding corpus coverage. `abstract_only` means
no source had a PDF. `failed` means a source was found but download failed
(network error, 403, corrupt file).

### RetrievalRecord

One per work in the active (above-threshold) work set, regardless of outcome.

```python
class RetrievalRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str                          # OpenAlex ID
    retrieval_status: RetrievalStatus
    source_url: str | None = None         # URL the PDF was fetched from
    pdf_path: str | None = None           # relative path to stored PDF
    error: str | None = None              # error message if failed
    retrieved_at: str                     # per-record wall-clock timestamp
```

### RetrievalMeta

Run-level metadata.

```python
class RetrievalMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta      # tool, tool_version, run_at, validation_skipped
    total_works: int
    retrieved_count: int
    abstract_only_count: int
    failed_count: int
    by_source: dict[str, int]
```

## Storage layout

```
data/fulltext-retrieval/
  retrieval.jsonl       # one RetrievalRecord per work
  retrieval-meta.json   # RetrievalMeta
  pdfs/                 # raw PDFs, named by OpenAlex work ID
    W1234567890.pdf
  manual/               # drop zone for manually downloaded PDFs
```

PDFs are named by OpenAlex ID (the `W...` suffix). The manual directory
uses the same naming convention — the CLI prints expected filenames when
reporting unretrieved works.

PDFs are not committed to git. Add `data/fulltext-retrieval/pdfs/` to `.gitignore`.

## CLI interface

```
laglitsynth fulltext-retrieval \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --screening-verdicts data/screening-abstracts/<run-id>/verdicts.jsonl \
    --screening-threshold 50 \
    --output-dir data/fulltext-retrieval/ \
    --email $UNPAYWALL_EMAIL \
    [--manual-dir data/fulltext-retrieval/manual/] \
    [--skip-existing] \
    [--dry-run]
```

### Arguments

- `--catalogue`: path to the deduplicated catalogue JSONL (`Work` records
  from stage 2).
- `--screening-verdicts`: path to the stage 3 verdict sidecar
  (`ScreeningVerdict` records).
- `--screening-threshold`: relevance score cutoff 0–100 (default: 50).
  Works at or above the threshold are retrieved.
- `--output-dir`: where to write retrieval records, metadata, and PDFs.
- `--email`: contact email for Unpaywall API requests. When omitted the tool
  reads `UNPAYWALL_EMAIL` from `.env` in the working directory and emits
  `Loaded UNPAYWALL_EMAIL from .env` to stderr. The explicit flag takes
  precedence when both are present.
- `--manual-dir`: directory to scan for manually placed PDFs. Defaults to
  `<output-dir>/manual/`.
- `--skip-existing`: do not re-retrieve works that already have a
  `retrieved_*` status. Enables resumability.
- `--dry-run`: report what would be retrieved without downloading.

### Resumability

The command writes one JSONL line per work as it processes. Re-running with
`--skip-existing` picks up where it left off. Works with `failed` status
are retried (the failure may have been transient). Works with
`abstract_only` status are retried too (a manual PDF may have appeared).

### Rate limiting

- Unpaywall: 1 request/second.
- PDF downloads: 1 request/second per domain. Avoids hammering a single
  publisher CDN.
- On HTTP 429 or 403: log, mark as `failed`, include the DOI in the
  unretrieved list. Do not retry immediately.

### Reporting

Print a summary to stderr after the run (example format, numbers are
placeholders):

```
Retrieval summary:
  Total works:            NNN
  Retrieved (OA):         NNN  (NN.N%)
  Retrieved (Unpaywall):  NNN  (NN.N%)
  Retrieved (manual):     NNN  (NN.N%)
  Abstract-only:          NNN  (NN.N%)
  Failed:                 NNN  (NN.N%)
```

Also write the list of unretrieved DOIs (abstract-only + failed) to
`<output-dir>/unretrieved.txt` for the manual-batch workflow.

## What to build first

### Build now

- PDF download from OpenAlex OA URLs.
- Unpaywall API client.
- Manual PDF directory scanner.
- `RetrievalRecord` / `RetrievalMeta` models and JSONL output.
- The `fulltext-retrieval` CLI subcommand with `--skip-existing`.
- Summary report and unretrieved DOI list.

### Defer

- Preprint-server detection (arXiv PDF URL construction, ESSOAr). Add when
  we see how much coverage OA + Unpaywall + manual provides.
- Parallel downloads. Sequential with resume is sufficient for hundreds of
  papers.
- Any form of automated institutional access.

## Risks and mitigations

### OA coverage lower than expected

If OA coverage is low, the manual burden grows. Mitigation: run retrieval
early on a 50-work sample to measure actual coverage before committing.
If coverage is poor, prioritise the manual path and consider adding
Semantic Scholar as a source.

### Publisher rate-limiting or blocking

Some CDNs block programmatic downloads even for OA content. Mitigation:
per-domain throttling, polite `User-Agent` header, and the manual fallback.
Blocked papers go to the unretrieved list — not worth fighting.

### Legal concerns

Downloading OA PDFs for text mining is legal in most jurisdictions (EU TDM
exception, US fair use for research). Paywalled content goes through the
manual path where the human exercises institutional access normally.

### PDF storage size

A few hundred PDFs at 1–5 MB each: 0.5–1.5 GB. Manageable but must stay
out of git.
