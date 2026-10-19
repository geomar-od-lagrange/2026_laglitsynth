# Unscreened sample run and packed handoff

## Goal

Run stages 1, 2 and 5 on a seeded random sample of a multi-query
catalogue without screening first, and hand the still-missing works to
several collaborators as separate packs that record how they were made.
Screening is deferred, not dropped: the works keep their PDFs when they
are screened later.

## Non-goals

No change to how stage 5 selects work: it still joins the catalogue against
a verdicts file. The sample and the pass-all verdicts are ordinary inputs
to the existing join. No new runner knobs; the runner keeps running stage 3
for real.

## Target state

`catalogue-sample` (new, in
[catalogue_dedup/sample.py](../../src/laglitsynth/catalogue_dedup/sample.py))
reads a catalogue (`--input`, default `deduplicated.jsonl`) and writes a
seeded random sample of `--n` works, in catalogue order, to `--output`
(default `<data-dir>/catalogue-dedup/sample-<n>-seed<seed>.jsonl`) with a
`SampleMeta` sidecar (`run`, `input_path`, `input_count`, `n`, `seed`,
`output_count`). `--seed` is required so the choice is always recorded.
Downstream stages take the sample via their existing `--catalogue` flag.

`screening-abstracts --pass-all` writes one `ScreeningVerdict` per input
work with `relevance_score=None`, `reason="unscreened"`, no seed and no raw
response, and never contacts Ollama. `unscreened` joins the sentinel
vocabulary: stage 5 treats a `None` score as in scope, so every sampled
work is active. `ScreeningMeta.llm` becomes `LlmMeta | None` and is `None`
on a pass-all run; `criterion` stays empty. The run's `config.yaml` records
`pass_all: true`, so the run dir says what it is.

`fulltext-retrieval-export` gains `--packs N` (default 1) and always writes
`export-meta.json` next to the bundle. With `N > 1` the top-level bundle is
unchanged and complete, and `pack-1/` … `pack-N/` each hold a full bundle
for a contiguous near-equal slice of the selection, with a README line
saying which pack it is. Import keeps reading the top-level
`pdf-manifest.csv`, so it does not matter which pack a folder came back
from. `ExportMeta` records `run`, `git_commit` (None outside a git
checkout), `run_id` (None when `--screening-verdicts` was passed
explicitly), the resolved `catalogue` and `screening_verdicts` paths,
`screening_threshold`, `active_count`, `held_count`, `exported_count`,
`with_doi_count`, `packs` and `pack_sizes`.

## Implementation sequence

1. `catalogue-sample` subcommand, `SampleMeta` model, CLI registration.
   Tests: same seed gives the same sample, output keeps catalogue order,
   `n` larger than the catalogue returns everything, meta counts match.
2. `--pass-all` on `screening-abstracts`; `ScreeningMeta.llm` optional;
   `unscreened` added to the stage-5 docstring. Tests: pass-all writes an
   `unscreened` verdict per work with null score/seed/raw_response, meta
   has `llm: null`, Ollama preflight is not called; stage 5's
   `_active_works` yields an `unscreened` work.
3. `--packs` and `export-meta.json` on `fulltext-retrieval-export`. Tests:
   `--packs 1` writes no subdirs, `--packs 3` over 7 works gives sizes
   3/2/2 whose union is the top-level manifest, each pack README names its
   index, meta fields and counts, `run_id` None with an explicit verdicts
   path.
4. Docs: [catalogue-dedup.md](../../docs/catalogue-dedup.md),
   [screening-abstracts.md](../../docs/screening-abstracts.md),
   [fulltext-retrieval.md](../../docs/fulltext-retrieval.md),
   [interfaces.md](../../docs/interfaces.md), roadmap.

## Critical files

- [src/laglitsynth/catalogue_dedup/dedup.py](../../src/laglitsynth/catalogue_dedup/dedup.py)
- [src/laglitsynth/screening_abstracts/screen.py](../../src/laglitsynth/screening_abstracts/screen.py)
- [src/laglitsynth/screening_abstracts/models.py](../../src/laglitsynth/screening_abstracts/models.py)
- [src/laglitsynth/fulltext_retrieval/export.py](../../src/laglitsynth/fulltext_retrieval/export.py)
- [src/laglitsynth/fulltext_retrieval/retrieve.py](../../src/laglitsynth/fulltext_retrieval/retrieve.py)
- [src/laglitsynth/cli.py](../../src/laglitsynth/cli.py)
