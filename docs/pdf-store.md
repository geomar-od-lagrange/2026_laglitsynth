# PDF store

`data/pdfs/` holds at most one PDF per work in the deduplicated catalogue.
It is the input to corpus assembly in
[pipeline-restructure.md](../plans/pipeline-restructure.md). Current
counts per source:

```sh
grep -o '"source":"[^"]*"' data/pdfs/provenance.jsonl | sort | uniq -c
```

The store lives in the `data/` submodule, the private repo
[willi-rath/2026_laglitsynth_data](https://git.geomar.de/willi-rath/2026_laglitsynth_data),
with the PDFs in Git LFS. It is private because it holds publisher copies
obtained through GEOMAR and Kiel University subscriptions. Setup is in the
[README](../README.md#setup).

## Layout

- `pdfs/<openalex short id>.pdf`: the PDF.
- `pdfs/provenance.jsonl`: one `PdfProvenanceRecord` per work (see
  [fulltext-retrieval.md](fulltext-retrieval.md)). `source` names the
  route. Works the pipeline tried without success have a `missing` record;
  other works without a PDF have no record.
- `pdfs/acquisition-routes.csv`: source and sha256 per PDF, with the
  source URL and original file name where they were recorded. It was
  written when the store was assembled; the CLI does not maintain it.

## Where the PDFs come from

| `source` | Route |
|---|---|
| `oa` | Open-access URLs: OpenAlex locations, Semantic Scholar `openAccessPdf`, arXiv |
| `unpaywall` | Unpaywall `best_oa_location`, where OpenAlex had nothing |
| `zotero-desktop` | Zotero desktop "Find Available PDF" on an RIS import |
| `institutional` | DOI → landing page → `citation_pdf_url` over an institution's network |
| `browser` | Zotero browser connector on doi.org tabs, or a PDF link clicked by hand |

`laglitsynth fulltext-retrieval` covers only part of `oa` (OpenAlex's best
open-access URL) and `unpaywall`. The rest of `oa` and all of
`institutional` came from one-off scripts outside the CLI. Zotero desktop
reaches publishers that refuse scripted requests, notably Wiley-hosted
journals (AGU, ASLO), MDPI, IEEE and Taylor & Francis.

Conference abstracts (EGU, AGU, AVISO/OSTST meeting abstracts) are not
pursued: there is no paper behind them.

## Adding PDFs

The order is Zotero desktop first, then the browser for what is left. Only
the operator writes the store: a collaborator on another network runs the
same procedure on their side and hands back a folder of PDFs, which the
operator imports (see the drop-zone rule in
[cross-machine.md](cross-machine.md#drop-zone-rule-for-collaborator-pdfs)).
Each PDF ends up as `data/pdfs/<openalex short id>.pdf` with a provenance
record, and the operator commits and pushes in `data/`. A work that
already has a PDF keeps it.

The CLI does not support this procedure end to end yet.
`fulltext-retrieval-export` writes an RIS file without `AN` and `UR`, and
`fulltext-retrieval-import` records only the sources `zotero-import` and
`manual`. Until step 4 of
[pipeline-restructure.md](../plans/pipeline-restructure.md) closes this,
the RIS export, the copy out of Zotero and the provenance records for
`zotero-desktop` and `browser` happen outside the CLI.

### Zotero desktop

Use a separate Zotero instance, never your own library. Give it its own
profile and data directory, and move its connector port off the default
23119 in the profile's `user.js`:

```js
user_pref("extensions.zotero.httpServer.port", 23120);
user_pref("extensions.zotero.httpServer.localAPI.enabled", true);
user_pref("extensions.zotero.sync.autoSync", false);
```

Start it with the debug output written to a file. `-ZoteroDebugText`
sends it to stdout, and macOS `open` redirects that. The paths must be
absolute:

```sh
Z=$PWD/zotero-batch
L=$Z/debug-$(date +%Y%m%d-%H%M).log
open -n --stdout "$L" --stderr "$L" -a /Applications/Zotero.app \
  --args -profile "$Z/profile" -no-remote -datadir "$Z/data" -ZoteroDebugText
```

Import an RIS file of the missing works. Per work it carries `TY`, `TI`,
`AU`, `PY`, `T2`, `DO`, `UR` (`https://doi.org/<doi>`, or the landing page
without a DOI) and the OpenAlex short id in `AN`. Import it with
File → Import, or POST it to `http://localhost:23120/connector/import`
with content type `application/x-research-info-systems`.

Apply the patch below, select all items, and run "Find Available PDF".
Afterwards, copy each item's child PDF to `<AN>.pdf`.

#### The 429 patch

Zotero 7 (10.0.9) runs "Find Available PDF" as one serial queue. Two of
its requests leave out `noRetryOnThrottle`: the landing-page request and
the download of a PDF found on that page. A 429 with `Retry-After: 900`
then holds the whole queue for 15 min, again and again. With the patch,
the queue parks only the throttling site and carries on.

Paste it into Tools → Developer → Run JavaScript after every start and
before the lookup. A queue that already hangs needs a restart; a new
lookup joins the pending queue.

```js
if (!Zotero.Attachments._origDownloadFile) {
  Zotero.Attachments._origDownloadFile = Zotero.Attachments.downloadFile;
}
Zotero.Attachments.downloadFile = function (url, path, options = {}) {
  return Zotero.Attachments._origDownloadFile.call(this, url, path, {
    ...options, noRetryOnThrottle: true, errorDelayMax: options.errorDelayMax ?? 7500,
  });
};
if (!Zotero.HTTP._origRequest) { Zotero.HTTP._origRequest = Zotero.HTTP.request; }
Zotero.HTTP.request = function (method, url, options = {}) {
  if (options && options.errorDelayMax === 0) { options = { ...options, noRetryOnThrottle: true }; }
  return Zotero.HTTP._origRequest.call(this, method, url, options);
};
"patched: downloadFile and HTTP.request hand 429/503 back to the queue";
```

#### Reading the log

- `HTTP GET <url>`: a request. Each one is logged twice, once when it is
  sent and once with its result.
- `failed with status code 429` (or `503`): a site is throttling.
- `Delaying request to <domain> for N ms`: the queue parked that site and
  moved on. This is normal.
- `Delaying N seconds for Retry-After`: the queue is blocked. With the
  patch applied this does not appear.

The PDFs attached so far are counted through the local API at
`http://localhost:23120/api/users/0/items`.

### Browser

Open doi.org tabs for the works still missing. Save each with the Zotero
connector, or click the PDF link. Items saved by the connector lose `AN`,
and downloads carry no work id at all. Match them to the catalogue by DOI,
then by title, and confirm the match against the PDF's first pages.
Record them with source `browser`.
