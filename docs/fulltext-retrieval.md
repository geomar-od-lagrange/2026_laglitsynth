# Full-text retrieval

Stage 5 acquires PDFs for the catalogue. It maintains a persistent,
work-keyed PDF store under `data/pdfs/` and three subcommands that fill
it: automatic retrieval from open-access sources, and an export/import
loop that lets collaborators contribute PDFs they can reach through their
own institutional access. The store is the first half's input to
[`corpus-assemble`](corpus.md), which copies the selected works' PDFs into
the corpus. Text extraction (parsing PDFs into structured sections) is a
separate concern handled by [fulltext-extraction.md](fulltext-extraction.md).

## Prerequisites

- Inputs: the deduplicated catalogue from
  [catalogue-dedup](catalogue-dedup.md),
  `<data-dir>/catalogue-dedup/deduplicated.jsonl` (`--catalogue`
  overrides). The store `<data-dir>/pdfs/` is created when absent; to fill
  the shared store, initialise the `data/` submodule first (see
  [pdf-store.md](pdf-store.md)). A filtered export also reads stage 3's
  verdicts, under the run-id in `<data-dir>/manifest.json` unless `--run-id`
  is passed. Import takes `--import-dir` and `--source`.
- Services: `fulltext-retrieval` downloads from OpenAlex open-access URLs
  and the Unpaywall API (network access). Export and import work offline.
- Credentials: `fulltext-retrieval` needs a contact email for Unpaywall, as
  `--email` or `UNPAYWALL_EMAIL` in `.env` (see
  [external-services.md](external-services.md#openalex-and-unpaywall)).

`fulltext-retrieval` works on every work in `--catalogue` (default: the
deduplicated catalogue); it reads no screening verdicts, so screening and
retrieval run in either order. The export, below, lists every catalogue
work without a PDF. With `--screening-threshold` it narrows that list by
screening score, since collaborator time is the cost worth saving: it joins
the catalogue against stage 3's verdict sidecar, keeping works at or above
the threshold and works whose score is `None` (the sentinel reasons). That
join **silently drops** any work absent from the screening verdicts file
(`_active_works` does `if sv is None: continue`), so confirm the verdicts
file covers the catalogue before a filtered export.

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

`data/pdfs/` is versioned in the private `data/` submodule, with the PDFs in
Git LFS; see [pdf-store.md](pdf-store.md).

## Provenance sidecar

Alongside the PDFs, `data/pdfs/provenance.jsonl` records, per work, where its
one PDF came from or that it is still missing. Each line is a
[`PdfProvenanceRecord`](../src/laglitsynth/fulltext_retrieval/models.py):

```python
class PdfSource(str, Enum):
    oa = "oa"                        # OA URL: OpenAlex location, Semantic Scholar or arXiv
    unpaywall = "unpaywall"          # Unpaywall best_oa_location
    manual = "manual"                # imported from a folder obtained any other way
    institutional = "institutional"  # scripted fetch over an institution's network
    zotero_desktop = "zotero-desktop"  # Zotero desktop "Find Available PDF"
    browser = "browser"              # browser session: Zotero connector or a clicked PDF link
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
PDF for the same work leaves one record with `source = zotero-desktop`.

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

For each catalogue work, retrieval tries OA sources and stops at the first
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
Downloads are rate-limited to one request per second per domain. The HTTP
client carries a per-request `timeout=30.0` seconds (distinct from the
per-domain rate limit — a slow server aborts at 30s rather than stalling the
run) and sets a `User-Agent: laglitsynth/0.1 (mailto:{email})` header on
every request. A work with
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
where a human exercises institutional access normally. The store's
`institutional` PDFs come from a one-off scripted fetch outside the CLI
(see [pdf-store.md](pdf-store.md)); no such fetcher is part of the
pipeline.

## Export → collaborator → import

PDF coverage from OA alone is patchy and a single IP gets throttled. The
people who can legitimately reach paywalled PDFs are several collaborators
with different institutional access — not one machine. The export/import loop
diversifies retrieval without any scraping.

### `fulltext-retrieval-export`

Takes the catalogue (`--catalogue`, default the deduplicated catalogue),
narrowed by screening score when `--screening-threshold` is given
(`--screening-verdicts` and `--run-id` pick the verdicts and are refused
without it), subtracts works that already have a non-`missing` provenance
record, and writes a handoff bundle into `--export-dir` (default
`data/pdfs/export/`):

- `dois.txt` — one `https://doi.org/<doi>` per line, for works that have a
  DOI. This is what a collaborator pastes into Zotero's "Add Item by
  Identifier" or a browser.
- `missing.ris` — one RIS record per missing work, for import into any
  reference manager: `TY  - JOUR`, `TI` (title), `AU` (authors), `PY`
  (year), `T2` (journal), `DO` (DOI), `UR` (`https://doi.org/<doi>`, or
  `primary_location.landing_page_url` for a work without a DOI) and `AN`
  (the work's stem). Zotero keeps `AN` on the item, so a PDF it finds is
  copied out as `<AN>.pdf` (see [pdf-store.md](pdf-store.md#zotero-desktop)).
  Tags without a value are left out.
- `pdf-manifest.csv` — the list for people, columns `work_id`, `stem`,
  `doi`, `title`, `year`, `expected_filename` (`<stem>.pdf`). Import does
  not read it.
- `no-doi.csv` — the works with no DOI, with `work_id`, `stem`, `title`,
  `year`, and `expected_filename`. These are the ones a collaborator has to
  search for by title. Always written, header row included, so its absence
  never has to be interpreted.
- `README.md` — what the bundle is, what each file is for, and what to send
  back: the PDFs as downloaded, in one folder, no renaming. It travels with
  the bundle, because the bundle travels and the repo does not.
- `export-meta.json` — how the bundle was made, as an
  [`ExportMeta`](../src/laglitsynth/fulltext_retrieval/models.py): git
  commit, run-id (None without a threshold or when `--screening-verdicts`
  was given explicitly), the resolved catalogue and verdicts paths (verdicts
  None when unfiltered), threshold (None when unfiltered), counts (active,
  already held, exported, with DOI), and pack sizes. This is what makes a
  bundle on a shared drive traceable back to the run that produced it.

Works without a DOI appear in `pdf-manifest.csv`, `no-doi.csv`, and
`missing.ris` (DOI empty) but not in `dois.txt` — there is no link to
resolve.

`--packs N` additionally splits the selection into `pack-1/` … `pack-N/`
under the bundle directory, contiguous slices of near-equal size, each a
complete bundle of its own whose README says which pack it is. One pack
goes to each collaborator. The top-level bundle stays complete, and import
matches against the whole catalogue, so which pack a folder came back from
does not matter.

The runner exports the gap when `EXPORT_GAP=1`, straight after stage 5. With
`STOP_AFTER_STAGE=5 EXPORT_GAP=1`, one invocation of
[run-pipeline.sh](../scripts/run-pipeline.sh) fetches the catalogue, screens
it, takes what open access gives, and writes the bundle for everything still
missing; the runner passes `SCREENING_THRESHOLD` as
`--screening-threshold`. `EXPORT_GAP` is an environment knob only: the
export rewrites the bundle, which a collaborator may be holding, so it is a
one-shot action rather than a property of the review config.

### `fulltext-retrieval-import`

Ingests a returned folder of PDFs into `data/pdfs/`. Takes `--import-dir`
(the folder a collaborator shipped back) and `--source`, the route the
folder's PDFs came by: `zotero-desktop`, `browser`, `institutional` or
`manual` (see [pdf-store.md](pdf-store.md#where-the-pdfs-come-from)).
Returned PDFs are matched against the whole `--catalogue` (default: the
deduplicated catalogue), not against the last export, so a folder sent
without a prior export, or a PDF for a work no export listed, still lands.
For each PDF it resolves a target stem, never guessed, in this order:

1. a self-identifying DOI that appears in the catalogue's DOI map, gathered
   from the Info dictionary, XMP metadata, link annotations on the first two
   pages, and first-page text. Every candidate is collected and the first
   metadata candidate present in the catalogue wins, because a publisher's
   Info dictionary often carries the journal's DOI rather than the article's.
   First-page text is accepted only when it names exactly one catalogue
   work: a correction notice or a cited companion paper puts two on page 1,
   and import reports that and skips rather than guess. Candidates never come
   from the reference list: in a topically clustered catalogue a bibliography
   is likely to cite other works in it. (Zotero's "Export Files" names
   attachments by item title, so filenames cannot be assumed to carry the
   DOI.)
2. a filename whose stem is a catalogue stem (`<stem>.pdf`, the name the
   RIS `AN` field gives, or a bare folder named that way);
3. a sidecar `*.csv`/`*.json` the collaborator returned that maps their
   filenames to DOIs;
4. a catalogue title the PDF carries
   ([title_match.py](../src/laglitsynth/fulltext_retrieval/title_match.py)),
   only for a PDF whose Info dictionary and XMP carry no DOI at all. A
   DOI there that is not in the catalogue says the PDF is some other
   document, such as an erratum, a reply or a book review, even when it
   prints a catalogue title; import reports it and skips. DOIs from link
   annotations and page text do not block the tier, because they also
   point at cited or corrected works. Candidates are the Info `/Title`, the
   XMP `dc:title`, and every run of one to eight consecutive lines among
   the first 40 non-blank lines of page 1. Titles and candidates are
   compared as keys (inline markup dropped, NFKC, casefolded, only letters
   and digits kept), so line breaks, hyphenation and dropped spaces do not
   matter, by `rapidfuzz`'s `fuzz.ratio`. A title is accepted only when it
   scores at least 95, beats the next-best title by at least 10, has a key
   of at least 30 characters, and belongs to one work. A shorter title is
   never accepted but still counts as the next-best, so it can block a
   correct match. A near-tie, or a title two works share, is reported with
   the contending stems and skipped. The rule was chosen against the PDF
   store
   ([contributor-pdf-matching.md](explorations/contributor-pdf-matching.md#title-matching-measured-against-the-pdf-store)).

Each candidate is validated by magic bytes, then deduplicated by
`content_sha256`: a byte-identical re-import is a no-op, and a PDF that
differs from one already stored for that work is skipped (the existing PDF
wins) unless `--overwrite` is passed. Matched, new PDFs are copied in and
their provenance is upserted with the given `--source`. A PDF that resolves
to no catalogue stem, or is not a real PDF, is reported and skipped. Import
prints a summary — matched and copied, skipped, and how many catalogue works
are still without a PDF after this import. The unmatched residual is a
matching problem, not the collaborator's: they are never asked to name
anything. What stays unmatched carries neither a catalogue DOI nor a
clearly matching title, or names a DOI outside the catalogue in its
metadata: a scanned page with no text, a record whose title is not the
paper's (a referee report), a preprint whose metadata carries its own
preprint DOI. Renaming such a file to `<stem>.pdf` and importing again
files it. An erratum or reply that carries no DOI in its metadata but
prints the corrected work's title is not caught by the gate and can be
filed under that work.

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
