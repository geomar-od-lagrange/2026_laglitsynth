# Zotero export probe: how returned PDFs carry their DOI

Investigative note for the Zotero-import open question in
[fulltext-retrieval-diversified.md](../../plans/fulltext-retrieval-diversified.md):

> How do PDFs returned from Zotero carry their DOI for matching -- via
> Zotero's export filename, or a CSV mapping we round-trip? Confirm
> against a real Zotero export before designing import.

The manual diversification path in that direction is: export the works
still missing a PDF as DOI links / RIS, hand them to a collaborator who
imports them into Zotero and runs "Find Available PDF" (using *their*
institutional access plus Unpaywall), then ships the attached PDFs back.
The import step must match each returned PDF to a work **by DOI**. This
probe checks whether the DOI survives that round-trip in a form import can
use, and where it lives.

This is a spike write-up, not an authoritative reference. Fill in the
"Findings" table after running the probe against a real local Zotero
library; until then it records only what the probe checks and why.

## What the probe checks

[scripts/probe_zotero.py](../../scripts/probe_zotero.py) talks to the
local Zotero 7 HTTP API (`http://localhost:23119`, read-only, no auth)
and, for one item that has both a DOI and a PDF attached via "Find
Available PDF", prints:

- the parent item's `itemType`, `title`, and resolved DOI (from the
  dedicated `DOI` field, falling back to a `DOI:` line in `extra`);
- for each PDF attachment child: its `filename`, `title`, `contentType`,
  `linkMode`, any DOI/url on the attachment itself, the local `file://`
  path (`links.enclosure.href`), and its `parentItem` linkage.

The question it settles is whether the **filename alone** encodes the DOI
(in which case import can match on filename) or whether the DOI is only on
the **parent item** (in which case import must follow the parent linkage
or round-trip a CSV mapping, as the plan's second option allows).

Run it locally:

```sh
pixi run python scripts/probe_zotero.py            # scan for a DOI+PDF item
pixi run python scripts/probe_zotero.py <itemKey>  # inspect a known parent
```

It requires a running Zotero 7 desktop with the local HTTP API enabled
(Settings -> Advanced -> "Allow other applications on this computer to
communicate with Zotero") and at least one DOI'd item carrying a
"Find Available PDF" attachment.

## What to record from a real run

Fill this in after running the probe. Run it against a few items from
different publishers, since filename conventions vary by source.

| Observation | Value |
|---|---|
| Parent `DOI` field populated? | _to record_ |
| DOI ever only in `extra`? | _to record_ |
| PDF attachment `filename` pattern | _to record_ |
| Does the filename contain the DOI? | _to record_ |
| DOI present on the attachment item itself? | _to record_ |
| `linkMode` of "Find Available PDF" attachments | _to record_ |
| `links.enclosure.href` shape (local `file://` path) | _to record_ |

## Implications for import (to confirm)

The expected outcome -- to verify, not assume -- is that the DOI lives on
the parent item and the PDF filename is publisher-derived rather than
DOI-encoded. If that holds, matching returned PDFs to works by DOI cannot
rely on the export filename alone; import either follows the
attachment-to-parent linkage on a Zotero export (e.g. an exported
collection with files) or round-trips a CSV mapping DOI ->
expected `<work-stem>.pdf` alongside the export, per the
[diversified-retrieval direction](../../plans/fulltext-retrieval-diversified.md).
Record which approach the real export supports here once the probe has
run, so the deferred import step can be designed against fact rather than
guess.
