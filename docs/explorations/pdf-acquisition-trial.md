# PDF acquisition trial: open access, institutional scripts, Zotero, browser

A one-day spike on 23 November 2026. The goal was to get as many PDFs as
possible for the deduplicated catalogue (9,149 works in
[data/catalogue-dedup/deduplicated.jsonl](../../data/catalogue-dedup/deduplicated.jsonl)).
It also measured what each acquisition route adds. All of it ran on a
GEOMAR LAN laptop. The results are messy on purpose and live outside the
pipeline's data layout. They feed into step 4 of
[pipeline-restructure.md](../../plans/pipeline-restructure.md), the
export/import loop.

## Where everything is

Everything is under [data/scratch-retrieval/](../../data/scratch-retrieval/),
which is gitignored and local to this laptop. Each PDF is named
`<openalex short id>.pdf`.

| Path | Route | Provenance |
|---|---|---|
| `pdfs/` | `laglitsynth fulltext-retrieval` (OpenAlex OA URL + Unpaywall), run with `--pass-all` verdicts from `screening-abstracts/passall/` | `pdfs/provenance.jsonl` |
| `extra/` | [scripts/extra.py](../../data/scratch-retrieval/scripts/extra.py): every OpenAlex location, Semantic Scholar `openAccessPdf`, arXiv, Europe PMC | `extra/provenance.csv` |
| `paywalled-direct/` | [scripts/paywalled.py](../../data/scratch-retrieval/scripts/paywalled.py) `direct`: DOI → landing page → `citation_pdf_url`, from the GEOMAR LAN | `paywalled-direct/provenance.csv` |
| `zotero/` | PDFs copied out of the Zotero collection `zotero-sample-100` (desktop "Find Available PDF" and the browser connector) | `zotero/provenance.csv` (route column) |
| `manual/` | PDFs clicked by hand in the browser, copied from `~/Downloads` | `manual/provenance.csv` |
| `zotero-sample-100.ris` | 100 random open-access misses (seed 0), OpenAlex ID in `AN` | |
| `browser-sample-100.csv` | Another 100 open-access misses (seed 1), disjoint from the RIS sample | |
| `manual-todo.csv` | The 35 browser-sample works the Zotero connector did not get | |
| `*.log` | Run logs | |

[data/pdfs/](../../data/pdfs/) holds 185 PDFs from earlier pipeline runs
and was not touched.

## Results

### Automated routes

| Route | Works tried | PDFs |
|---|---|---|
| Pipeline retrieval, catalogue order forward | 1,060 | 444 |
| `extra.py`, catalogue order backward | 5,249 | 1,866 (OpenAlex non-primary locations 1,839, Semantic Scholar 25, arXiv 2) |
| `paywalled.py direct`, on `extra.py` misses with a DOI | 233 | 47 |

Together with the 185 PDFs already in `data/pdfs/`, 2,520 of the 6,396
works tried have a PDF (39%). The other 2,753 catalogue works were never
tried because time ran out.

`paywalled.py` mostly failed for reasons that say nothing about the
subscriptions:

- Landing pages without a `citation_pdf_url` (Elsevier among them).
- 403s on the first request (MDPI, OUP, Wiley/AGU, ACS, MIT Press, T&F).
  That is bot protection, not rate limiting: the 403s came even at 30 s
  per site.

The script stops trying a publisher after three refusals and does not get
around bot protection.

### Zotero and browser, on open-access misses with a DOI

| Route | Sample | PDFs | Time |
|---|---|---|---|
| Zotero desktop: RIS import, then automatic PDF retrieval | RIS sample, 100 | 34 | Unattended. Imported at 18:47:00; the 34 attachments arrived 18:47:21–18:52:27, and a second "Find Available PDF" run added nothing |
| Zotero browser connector on doi.org tabs | Browser sample, 100 | 65 | About 19 min of hands-on time (18:41–19:00, including the RIS import) |
| Clicking the PDF link by hand on the tabs still left | The remaining 35 of the browser sample | 9 more | About 7 min (19:04–19:11) |

The browser route, connector plus manual clicks, gave 74 of 100. About 12
works in that sample have no paper behind them: Zotero datasets, EGU and
AVISO abstracts, a Cassyni event, a review reply. So it got 74 of about
88 actual papers. Most of the browser PDFs come from subscription
publishers that the scripts could not get through, such as Wiley/AGU,
Elsevier, MDPI, IEEE, AMS, Springer, AIP and T&F. The GEOMAR
subscriptions do pay off, but only through a real browser session.

Still missing in the browser sample:

- APS (PRE, PRL);
- AIAA (three), SAE, ASME, AMPP;
- JoVE, CiCP, a Springer chapter, IEEE VIS;
- TSFP proceedings, an EPFL thesis, a DTIC report.

### Extrapolation (from one sample of 100 each)

Taking the 39% open-access rate and the 74% browser rate on misses with a
DOI (83% of misses have one), and counting misses without a DOI as zero,
the whole catalogue would reach about 76%. At about 26 min of hands-on
time per 100 misses, the roughly 4,600 misses with a DOI would need about
20 h of browser work, or about 13 h if Zotero desktop takes its share
unattended first. Filtering the export by screening score cuts this.

## Agreed acquisition order

1. Open access, automated (pipeline retrieval plus the extra sources).
2. Zotero desktop: import an RIS export of the misses and let Zotero
   retrieve PDFs.
3. Zotero browser connector on doi.org tabs for what is still missing.
4. Clicking PDF links by hand for the rest.

Steps 2–4 run on each collaborator's own network, because the GEOMAR and
university subscriptions differ. The handoff format follows from the
trial. The export is an RIS file with the OpenAlex ID in `AN` and the
DOI. Import matches returned PDFs by `AN`, then DOI, then title.
Connector-saved items lose `AN`, and a few have no DOI or carry a
different one (the Zenodo version DOI, or the published GMD paper for an
EGUsphere review reply).

## Open threads

- **Fill the gap:** about 2,800 catalogue works were never tried. Re-running
  `extra.py` skips what it already has.
- **University route:** never run, because the SSH tunnel was not open.
  With `ssh -D 1080 -N -f somw122@nesh-login.rz.uni-kiel.de` up, run
  `paywalled.py <repo> <deadline_epoch> socks`. Per the results above, a
  browser behind that network would do better than the script.
- **Zotero desktop failures:** what it tried and why it failed is
  unknown. Help → Debug Output Logging during a run would show the URLs.
- **Fold into the corpus:** none of these PDFs are in `data/pdfs/` or
  `data/corpus/` yet. That waits for the import side of
  [pipeline-restructure.md](../../plans/pipeline-restructure.md).
- **Thesis chapter files:** `~/Downloads` still holds the Caltech thesis
  chapter files (`JMendez_Thesis_0`–`4`). Only the full thesis was copied.
