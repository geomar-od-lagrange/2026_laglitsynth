# Zotero ecosystem for DOI → metadata (with abstracts) and PDF retrieval

Investigation into whether the pipeline should reuse Zotero's machinery
for resolving a DOI to bibliographic metadata (including **abstracts**)
and for retrieving open-access PDFs — by vendoring code, running a Zotero
service, or installing a package — versus calling REST APIs directly.
Prompted by the finding that the [Web of Science Starter
API](wos-starter-api.md) returns DOIs but no abstracts and no PDFs.
Researched May 2026. Desk research; the translation-server JSON shapes
below were read from docs, not a live run.

## Recommendation

**Do not vendor Zotero code and do not run translation-server as a
sidecar.** Stay with thin httpx + Pydantic wrappers over REST APIs. The
pipeline already does OA-URL + Unpaywall retrieval in
[`fulltext_retrieval/retrieve.py`](../../src/laglitsynth/fulltext_retrieval/retrieve.py)
with the `RetrievalStatus` / `RetrievalRecord` models (which already
include an `abstract_only` status). The only worthwhile additions are a
**Semantic Scholar** lookup (best single DOI → abstract source), with
**OpenAlex** as fallback and optionally **Crossref** content negotiation.
Zotero's tools give Crossref-class abstracts wrapped in an AGPL
Node/Docker service — no capability we can't get directly, at the cost of
a runtime that clashes with a clean typed-Python pixi project.

## Zotero translators and translation-server

[zotero/translators](https://github.com/zotero/translators) are ~600+
**JavaScript** site/metadata scrapers (one `.js` per source: ACM, IEEE,
arXiv, PubMed, Crossref, generic Embedded Metadata, …), each exposing
`detectWeb`/`doWeb` or `detectSearch`/`doSearch`. They run only inside a
sandbox the Zotero client supplies (DOM, `Zotero.Item`, HTTP shims) — you
cannot `import` one standalone. Licensed **AGPL-3.0** (the file is named
`COPYING`, so GitHub's detector misreports it as unknown, but the project
license is unambiguous).

[zotero/translation-server](https://github.com/zotero/translation-server)
is the official **Node.js** service providing that sandbox, deployed via
Docker / `npm start` / Lambda. Also **AGPL-3.0**. Endpoints:

- `POST /web` — body is a URL; runs web translators → Zotero-API-JSON.
- `POST /search` — body is an **identifier** (DOI, ISBN, PMID, arXiv):
  ```
  curl -d '10.2307/4486062' -H 'Content-Type: text/plain' \
       http://127.0.0.1:1969/search
  ```
- `POST /export?format=csljson|ris|bibtex` — convert items.
- `POST /import` — parse RIS/BibTeX/… into Zotero JSON.

Zotero JSON carries an `abstractNote` field (→ CSL-JSON `abstract` on
export), so it *can* return abstracts — but only as good as the source it
scrapes. For a bare DOI, `/search` routes through Crossref-style search
translators, so the abstract is essentially the **Crossref** abstract: no
more complete than calling Crossref yourself.

## DOI → metadata with abstracts: realistic sources

Zotero's `/search` has no secret source; it hits the same public APIs.
Compared directly from a DOI:

| Source | Abstract? | Notes |
|---|---|---|
| Crossref REST (`api.crossref.org/works/{doi}`) | Sometimes | `abstract` is **JATS-XML** only when the publisher deposited one; coverage is partial, varies by publisher/discipline. Strip JATS tags for plain text. |
| Semantic Scholar Graph (`/graph/v1/paper/DOI:{doi}`) | Often | Plain-text `abstract`; also `openAccessPdf`. Public, no key (free key raises limits). Best single DOI → abstract source. Some abstracts withheld for licensing. |
| OpenAlex (already used via `pyalex`) | Yes (inverted index) | `abstract_inverted_index` → reconstruct. Broad coverage; already in the pipeline (see [`catalogue_fetch`](../../src/laglitsynth/catalogue_fetch/fetch.py)). |
| Europe PMC | Yes (`abstractText`) | Life-sciences/biomed only. |
| DataCite | Rarely | `descriptions[]` with `descriptionType="Abstract"`; only DataCite DOIs (datasets/theses), not journal articles. |

Translation-server is **not** better than calling these directly — it
yields Crossref-quality abstracts wrapped in a Node service.

## PDF / open-access retrieval

Zotero "Find Available PDF" (per the [Zotero
blog](https://www.zotero.org/blog/improved-pdf-retrieval-with-unpaywall-integration/)):
load the DOI/URL page and scrape a PDF link (needs institutional/VPN
access), then for OA copies try **Unpaywall → Semantic Scholar → DOI
content negotiation**, in that order; custom JSON PDF resolvers are also
supported. Same strategy the pipeline already implements. Standalone
Python options:

| Tool | License | Maintained | Notes |
|---|---|---|---|
| Unpaywall REST (`api.unpaywall.org/v2/{doi}?email=`) | Free service, CC0 data | Yes | `best_oa_location.url_for_pdf`. The right primary source; already called. |
| `unpywall` (PyPI) | MIT | Yes | Thin wrapper (`get_pdf_link`, `get_json`); needs `UNPAYWALL_EMAIL`; pulls in pandas. |
| `pyalex` | MIT | Yes (already a dep) | OA locations + abstract inverted index. |
| `paperscraper` | MIT | Yes | Metadata + PDF/XML from PubMed, arXiv, bioRxiv/medRxiv/chemRxiv; preprint-biased. |
| `scidownl` / `PyPaperBot` | MIT | Patchy | Route through **Sci-Hub** — copyright-infringing. **Do not use.** |

Maintained + permissive + legitimate: Unpaywall (direct or `unpywall`),
pyalex, paperscraper. Avoid the Sci-Hub tools.

## pyzotero is a different thing

[`pyzotero`](https://github.com/urschrei/pyzotero) (Blue Oak Model
License, actively maintained) is a client for the **Zotero Web API / a
user's personal or group library** — read/write items, collections,
attachments, exports. It is **not** a DOI-lookup or translation engine and
cannot resolve arbitrary DOIs to metadata or abstracts. (The local
[Zotero skill](../../.claude) in this repo is likewise scoped to *your*
library, not arbitrary DOIs.) Confusing it with translation-server is the
common trap; they solve different problems. There is **no maintained
pure-Python port** of the JS translators — tools that need translator
output shell out to the Node service.

## Why not vendor / run the AGPL service

- **License.** Translators and translation-server are AGPL-3.0. Vendoring
  that JS into this repo would pull AGPL's network-copyleft (§13) over the
  project. Running it as a separate Docker sidecar keeps our code clear of
  that, but injects a Node/Docker dependency into an otherwise clean
  pixi/typed-Python pipeline plus container lifecycle overhead — and we'd
  re-validate its JSON into Pydantic anyway.
- **No capability gain.** Its DOI abstracts come from Crossref-class
  sources we can call ourselves; its PDF logic mirrors the
  Unpaywall/S2/content-negotiation path we already run.

## Concrete next steps (if pursued)

- **Abstracts from DOI:** a small typed Semantic Scholar client
  (`GET /graph/v1/paper/DOI:{doi}?fields=title,abstract,openAccessPdf,externalIds`)
  as primary, OpenAlex as fallback, optional Crossref content negotiation
  (strip JATS) last. Plain httpx + Pydantic; reconstruct inverted index /
  strip JATS at ingest per the "plain text over clever encodings"
  principle. Feeds works whose abstract is missing (e.g. WoS-only records).
- **PDFs:** keep the current Unpaywall + OA-URL path in
  [`retrieve.py`](../../src/laglitsynth/fulltext_retrieval/retrieve.py).
  `unpywall` (MIT) is a fine packaged helper, but a direct httpx call to
  `api.unpaywall.org/v2/{doi}?email=` reading `best_oa_location.url_for_pdf`
  is simpler and avoids the pandas dependency.

### Example payloads

Semantic Scholar (DOI → abstract + OA PDF):
```
GET https://api.semanticscholar.org/graph/v1/paper/DOI:10.1038/nphys1170?fields=title,abstract,openAccessPdf,externalIds
→ { "title": "...", "abstract": "Plain-text abstract ...",
    "openAccessPdf": {"url": "https://.../paper.pdf", "status": "GREEN"},
    "externalIds": {"DOI": "10.1038/nphys1170"} }
```
Unpaywall (DOI → best OA PDF):
```
GET https://api.unpaywall.org/v2/10.1038/nphys1170?email=you@example.org
→ { "is_oa": true, "best_oa_location": { "url_for_pdf": "https://.../paper.pdf",
     "host_type": "repository", "version": "publishedVersion" }, ... }
```

## Uncertainty flags

- Per-DOI abstract presence from translation-server depends on the
  underlying scrape (Crossref); not verified with a live `curl`.
- Crossref and Semantic Scholar abstract coverage is genuinely partial and
  varies by publisher/discipline/licensing; the fraction for any specific
  corpus is unmeasured. OpenAlex remains the broadest fallback already in
  the pipeline.

## Sources

- [zotero/translators](https://github.com/zotero/translators), [zotero/translation-server](https://github.com/zotero/translation-server) (AGPL-3.0)
- [Zotero blog — Unpaywall PDF retrieval](https://www.zotero.org/blog/improved-pdf-retrieval-with-unpaywall-integration/)
- [Semantic Scholar Graph API](https://api.semanticscholar.org/api-docs/), [Unpaywall API](https://unpaywall.org/products/api), [Crossref REST API](https://api.crossref.org/)
- [pyzotero](https://github.com/urschrei/pyzotero), [unpywall](https://pypi.org/project/unpywall/), [paperscraper](https://github.com/PhosphorylatedRabbits/paperscraper)
