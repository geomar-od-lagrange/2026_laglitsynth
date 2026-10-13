# Collaborator handoff loop

## Goal

Make the DOI handoff something an operator runs rather than assembles:
`fulltext-retrieval-export` from the runner, and a bundle a collaborator can
act on without a covering e-mail. Lands on the [run manifest](run-manifest.md),
which reduces both handoff subcommands to `--data-dir`.

## Target state

`pdf-manifest.csv` gains `title` and `year` columns so a person can read it
(import matches on `work_id`, `stem`, `doi` only). `no-doi.csv` lists the works
`dois.txt` cannot represent, always written, header row included. A `README.md`
written from a template in the export module tells the collaborator to send
the PDFs back as downloaded, in one folder, with no renaming: import reads the
DOI out of each file, and whatever stays unmatched is the operator's residual
to rename, not the collaborator's. The runner gains an `EXPORT_GAP=1` env
knob that runs the export after stage 5; it is env-only because the export
rewrites the CSV a collaborator may be holding, so it is a one-shot action
rather than a property of the review config.

## Implementation sequence

Landed as one squash with the run manifest: the columns, `no-doi.csv`, the
README, the runner step. Tests: a DOI-less work appears in `no-doi.csv` and
the README and not in `dois.txt`; a bundle with no DOI-less works omits the
`no-doi.csv` bullet; the manifest with the extra columns round-trips through
`read_manifest`.

## Follow-ups

Title matching for returned PDFs that embed no DOI, per
[contributor-pdf-matching.md](../../docs/explorations/contributor-pdf-matching.md).

## Critical files

- [src/laglitsynth/fulltext_retrieval/export.py](../../src/laglitsynth/fulltext_retrieval/export.py)
- [src/laglitsynth/fulltext_retrieval/import_.py](../../src/laglitsynth/fulltext_retrieval/import_.py)
- [scripts/run-pipeline.sh](../../scripts/run-pipeline.sh)
- [docs/fulltext-retrieval.md](../../docs/fulltext-retrieval.md)
