# Run manifest and the `data/` layout

A review is a `data/` directory with a fixed layout and one
`data/manifest.json`. Every stage reads its inputs from, and writes its
outputs to, a conventional path under `--data-dir`, so an invocation normally
names no paths at all. The one value the layout cannot derive is the run-id
leaf that stages 3, 7, and 8 write under, and the manifest carries that.

## The file

A typed [`RunManifest`](../src/laglitsynth/manifest.py) with three fields:
`run` (the `RunMeta` of the mint event), `run_id`, and `queries` (the search
terms the review ran). It is written once and never rewritten, so it is safe
to copy between machines in either direction.

```sh
laglitsynth manifest-init "Lagrangian particle tracking" --data-dir data
```

`manifest-init` mints the run-id and refuses to overwrite an existing
manifest. The runner ([run-pipeline.sh](../scripts/run-pipeline.sh)) calls it
when `$ROOT/manifest.json` is absent, so a review driven by the runner never
needs it typed.

## The layout

[`layout.py`](../src/laglitsynth/layout.py) is the one place the conventional
paths are spelled out. Under `<data-dir>/`:

| Stage | Reads | Writes |
|---|---|---|
| `catalogue-fetch` | — | `catalogue-fetch/<slug>_<timestamp>.jsonl` |
| `catalogue-dedup` | `catalogue-fetch/*.jsonl` | `catalogue-dedup/deduplicated.jsonl` |
| `screening-abstracts` | `catalogue-dedup/deduplicated.jsonl` | `screening-abstracts/<run-id>/verdicts.jsonl` |
| `fulltext-retrieval` | the catalogue, `screening-abstracts/<run-id>/verdicts.jsonl` | `pdfs/` |
| `fulltext-retrieval-export` | the same pair | `pdfs/export/` |
| `fulltext-retrieval-import` | `pdfs/export/pdf-manifest.csv` | `pdfs/` |
| `fulltext-extraction` | `pdfs/` | `fulltext-extraction/extraction.jsonl` |
| `fulltext-eligibility` | the catalogue, the screening verdicts, `fulltext-extraction/extraction.jsonl` | `fulltext-eligibility/<run-id>/verdicts.jsonl` |
| `extraction-codebook` | the catalogue, `fulltext-eligibility/<run-id>/verdicts.jsonl`, the extractions | `extraction-codebook/<run-id>/records.jsonl` |

Every input flag stays available as an override and wins when passed. An
omitted flag takes the conventional path; if that path does not exist the
stage exits before doing any work, naming the upstream stage to run and the
flag to pass instead (`… not found; run catalogue-dedup first or pass
--catalogue`).

## The run-id

`--run-id` resolves the same way on every stage that writes or reads a
`<run-id>/` leaf: the flag wins, else the manifest's `run_id`, else the stage
exits asking for one. No stage mints a run-id on its own, because a leaf
nobody else knows about is exactly the divergence the manifest exists to
prevent. `generate-run-id` remains for ad-hoc use with an explicit `--run-id`.

One run-id per review means a re-run of `screening-abstracts` without
`--run-id` writes into the same run directory and replaces its
`verdicts.jsonl`; the stage says so on stderr when that is about to happen.
Pass `--run-id` to keep an earlier run alongside, and pass the same `--run-id`
to the stages that read it.

## The collaborator handoff

Once screening and retrieval have run, the handoff is two commands:

```sh
laglitsynth fulltext-retrieval-export --data-dir data
# hand data/pdfs/export/ to a collaborator, receive a folder of PDFs back
laglitsynth fulltext-retrieval-import --import-dir ~/returned --source manual --data-dir data
```

See [fulltext-retrieval.md](fulltext-retrieval.md) for the bundle contents
and how import matches returned files.

## Across machines

The manifest travels with `data/` under rsync, so the run-id crosses machines
with the data rather than in someone's shell history. Because it is written
once, it has no sync direction to get wrong. See
[cross-machine.md](cross-machine.md) for the rules on the rest of `data/`.
