# PDF store

[data/pdfs/](../data/pdfs/) holds one PDF per catalogue work for 5,936 of
the 9,149 works in the deduplicated catalogue (65 %), 28.4 GB in total.
It is the input to corpus assembly in
[pipeline-restructure.md](../plans/pipeline-restructure.md).

The store lives in the `data/` submodule, the private repo
[willi-rath/2026_laglitsynth_data](https://git.geomar.de/willi-rath/2026_laglitsynth_data),
with the PDFs in Git LFS. It is private because it holds publisher copies
obtained through GEOMAR and Kiel University subscriptions. Setup is in the
[README](../README.md#setup).

## Layout

- `pdfs/<openalex short id>.pdf`: the PDF.
- `pdfs/provenance.jsonl`: one `PdfProvenanceRecord` per work (see
  [fulltext-retrieval.md](fulltext-retrieval.md)). `source` names the
  route. Works the pipeline tried without success have a `missing` record
  (184); the remaining works without a PDF have no record.
- `pdfs/acquisition-routes.csv`: source URL, original file name and
  sha256 per PDF.

## Where the PDFs come from

| `source` | PDFs | Route |
|---|---|---|
| `oa` | 3,765 | Open-access URLs: OpenAlex locations, Semantic Scholar `openAccessPdf`, arXiv |
| `unpaywall` | 17 | Unpaywall `best_oa_location`, where OpenAlex had nothing |
| `zotero-desktop` | 1,470 | Zotero desktop "Find Available PDF" on an RIS import, GEOMAR network |
| `institutional` | 534 | DOI → landing page → `citation_pdf_url`, scripted once over the GEOMAR and Kiel University networks |
| `browser` | 150 | Zotero browser connector on doi.org tabs, or a PDF link clicked by hand, GEOMAR network |

Of the open-access PDFs, 631 come from `laglitsynth fulltext-retrieval`
(OpenAlex's best open-access URL). The other 3,134 come from a sweep over
every OpenAlex location of a work (3,089), Semantic Scholar (43) and arXiv
(2).

Zotero desktop reaches publishers that refuse scripted requests. Of the
1,101 PDFs from its last run, 419 came from Wiley (AGU included), 207 from
MDPI, 52 from IEEE and 29 from Taylor & Francis. Zotero's own open-access
lookup added little beyond OpenAlex. That run took about 4.5 h unattended
for 4,220 imported works.

The browser got 76 of a random 100 works that every other route had
missed, in about 24 min of hands-on time.

Conference abstracts (EGU, AGU, AVISO/OSTST meeting abstracts) are not
pursued: there is no paper behind them.

## Adding PDFs

The order is Zotero desktop first, then the browser for what is left. A
collaborator on another network follows the same procedure. Each PDF ends
up as `data/pdfs/<openalex short id>.pdf` with a provenance record, and is
committed and pushed in `data/`. A work that already has a PDF keeps it.

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
