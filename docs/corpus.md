# Corpus

`data/corpus/` is where the two halves of the [pipeline](pipeline.md)
meet. The first half (fetch, dedup, screening, retrieval and the
collaborator export/import loop) finds the literature and acquires the
PDFs; `corpus-assemble` selects the works and copies their PDFs into the
corpus; the analysis half (stages 6–8) reads only the corpus. The corpus
is the durable product of acquisition, and it is useful on its own,
independent of any analysis built on it.

## Prerequisites

- Inputs: the deduplicated catalogue,
  `<data-dir>/catalogue-dedup/deduplicated.jsonl` (`--catalogue`
  overrides); stage 3's `verdicts.jsonl` with `screening-meta.json` beside
  it, under the run-id in `<data-dir>/manifest.json` (`--screening-run-id`
  or `--screening-verdicts` override); the PDF store `<data-dir>/pdfs/`
  with its Git LFS content pulled (see [pdf-store.md](pdf-store.md)).
  `--screening-threshold` is required.
- Services: none.
- Credentials: none.

## What it holds

The corpus is self-contained: it holds copies, not links into the PDF
store, so it does not depend on the store staying unchanged.

| Path | Model | Contents |
|---|---|---|
| `data/corpus/works.jsonl` | [`Work`](../src/laglitsynth/catalogue_fetch/models.py) | The selected catalogue works |
| `data/corpus/verdicts.jsonl` | [`ScreeningVerdict`](../src/laglitsynth/screening_abstracts/models.py) | Their screening verdicts; none for works included by `--allow-unscreened` |
| `data/corpus/pdfs/<stem>.pdf` | (binary) | Copies of the store PDFs of the selected works |
| `data/corpus/provenance.jsonl` | [`CorpusPdf`](../src/laglitsynth/corpus/models.py) | One row per selected work, works without a PDF included |
| `data/corpus/corpus-meta.json` | [`CorpusMeta`](../src/laglitsynth/corpus/models.py) | How the corpus was made |

A `CorpusPdf` row is `{work_id, pdf_path, content_sha256, store_record}`:
`pdf_path` is `pdfs/<stem>.pdf` relative to the corpus directory and
`None` for a work without a PDF, `content_sha256` is the hash of the
copy, and `store_record` is the work's
[`PdfProvenanceRecord`](../src/laglitsynth/fulltext_retrieval/models.py)
in the store (`None` when the store has none). Works without a PDF stay
in the corpus: the gap is part of what the review reports.

`CorpusMeta` records the inputs (`catalogue`, `screening_verdicts`,
`screening_run_id`), the screening LLM (`screening_llm`, copied from the
screening meta), the `screening_threshold`, the counts
(`catalogue_count`, `selected_count`, `unscored_count`,
`below_threshold_count`, `no_verdict_count`, `with_pdf_count`), and the
`corpus_id` and `analysis_run_id` described below.

## Assembling it

```sh
laglitsynth corpus-assemble \
    [--catalogue data/catalogue-dedup/deduplicated.jsonl] \
    [--screening-verdicts data/screening-abstracts/<run-id>/verdicts.jsonl] \
    [--screening-run-id ID] --screening-threshold FLOAT \
    [--allow-unscreened] [--data-dir DIR]
```

`corpus-assemble` is the only place the screening threshold is applied.
It selects every catalogue work whose verdict scores at least
`--screening-threshold`, and every work whose verdict has no score (no
abstract, LLM timeout, rejection or parse failure), because stage 7
still judges those on the full text. The threshold is required; the
runner passes it from `screening_threshold` in the review config or
`SCREENING_THRESHOLD`. The screening run-id defaults to the one in
`data/manifest.json` (see [run-manifest.md](run-manifest.md)).

The command exits without writing anything when a verdict row fails
validation, when `screening-meta.json` beside the verdicts is missing or
does not validate, or when catalogue works have no verdict row at all.
The last case is usually a screening run that died or a `--max-records`
smoke run; `--allow-unscreened` includes such works instead, which is
how an unscreened or partly screened catalogue is assembled.

PDFs are copied from the store `data/pdfs/` for every selected work
whose store record is not `missing`. A record whose PDF file is absent
stops the command, naming the file. A file without the `%PDF` magic
bytes, usually an unfetched Git LFS pointer, stops it with the advice to
run `git lfs pull` in `data/` (see [pdf-store.md](pdf-store.md)).

The corpus is always rewritten whole. The command removes any leftover
`data/corpus.tmp/`, writes into a fresh one, and then replaces
`data/corpus/` with it. Analysis outputs live outside the corpus in
their own stage directories, so re-assembling never touches them.

## Identity

`corpus_id` is the sha256 of the lines `work_id<TAB>content_sha256`
(empty after the tab for a work without a PDF), sorted by work id and
joined with newlines. Two assemblies with the same members and the same
PDF bytes share an id. The id ignores catalogue order, the threshold and
the time of assembly; it changes with membership or with any PDF's
bytes.

Every analysis output records the `corpus_id` it ran on, and the stages
check it:

- Stage 6 writes `corpus_id` into `extraction-meta.json` and the corpus
  copy's hash into each `ExtractedDocument.pdf_sha256`. With
  `--skip-existing` it re-extracts a work whose PDF changed.
- Stage 7 exits when `extraction-meta.json` names another corpus, uses
  an extraction only when its `pdf_sha256` matches the corpus copy, and
  writes `corpus_id` into `eligibility-meta.json`.
- Stage 8 applies the same extraction checks, exits when
  `eligibility-meta.json` is missing or names another corpus or another
  `prompt_sha256` than its own `--eligibility-criteria`, and writes
  `corpus_id` into `extraction-codebook-meta.json`.
- With `--skip-existing`, stages 7 and 8 refuse to resume a run whose
  own meta names another corpus.

Stale output is therefore detected, never silently mixed in.

## The analysis run-id

The screening run-id (in `data/manifest.json`) names the first half's
stage-3 run. Stages 7 and 8 write under a separate analysis run-id,
`CorpusMeta.analysis_run_id`, which their `--run-id` defaults to.
`corpus-assemble` keeps the previous `analysis_run_id` while the
`corpus_id` is unchanged and mints a fresh one otherwise. A changed
corpus therefore gets new analysis directories and earlier output is
never rewritten, while an identical re-assembly lets `--skip-existing`
resume a crashed run. `--run-id` on stages 7 and 8 still names a variant
run on the same corpus, for example with other criteria.

## One branch per paper

Once the corpus is assembled, each paper moves through analysis on its
own. No analysis stage looks across papers, and no per-paper record is
rewritten from cross-paper results; the planned synthesis step is the
only aggregation. Cross-paper work (deduplication, import matching,
vocabulary term frequencies) happens before the corpus.

The readers stages 6–8 share are in
[`corpus/load.py`](../src/laglitsynth/corpus/load.py).
