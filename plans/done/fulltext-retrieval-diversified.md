# Diversifying full-text retrieval

## Goal

Stage 5 today ([fulltext_retrieval/retrieve.py](../../src/laglitsynth/fulltext_retrieval/retrieve.py))
runs one process against OA URLs, Unpaywall, and a single manual drop
directory, writing PDFs into a per-run `--output-dir`. PDF coverage from
any single source is patchy, publishers throttle a single IP, and the
people who can legitimately reach paywalled PDFs are several collaborators
with different institutional access — not one machine. This plan turns the
store into a persistent, work-keyed PDF store under `data/`, shared across
every search a review runs, and adds two cheap diversification paths on top
of today's automatic retrieval: an *export* step that lists works still
lacking a PDF as DOI links plus RIS plus a round-trippable CSV, and an
*import* step that ingests a folder of PDFs a collaborator fetched
through their own institutional access (via the Zotero application or a
plain folder drop), matched back to works by an explicit DOI→stem mapping.
The blocking question this plan was deferred on — *where the persistent
store lives relative to per-search runs* — is answered by the
[run-manifest](../run-manifest.md) plan: the store lives under `data/` and the
manifest is the index over it.

## Non-goals

Legitimate access only — OA plus collaborators' own licensed access via
their own tools; no scraping behind paywalls, no captcha-solving, no
Sci-Hub, no bulk publisher hammering. We use the Zotero *application*, run
by a human with the right subscription, never its AGPL code or
translation-server (see [zotero-retrieval.md](../../docs/explorations/zotero-retrieval.md)).
This plan does not build the per-attempt log or pluggable-resolver
framework an earlier draft proposed — that stays deferred (see Follow-ups).
It does not store multiple PDF versions per work; one PDF per work, chosen
below. It does not do text extraction — that is stage 6
([fulltext_extraction](../../src/laglitsynth/fulltext_extraction/)) — and it
does not backfill abstracts, which is the separate
[DOI → abstract lookup](doi-abstract-lookup.md) plan.

## Collaboration model and build scope

This store is single-writer. One person — call them A — drives the
pipeline and is the only process that ever writes `data/pdfs/`,
`provenance.jsonl`, and the meta. Collaborators B–D do not run retrieval
against A's tree; they receive an export bundle, fetch PDFs through their
own institutional access, and hand back a *folder* of PDFs, which A
ingests with `fulltext-retrieval-import`. The shared file store is a
**drop zone for those folders, not a shared mutable store** — B–D push
artifacts, A integrates them serially. Because A is the sole writer, the
work-keyed `provenance.jsonl` stays a plain whole-file
last-write-wins record (read-all, rewrite-atomic): there is no concurrent
writer to race, so no append-only journal or locking is needed. Import
dedups by `content_sha256` and one-PDF-per-work, so two collaborators
returning the same PDF is harmless — which means partitioning the missing
list across B–D is an *efficiency* nicety, not a correctness requirement,
and is left out of this build (A can split `dois.txt` by hand). The
"pause" while retrieval happens needs no mechanism: the pipeline is manual
subcommands, so A simply does not run extraction until the gap is small
enough, checked by re-running export.

Manifest wiring is **out of scope for this build.** The
[run-manifest](../run-manifest.md) plan is not yet implemented, so every
stage here takes its inputs via explicit flags exactly as today; there are
no `latest_output` / `append_stage` calls. When the manifest lands, the
omitted-input resolution and lineage entry are added then, as that plan
describes — additively, breaking nothing here.

## Target state

### A work-keyed, persistent PDF store under `data/`

The PDF store stops being a per-run `--output-dir/pdfs/` tree and becomes
one persistent directory, `data/pdfs/`, holding `data/pdfs/<work-stem>.pdf`
for every work whose PDF has been obtained by any means. The stem is the
OpenAlex work-id leaf produced by
[work_id_to_filename](../../src/laglitsynth/ids.py) — exactly the filename
stage 5 writes today, so this is a relocation of the store, not a new
naming scheme. A PDF is fetched once per work and reused by every search
that includes that work; a new search mostly reuses PDFs already on disk
and only fills the margin. This is the storage analogue of what
[dedup](../../src/laglitsynth/catalogue_dedup/) already does for catalogues:
the store is the union over searches, deduplicated by work, and a search is
a *view* over it, not a private copy. The manifest's `data_dir` is the
project root the store hangs off, and `data/pdfs/` sits beside the other
per-stage subdirs the manifest already resolves.

#### One PDF per work, not versions

The store holds exactly one PDF per work — `data/pdfs/<work-stem>.pdf`,
no preprint-vs-published split. The pipeline's downstream consumer
(stage 6 extraction) reads one full text per work and does not branch on
version; carrying multiple versions would force a "which one feeds
extraction" choice with no consumer to make it for. Keeping it to one PDF
is what lets the attempt/version/resolver model stay deferred: with a
single slot per work, provenance is a single record saying where that one
PDF came from, not a log of competing attempts. If a later stage ever needs
version provenance, that is the trigger to revisit the deferred model — not
before.

#### DOI-less works key on the work stem

The store is keyed on the work stem, not the DOI. DOI is provenance and a
retrieval/match hint, never the filesystem key. This is deliberate: ~10–15%
of WoS-only records carry no DOI at all (see
[wos-starter-api.md](../../docs/explorations/wos-starter-api.md)), and a
DOI-keyed store would have no slot for them. Because every work has an
OpenAlex-style id and therefore a stem, stem-keying gives every work a slot
uniformly — DOI-bearing or not. The export step still emits a DOI link for
works that have one (that is what a collaborator resolves), and records
`None` for works that do not, per "None means None"; the CSV always carries
the stem so import can match a returned PDF even when no DOI exists.

### Provenance sidecar

Alongside the PDFs, a single work-keyed provenance file
`data/pdfs/provenance.jsonl` records, per work, where its one PDF came from
or that it is still missing. A new model in
[fulltext_retrieval/models.py](../../src/laglitsynth/fulltext_retrieval/models.py):

```python
class PdfSource(str, Enum):
    oa = "oa"                      # OA URL on the Work record
    unpaywall = "unpaywall"        # Unpaywall best_oa_location
    zotero_import = "zotero-import"  # ingested from a Zotero-exported folder
    manual = "manual"              # ingested from a plain folder drop
    missing = "missing"            # no PDF yet; recorded so gaps are addressable


class PdfProvenanceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    stem: str                      # data/pdfs/<stem>.pdf
    doi: str | None                # None for DOI-less works
    source: PdfSource
    source_url: str | None         # the URL a PDF was fetched from, when applicable
    pdf_path: str | None           # "data/pdfs/<stem>.pdf" when source != missing, else None
    content_sha256: str | None     # of the PDF bytes; None when missing
    obtained_at: str               # ISO-8601 UTC of the record
```

This replaces the per-run `retrieval.jsonl` / `RetrievalRecord` as the
store's source of truth. `RetrievalStatus` collapses into `PdfSource`: the
old `retrieved_oa` / `retrieved_unpaywall` / `retrieved_manual` become
`oa` / `unpaywall` / `manual`, the new `zotero-import` is added, and
`abstract_only` / `failed` both become `missing` — the store does not
distinguish "never had a source" from "a download failed," because for the
shared store the only fact that matters downstream is whether a PDF exists.
Provenance is work-keyed and last-write-wins per work: re-running OA
retrieval, then importing a Zotero PDF for the same work, leaves one
`PdfProvenanceRecord` with `source = zotero-import`. The file is read on
startup into a `dict[str, PdfProvenanceRecord]` keyed by `work_id` and
rewritten atomically, the same shape today's `retrieval.jsonl` seeding uses.

`RetrievalMeta` keeps its role as the per-invocation meta sidecar
(`run: RunMeta` plus counters) but its counters are recomputed over
`PdfSource` values.

### Export step: `fulltext-retrieval-export`

A new subcommand resolves the works in the current selection that still
lack a PDF and writes a handoff bundle a collaborator can act on. Inputs
resolve the same way every manifest-aware stage resolves them
([run-manifest](../run-manifest.md)): explicit `--catalogue` /
`--screening-verdicts` win, else `latest_output` from the manifest. The
selection is the screening-gated active set —
[_active_works](../../src/laglitsynth/fulltext_retrieval/retrieve.py) is reused
verbatim — minus works that already have a non-`missing` provenance record.
It writes, into `--export-dir` (default `data/pdfs/export/`):

- `dois.txt` — one `https://doi.org/<doi>` per line, for works that have a
  DOI; this is the list a collaborator pastes into Zotero's "Add Item by
  Identifier" or a browser.
- `missing.ris` — an RIS file with one record per missing work (title,
  authors, year, DOI, journal), for import into any reference manager.
- `pdf-manifest.csv` — the round-trip key. Columns `work_id`, `stem`,
  `doi`, `expected_filename` (`<stem>.pdf`). This is what import reads to
  match returned PDFs back to works; it is written by us, not derived from
  Zotero, so matching never depends on what Zotero happens to name a file.

Works without a DOI appear in `pdf-manifest.csv` and `missing.ris` (DOI
column/tag empty) but not in `dois.txt` — there is no link to resolve, so a
collaborator handles them by title or skips them.

### Import step: `fulltext-retrieval-import`

A new subcommand ingests a returned folder of PDFs into `data/pdfs/`. It
takes `--import-dir` (the folder a collaborator shipped back), `--manifest`
(the `pdf-manifest.csv` from the matching export), and `--source`
(`zotero-import` | `manual`). Matching is by the explicit mapping, not the
filename:

1. Read `pdf-manifest.csv` into DOI→stem and stem→stem maps.
2. For each PDF in `--import-dir`, determine its target stem. Zotero's
   "Export Files" names attachments by item title, not by DOI, so a
   returned export's filenames cannot be assumed to carry the DOI; the
   source plan's open question deliberately left this unconfirmed against a
   real export, and round-tripping our own mapping makes import independent
   of whatever Zotero names a file. Import therefore resolves the stem in
   this order: (a) the file's own embedded DOI when present in the PDF
   metadata or first-page text and it appears in the manifest's DOI map;
   (b) a filename whose stem matches the manifest directly (the
   plain-folder-drop case, where the collaborator was asked to name files
   `<stem>.pdf`); (c) a sidecar `*.csv`/`*.json` the collaborator returned
   that maps their filenames to DOIs. A PDF that resolves to no manifest
   stem is reported and skipped, never guessed.
3. Validate PDF magic bytes (`%PDF`, reusing `_validate_pdf`); a
   non-PDF file is reported and skipped.
4. Dedup: if `data/pdfs/<stem>.pdf` already exists with the same
   `content_sha256`, skip; if it exists with a different hash, the existing
   PDF wins unless `--overwrite` is passed (one-PDF-per-work, last decision
   is the operator's, not an accident of import order).
5. Copy validated, matched, new PDFs into `data/pdfs/` and upsert each
   work's `PdfProvenanceRecord` with the given `--source`, the computed
   `content_sha256`, and `pdf_path`.

Import prints a summary: matched and copied, skipped (already present /
not a PDF / no manifest match), and the count of manifest works still
missing after this import.

### Automatic OA retrieval stays, rewired to the shared store

`fulltext-retrieval` keeps its OA-URL + Unpaywall logic unchanged in
substance — same `_try_oa_urls`, `_try_unpaywall`, `_RateLimiter`,
`_download_pdf` — but writes into `data/pdfs/<stem>.pdf` instead of a
per-run `--output-dir/pdfs/`, and records `PdfProvenanceRecord` rows
instead of `RetrievalRecord`. `--skip-existing` keys off provenance:
a work with a non-`missing` record is skipped. The `--manual-dir` path is
subsumed by `fulltext-retrieval-import --source manual` and is removed from
`fulltext-retrieval` — manual ingestion is now an explicit step with magic-
byte validation and dedup, not an inline copy. `unretrieved.txt` is
dropped; the export step supersedes it (the canonical "what is still
missing" view is `missing` provenance records, which export reads).

## Design decisions

The store is keyed on the work stem rather than the DOI so that DOI-less
works get a slot uniformly and the key never changes when a DOI is later
discovered; DOI is recorded as provenance and used as a *match hint* at
import, never as the filesystem name. One PDF per work (no versions) is
chosen because no downstream stage consumes a version distinction, and it
is precisely what keeps the per-attempt/version/resolver framework
deferrable — a single slot needs only a single provenance record. Import
matches via the export-written `pdf-manifest.csv` rather than trusting
Zotero's export filenames because "Export Files" names attachments by item
title, not by DOI, and the source plan never confirmed filename→DOI against
a real export; round-tripping our own mapping makes the match deterministic
and tool-agnostic instead of depending on unverified filename semantics.
Provenance is a separate work-keyed `provenance.jsonl` rather than folded
into the run manifest because it is store state (one row per work,
persistent, last-write-wins), whereas the manifest is lineage (append-only,
one row per stage invocation); the manifest still records each
retrieval/export/import invocation's input→output paths via `append_stage`,
pointing at `data/pdfs/` as output.

## Implementation sequence

Five commits. `pixi run typecheck` and `pixi run test` pass between each.

### 1. Provenance model and shared store helpers

Replace `RetrievalStatus` / `RetrievalRecord` in
[fulltext_retrieval/models.py](../../src/laglitsynth/fulltext_retrieval/models.py)
with `PdfSource` and `PdfProvenanceRecord`; keep `RetrievalMeta` with
counters recomputed over `PdfSource`. Add a `store.py` module with
`load_provenance(data_dir) -> dict[str, PdfProvenanceRecord]`,
`upsert_provenance(...)`, `store_pdf_path(data_dir, stem) -> Path`, and
`sha256_of(path)`. Tests in `tests/test_store.py`: provenance round-trips
through `write`/`load`; `upsert` is last-write-wins per work; a `missing`
record round-trips with `pdf_path=None` and `content_sha256=None`.

### 2. Rewire `fulltext-retrieval` onto the shared store

Point OA + Unpaywall retrieval at `data/pdfs/<stem>.pdf`; emit
`PdfProvenanceRecord` (sources `oa` / `unpaywall`, else `missing`) instead
of `RetrievalRecord`; make `--skip-existing` key off provenance; drop
`--manual-dir` and `unretrieved.txt`. Inputs stay explicit flags as today
(manifest wiring deferred, see build scope above). Tests: a retrieved work gets an `oa`/`unpaywall` record and a
PDF at the stem path; a sourceless work gets a `missing` record and no PDF;
`--skip-existing` skips works with a non-`missing` record; behaviour is
unchanged when no manifest exists.

### 3. `fulltext-retrieval-export`

Add the export subcommand: resolve the screening-gated selection (reuse
`_active_works`), subtract works with a non-`missing` provenance record,
and write `dois.txt`, `missing.ris`, `pdf-manifest.csv` into `--export-dir`.
Register it in [cli.py](../../src/laglitsynth/cli.py). Tests: only
PDF-less in-selection works are exported; a DOI-less work appears in the
CSV and RIS with empty DOI but is absent from `dois.txt`; the CSV's
`expected_filename` is `<stem>.pdf`; the RIS parses back to the right
titles/DOIs.

### 4. `fulltext-retrieval-import`

Add the import subcommand: read `pdf-manifest.csv`, resolve each PDF's
target stem by DOI→stem then stem-filename then a returned sidecar map,
validate magic bytes, dedup by `content_sha256`, copy matched PDFs into
`data/pdfs/`, and upsert provenance with `--source`. Register it in
[cli.py](../../src/laglitsynth/cli.py). Tests: a PDF matched by DOI lands at
its stem path with a `zotero-import` record; a `--source manual` filename-
stem match lands with a `manual` record; a non-PDF and an unmatched file
are skipped and reported; a byte-identical re-import is a no-op; a
differing PDF is skipped unless `--overwrite`.

### 5. Docs and roadmap

Rewrite [docs/fulltext-retrieval.md](../../docs/fulltext-retrieval.md) to
describe the persistent `data/pdfs/` store, the provenance file, and the
export→collaborator→import loop. Update the Stage 5 entries in
[docs/interfaces.md](../../docs/interfaces.md) that name the removed
`RetrievalRecord` / `RetrievalStatus`, `retrieval.jsonl`, `unretrieved.txt`,
and `--manual-dir` (the Stage 5 path table, the model registry, the
dataflow row, and the manual-download command block) so they describe
`PdfProvenanceRecord`, `data/pdfs/`, and the export/import subcommands
instead; link the new doc from [README.md](../../README.md). Move this plan to
[plans/done/](.) and update [roadmap.md](../roadmap.md).

## Follow-ups

The per-attempt log + version model + pluggable-resolver framework stays
deferred: it earns its complexity only if a downstream stage needs multiple
PDF versions per work or fine-grained per-attempt provenance, and the
one-PDF-per-work store above is the trigger-free baseline until then. A
Semantic Scholar `openAccessPdf` source could slot in beside OA/Unpaywall
as one more automatic `PdfSource`, but that overlaps the
[DOI → abstract lookup](doi-abstract-lookup.md) client and should land with
it, not here. Stage 6 (extraction) gaining a manifest entry that reads
`data/pdfs/` as its input is tracked in the
[run-manifest](../run-manifest.md) follow-ups.

## Risks

A collaborator's returned folder may name PDFs in a way none of the three
match strategies resolves (Zotero's per-item naming, embedded DOI absent,
no sidecar). Mitigation: import never guesses — an unresolved PDF is
reported by filename and skipped, and the post-import summary names how
many manifest works are still missing, so the gap is operator-visible and
the collaborator can be asked to return a filename→DOI sidecar.

## Critical files

- [src/laglitsynth/fulltext_retrieval/retrieve.py](../../src/laglitsynth/fulltext_retrieval/retrieve.py)
- [src/laglitsynth/fulltext_retrieval/models.py](../../src/laglitsynth/fulltext_retrieval/models.py)
- [src/laglitsynth/ids.py](../../src/laglitsynth/ids.py)
- [src/laglitsynth/io.py](../../src/laglitsynth/io.py)
- [src/laglitsynth/cli.py](../../src/laglitsynth/cli.py)
- [src/laglitsynth/catalogue_dedup/dedup.py](../../src/laglitsynth/catalogue_dedup/dedup.py)
- [plans/run-manifest.md](../run-manifest.md)
- [docs/explorations/zotero-retrieval.md](../../docs/explorations/zotero-retrieval.md)
- [docs/explorations/wos-starter-api.md](../../docs/explorations/wos-starter-api.md)
- [docs/explorations/running-the-pipeline.md](../../docs/explorations/running-the-pipeline.md)
