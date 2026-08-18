# Collaborator handoff loop

## Goal

Make the DOI handoff something an operator runs rather than assembles.
`fulltext-retrieval-export` and `fulltext-retrieval-import` already carry the
mechanism, but the export is invoked by hand with four flags, and the bundle
it writes assumes the reader knows what to do with three unlabelled files.
This plan puts the export in the runner and gives the bundle enough context
for a collaborator to act on it without a covering e-mail. It lands on top of
the [run manifest](run-manifest.md), which reduces the export invocation to
`--data-dir`.

## Target state

### Bundle contents

The export writes into `<data-dir>/pdfs/export/`. Today it writes three
files: `dois.txt` with one resolver URL per DOI-bearing work, `missing.ris`
with one RIS record per work, and `pdf-manifest.csv` with `work_id`, `stem`,
`doi`, and `expected_filename`. A work without a DOI reaches the RIS and the
manifest, but not `dois.txt`, and nothing in the bundle says so.

Three additions:

- `pdf-manifest.csv` gains `title` and `year` columns, after `doi`. The
  export already has both values — it writes them into `missing.ris`. Import
  gains nothing from them yet; they are the precursor the
  [contributor PDF matching note](../docs/explorations/contributor-pdf-matching.md)
  names for title matching, and they let a person read the manifest.
- `no-doi.csv` lists the works that `dois.txt` cannot represent, with
  `work_id`, `stem`, `title`, `year`, and `expected_filename`. These are the
  works a collaborator has to search for by title. The file is written even
  when empty, so its absence never has to be interpreted.
- `README.md` states what the bundle is, what to do with each file, and what
  to send back. It names the two return paths the importer accepts: a folder
  of PDFs named `<stem>.pdf`, or a folder of arbitrarily-named PDFs, which
  import resolves by the DOI embedded in each file. It is written from a
  template in the export module, with the work count and the export date
  filled in.

### Runner step

[`scripts/run-pipeline.sh`](../scripts/run-pipeline.sh) gains an export step
after stage 5, gated on `EXPORT_GAP` (default `0`). With `EXPORT_GAP=1` it
runs `fulltext-retrieval-export --data-dir "$ROOT"` under the same
`run_stage` gating as every other stage, so `STOP_AFTER_STAGE=5 EXPORT_GAP=1`
is the whole "screen the corpus, then hand me the gap" run. The knob follows
the established env > config > default precedence, with `export_gap` added to
[`ReviewConfig`](../src/laglitsynth/review.py).

Import stays a manual invocation. It takes a path to a folder a collaborator
returned, which no config file can know in advance, and it is run once per
return rather than once per pipeline run.

## Design decisions

The export writes a `README.md` into the bundle rather than the repo
documenting the bundle, because the bundle travels and the repo does not. A
collaborator receives a directory over e-mail or a shared drive, with no
checkout and no context.

`no-doi.csv` is a separate file rather than a column in `pdf-manifest.csv`
because the two are read by different readers. The manifest is the
round-trip key that import parses; `no-doi.csv` is a worklist for a person.
Filtering the manifest by an empty `doi` column would work and would be one
file fewer, but it asks the collaborator to do the filtering.

## Implementation sequence

Three commits. `pixi run typecheck` and `pixi run test` pass between each.

1. `title` and `year` columns on `pdf-manifest.csv`, in
   [export.py](../src/laglitsynth/fulltext_retrieval/export.py); `year` is
   empty for a work with no `publication_year`. Update `read_manifest` and
   `ManifestEntry` in
   [import_.py](../src/laglitsynth/fulltext_retrieval/import_.py) to accept
   the new columns without using them yet. Tests: the manifest round-trips
   through `read_manifest` with the new columns present; a work with a null
   title or year writes an empty cell rather than the string `None`.
2. `no-doi.csv` and the bundle `README.md`. Tests: a selection with no
   DOI-less works writes an empty `no-doi.csv` with its header row; a
   DOI-less work appears there and not in `dois.txt`; the README names the
   work count the export reported.
3. The `EXPORT_GAP` runner step and `export_gap` on `ReviewConfig`, plus the
   handoff section in
   [fulltext-retrieval.md](../docs/fulltext-retrieval.md) and the knob in
   [configs.md](../docs/configs.md). Test: `review-config` emits
   `CFG_EXPORT_GAP` for a config that sets it.

## Follow-ups

Import tiers — reading the DOI from XMP metadata and from title-page link
annotations — wait on a real round-trip, per the
[contributor PDF matching note](../docs/explorations/contributor-pdf-matching.md).
Title matching waits on the same evidence and on the columns commit 1 adds.

## Critical files

- [src/laglitsynth/fulltext_retrieval/export.py](../src/laglitsynth/fulltext_retrieval/export.py)
- [src/laglitsynth/fulltext_retrieval/import_.py](../src/laglitsynth/fulltext_retrieval/import_.py)
- [src/laglitsynth/review.py](../src/laglitsynth/review.py)
- [scripts/run-pipeline.sh](../scripts/run-pipeline.sh)
- [docs/fulltext-retrieval.md](../docs/fulltext-retrieval.md)
- [docs/configs.md](../docs/configs.md)
- [docs/explorations/contributor-pdf-matching.md](../docs/explorations/contributor-pdf-matching.md)
