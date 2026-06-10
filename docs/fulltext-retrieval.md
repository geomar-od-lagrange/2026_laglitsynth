# Full-text retrieval

Stage 5 turns the screened catalogue into a corpus of PDFs. It maintains a
persistent, work-keyed PDF store under `data/pdfs/` and three subcommands
that fill it: automatic retrieval from open-access sources, and an
export/import loop that lets collaborators contribute PDFs they can reach
through their own institutional access. Text extraction (parsing PDFs into
structured sections) is a separate concern handled by
[fulltext-extraction.md](fulltext-extraction.md).

The active work set is the screening-gated selection: the deduplicated
catalogue joined against stage 3's verdict sidecar at a caller-supplied
`--screening-threshold`. Works at or above the threshold (and works whose
score is `None`, the sentinel reasons) are in scope.

## The persistent PDF store

The store is one directory, `data/pdfs/`, holding `data/pdfs/<stem>.pdf` for
every work whose PDF has been obtained by any means. The stem is the
OpenAlex work-id leaf from
[work_id_to_filename](../src/laglitsynth/ids.py). The store is the union
over every search a review runs, deduplicated by work: a PDF is fetched once
per work and reused by every search that includes it, so a new search mostly
reuses PDFs already on disk and only fills the margin. This is the storage
analogue of what [catalogue-dedup](../src/laglitsynth/catalogue_dedup/) does
for catalogues.

There is exactly one PDF per work — no preprint-versus-published split.
Downstream extraction reads one full text per work and does not branch on
version, so a single slot per work keeps provenance to a single record
rather than a log of competing attempts.

The store is keyed on the work stem, not the DOI. Roughly 10–15% of
WoS-only records carry no DOI, and a DOI-keyed store would have no slot for
them; every work has an OpenAlex id and therefore a stem, so stem-keying
gives every work a slot uniformly. DOI is recorded as provenance and used as
a match hint at import, never as the filesystem name.

PDFs are not committed to git; add `data/pdfs/` to `.gitignore`.

## Provenance sidecar

Alongside the PDFs, `data/pdfs/provenance.jsonl` records, per work, where its
one PDF came from or that it is still missing. Each line is a
[`PdfProvenanceRecord`](../src/laglitsynth/fulltext_retrieval/models.py):

```python
class PdfSource(str, Enum):
    oa = "oa"                        # OA URL on the Work record
    unpaywall = "unpaywall"          # Unpaywall best_oa_location
    zotero_import = "zotero-import"  # ingested from a Zotero-exported folder
    manual = "manual"                # ingested from a plain folder drop
    missing = "missing"              # no PDF yet; recorded so gaps are addressable


class PdfProvenanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    stem: str                # data/pdfs/<stem>.pdf
    doi: str | None          # None for DOI-less works
    source: PdfSource
    source_url: str | None   # the URL a PDF was fetched from, when applicable
    pdf_path: str | None     # "pdfs/<stem>.pdf" when source != missing, else None
    content_sha256: str | None  # of the PDF bytes; None when missing
    obtained_at: str         # ISO-8601 UTC of the record
```

The store only cares whether a PDF exists, so `missing` collapses both "no
source was ever found" and "a download failed." Provenance is work-keyed and
last-write-wins per work: re-running OA retrieval and then importing a Zotero
PDF for the same work leaves one record with `source = zotero-import`.

The store is single-writer by design. One operator drives the pipeline and
is the only process that writes `data/pdfs/`, so `provenance.jsonl` is a
plain whole-file record: it is read into a `dict[str, PdfProvenanceRecord]`
keyed by `work_id` on startup and rewritten atomically. There is no
concurrent writer to race, hence no append-only journal or locking. Helpers
live in [store.py](../src/laglitsynth/fulltext_retrieval/store.py).

The per-invocation meta sidecar
[`RetrievalMeta`](../src/laglitsynth/fulltext_retrieval/models.py) is written
to `data/fulltext-retrieval/retrieval-meta.json` with counters recomputed
over `PdfSource`.

## Automatic retrieval: `fulltext-retrieval`

For each active work, retrieval tries OA sources and stops at the first
success:

1. OpenAlex open-access URLs — `primary_location.pdf_url` and
   `open_access.oa_url`, already in the `Work` record. Cheapest source: no
   extra API call.
2. Unpaywall — for works with a DOI but no usable OpenAlex OA link, query
   `api.unpaywall.org/v2/{doi}?email=...` and download its
   `best_oa_location`. Free, requires only an email (read from `--email` or
   `UNPAYWALL_EMAIL` in `.env`).

Every downloaded response is validated by magic bytes (`%PDF`) before it is
stored, so an HTML error page served with a PDF content-type is rejected.
Downloads are rate-limited to one request per second per domain. A work with
no source, or whose every download failed, gets a `missing` record and no
PDF. Retrieval is sticky by default: a work that already has a non-`missing`
provenance record is skipped, so a plain run attempts only `missing` and
never-seen works and never re-downloads or overwrites a PDF already held —
obtaining PDFs is the scarce resource (rate limits, captchas even for paid
access), and a "successful" re-fetch could replace a good full text with a
paywalled stub. `--refetch` opts back into re-downloading held works for the
rare case where a stored PDF is known bad; a failed re-fetch never downgrades
the prior record to `missing` or deletes the held PDF. `--dry-run` reports
what would be retrieved without downloading.

Sci-Hub and shadow libraries, publisher APIs, and Google Scholar scraping
are all out of scope — legal risk or integration cost not appropriate for a
systematic review. Paywalled PDFs come in through the import loop below,
where a human exercises institutional access normally.

## Export → collaborator → import

PDF coverage from OA alone is patchy and a single IP gets throttled. The
people who can legitimately reach paywalled PDFs are several collaborators
with different institutional access — not one machine. The export/import loop
diversifies retrieval without any scraping.

### `fulltext-retrieval-export`

Resolves the active selection, subtracts works that already have a
non-`missing` provenance record, and writes a handoff bundle into
`--export-dir` (default `data/pdfs/export/`):

- `dois.txt` — one `https://doi.org/<doi>` per line, for works that have a
  DOI. This is what a collaborator pastes into Zotero's "Add Item by
  Identifier" or a browser.
- `missing.ris` — one RIS record per missing work (title, authors, year,
  journal, DOI), for import into any reference manager.
- `pdf-manifest.csv` — the round-trip key, columns `work_id`, `stem`, `doi`,
  `expected_filename` (`<stem>.pdf`). Import reads this to match returned
  PDFs back to works; it is written by us, so matching never depends on what
  a collaborator's tool names a file.

Works without a DOI appear in `pdf-manifest.csv` and `missing.ris` (DOI
empty) but not in `dois.txt` — there is no link to resolve.

### `fulltext-retrieval-import`

Ingests a returned folder of PDFs into `data/pdfs/`. Takes `--import-dir`
(the folder a collaborator shipped back), `--manifest` (the
`pdf-manifest.csv` from the matching export), and `--source`
(`zotero-import` | `manual`). For each PDF it resolves a target stem, never
guessed, in this order:

1. an embedded DOI in the PDF metadata or first-page text that appears in the
   manifest's DOI map (Zotero's "Export Files" names attachments by item
   title, so filenames cannot be assumed to carry the DOI);
2. a filename whose stem matches the manifest directly (the plain-folder-drop
   case, where the collaborator was asked to name files `<stem>.pdf`);
3. a sidecar `*.csv`/`*.json` the collaborator returned that maps their
   filenames to DOIs.

Each candidate is validated by magic bytes, then deduplicated by
`content_sha256`: a byte-identical re-import is a no-op, and a PDF that
differs from one already stored for that work is skipped (the existing PDF
wins) unless `--overwrite` is passed. Matched, new PDFs are copied in and
their provenance is upserted with the given `--source`. A PDF that resolves
to no manifest stem, or is not a real PDF, is reported and skipped. Import
prints a summary — matched and copied, skipped, and how many manifest works
are still missing after this import.

A deferred enhancement would let contributors ignore filenames and sidecars
entirely, matching returned PDFs against the closed manifest by broadened
DOI extraction (XMP, link annotations) and a title fallback —
[contributor-pdf-matching.md](explorations/contributor-pdf-matching.md)
captures the design and why its data-dependent half waits for real returns.

## Collaboration model

The store is single-writer. One person — call them A — drives the pipeline
and is the only process that writes `data/pdfs/`, `provenance.jsonl`, and the
meta. Collaborators B–D do not run retrieval against A's tree; they receive
an export bundle, fetch PDFs through their own access, and hand back a folder
of PDFs, which A ingests with `fulltext-retrieval-import`. Because import
dedups by content hash and one-PDF-per-work, two collaborators returning the
same PDF is harmless, so partitioning the missing list across B–D is an
efficiency nicety, not a correctness requirement. See
[cross-machine.md](cross-machine.md) for the drop-zone rule that keeps the
whole-file provenance from ever racing.
