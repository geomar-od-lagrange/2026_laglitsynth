"""Manual probe of the local Zotero 7 HTTP API for PDF-to-DOI matching.

NOT pipeline code. This is a throwaway probe the maintainer runs LOCALLY,
by hand, against a *running* Zotero 7 desktop application exposing its
built-in HTTP API at http://localhost:23119. It makes no changes; the
local API is read-only.

It answers the Zotero-import open question in
plans/fulltext-retrieval-diversified.md: when a collaborator attaches a PDF
via "Find Available PDF", how is that PDF *named*, and how is its parent
item's *DOI* exposed -- so a future import step can match returned PDFs
back to works by DOI. The two candidate match keys the diversified
retrieval direction names are (a) the export filename and (b) a CSV
mapping we round-trip; this probe inspects (a) and the parent-item DOI so
we know whether (a) alone is enough.

Prerequisites:
    - Zotero 7 desktop is running with the local HTTP API enabled
      (Settings -> Advanced -> "Allow other applications on this computer
      to communicate with Zotero").
    - At least one item in the library has a DOI AND a PDF attachment
      added via "Find Available PDF". Pass its item key as the first
      argument, or let the probe scan for the first such item.

Usage:
    pixi run python scripts/probe_zotero.py            # scan for a DOI+PDF item
    pixi run python scripts/probe_zotero.py <itemKey>  # inspect a known parent

Record what it prints into docs/explorations/zotero-export-probe.md.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

# Local Zotero HTTP API. No auth; read-only. The /api prefix mirrors the
# Zotero web API v3, scoped to the local user library at /users/0.
BASE = "http://localhost:23119/api"
LIBRARY = "/users/0"


def _client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=30.0)


def _get_json(c: httpx.Client, path: str, **params: Any) -> Any:
    """GET a local-API path and return parsed JSON, failing loudly.

    Always returns JSON; do not pass format=keys/versions here, those
    response formats are plain text and json() would choke on them.
    """
    r = c.get(path, params=params or None)
    if r.status_code != 200:
        raise SystemExit(
            f"HTTP {r.status_code} for {path} -- is Zotero running with the "
            f"local HTTP API enabled? Body: {r.text[:200]}"
        )
    return r.json()


def _doi_of(item: dict[str, Any]) -> str | None:
    """Pull a DOI out of an item's data, trying the dedicated field first.

    Zotero stores a DOI in data['DOI'] for types that have the field
    (journalArticle, preprint, ...). Some items carry it only in 'extra'
    as a 'DOI: ...' line instead; check there as a fallback.
    """
    data = item.get("data", {})
    doi = data.get("DOI")
    if doi:
        return str(doi)
    extra = data.get("extra") or ""
    for line in extra.splitlines():
        if line.strip().lower().startswith("doi:"):
            return line.split(":", 1)[1].strip()
    return None


def _find_doi_pdf_item(c: httpx.Client) -> str | None:
    """Scan top-level items for the first one with a DOI and a PDF child."""
    items = _get_json(c, f"{LIBRARY}/items/top", limit=100, format="json")
    for item in items:
        key = item.get("key")
        if not key or not _doi_of(item):
            continue
        children = _get_json(c, f"{LIBRARY}/items/{key}/children")
        if any(_is_pdf_attachment(ch) for ch in children):
            return str(key)
    return None


def _is_pdf_attachment(child: dict[str, Any]) -> bool:
    data = child.get("data", {})
    return (
        data.get("itemType") == "attachment"
        and data.get("contentType") == "application/pdf"
    )


def inspect(c: httpx.Client, parent_key: str) -> None:
    """Print the parent's DOI and every PDF child's naming + file path."""
    parent = _get_json(c, f"{LIBRARY}/items/{parent_key}")
    pdata = parent.get("data", {})
    doi = _doi_of(parent)

    print("=== PARENT ITEM ===")
    print(f"  key:       {parent_key}")
    print(f"  itemType:  {pdata.get('itemType')}")
    print(f"  title:     {pdata.get('title')}")
    print(f"  DOI field: {pdata.get('DOI')!r}")
    print(f"  DOI (resolved, incl. extra fallback): {doi!r}")
    print(f"  url:       {pdata.get('url')!r}")

    children = _get_json(c, f"{LIBRARY}/items/{parent_key}/children")
    pdfs = [ch for ch in children if _is_pdf_attachment(ch)]
    print(f"\n=== PDF ATTACHMENTS ({len(pdfs)}) ===")
    if not pdfs:
        print("  none -- attach a PDF via 'Find Available PDF' and retry.")
        return

    for ch in pdfs:
        cdata = ch.get("data", {})
        links = ch.get("links", {})
        enclosure = (links.get("enclosure") or {})
        print(f"\n  attachment key:   {ch.get('key')}")
        # 'filename' is what Zotero named the stored PDF on disk -- the
        # primary candidate match key for import-by-filename.
        print(f"  filename:         {cdata.get('filename')!r}")
        print(f"  title:            {cdata.get('title')!r}")
        print(f"  contentType:      {cdata.get('contentType')!r}")
        print(f"  linkMode:         {cdata.get('linkMode')!r}")
        # The attachment's own DOI/url is usually empty; the DOI lives on
        # the PARENT. This is the thing import must rely on: filename does
        # NOT encode the DOI, so matching needs the parent linkage.
        print(f"  attachment DOI:   {cdata.get('DOI')!r}")
        print(f"  attachment url:   {cdata.get('url')!r}")
        # links.enclosure.href is the local file:// path to the PDF bytes;
        # links.enclosure.title is often the on-disk filename too.
        print(f"  file href:        {enclosure.get('href')!r}")
        print(f"  enclosure title:  {enclosure.get('title')!r}")
        print(f"  parentItem:       {cdata.get('parentItem')!r}")

    print("\n=== MATCHING NOTE ===")
    print(
        "  The DOI is on the PARENT item, not the attachment, and the PDF\n"
        "  filename is publisher-derived, not the DOI. So a DOI<->PDF match\n"
        "  on a Zotero *export* must round-trip the parent linkage (or a CSV\n"
        "  mapping), not rely on the filename alone. Confirm this holds for\n"
        "  several 'Find Available PDF' items before designing import."
    )


def main() -> None:
    with _client() as c:
        # Sanity-check the API is reachable before doing anything else.
        # Use the default JSON response: _get_json parses JSON, so a
        # plain-text format like format=keys would make it raise here.
        _get_json(c, f"{LIBRARY}/items/top", limit=1)
        if len(sys.argv) > 1:
            parent_key = sys.argv[1]
        else:
            print("No item key given; scanning for a DOI+PDF item...", file=sys.stderr)
            found = _find_doi_pdf_item(c)
            if not found:
                raise SystemExit(
                    "No top-level item with both a DOI and a PDF attachment "
                    "found in the first 100 items. Add a DOI'd item, attach a "
                    "PDF via 'Find Available PDF', and pass its key explicitly."
                )
            print(f"Using discovered item key: {found}", file=sys.stderr)
            parent_key = found
        inspect(c, parent_key)


if __name__ == "__main__":
    main()
