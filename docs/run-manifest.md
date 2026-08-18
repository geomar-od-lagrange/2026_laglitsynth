# Run manifest

One file, `data/manifest.json`, pins what a review is: the queries it ran, the
shared run-id, and an append-only log of what each stage read and wrote. Stages
resolve their inputs from it, so paths stop being carried between invocations by
hand.

## The file

A typed [`RunManifest`](../src/laglitsynth/manifest.py), written through the
same [`io.write_meta`](../src/laglitsynth/io.py) every `*-meta.json` sidecar
uses:

| Field | Meaning |
|---|---|
| `run` | The `RunMeta` of the mint event: tool, version, timestamp |
| `run_id` | The shared leaf under `<stage>/<run-id>/` for stages 3, 7, and 8 |
| `queries` | The search terms this review ran |
| `data_dir` | The project root marker |
| `stages` | Append-only; one `StageEntry` per stage invocation |

Each `StageEntry` records the stage's CLI subcommand name, an ISO-8601 UTC
timestamp, its resolved `inputs` by logical name, its `output`, and its
`meta_path` when it writes a sidecar. A re-run appends rather than rewriting, so
the file is a lineage log and not a current-state snapshot. `latest_output`
reads it backward, which makes the last entry for a stage its current output.

`output` is always the path a downstream stage would pass as its own input flag.
Stage 3 records `verdicts.jsonl`, not the run-id directory holding it, because
`fulltext-retrieval` names that file.

## Starting a review

```sh
laglitsynth manifest-init "Lagrangian particle tracking" --data-dir data
```

This mints the run-id, records the queries, and writes the manifest with an
empty stage log. It refuses to overwrite an existing manifest: a review is
initialised once. `generate-run-id` remains for ad-hoc use.

## How a stage resolves its inputs

Every wired stage follows one rule, in
[`resolve_input`](../src/laglitsynth/manifest.py):

1. An explicit flag wins. An invocation that passes every path behaves exactly
   as it did before the manifest existed.
2. Otherwise the last entry for the named upstream stage supplies the path.
3. Otherwise the stage exits with the same required-flag error as before,
   naming the flag.

A manifest that names a path absent on this disk is an error naming the
manifest, never a silent skip. That is the case where `data/manifest.json`
arrives on a second machine ahead of the data it describes.

`--run-id` resolves the same way through `resolve_run_id`: the flag, else the
manifest's `run_id`, else a fresh one.

A stage records its own entry through `record_stage` when the run is finished.
Where a review has no manifest, `record_stage` does nothing and no stage
behaves differently — the manifest is additive.

## Wired stages

| Stage | Resolves | Records as `output` |
|---|---|---|
| `catalogue-dedup` | `--input` from `catalogue-fetch` | `deduplicated.jsonl` |
| `screening-abstracts` | catalogue from `catalogue-dedup` | `verdicts.jsonl` |
| `fulltext-retrieval` | catalogue, screening verdicts | the `pdfs/` store |
| `fulltext-retrieval-export` | catalogue, screening verdicts | the bundle directory |
| `fulltext-retrieval-import` | `--manifest` from the export bundle | the `pdfs/` store |
| `fulltext-eligibility` | catalogue, screening verdicts, extractions | `verdicts.jsonl` |
| `extraction-codebook` | catalogue, eligibility verdicts, extractions | `records.jsonl` |

`catalogue-fetch` and `fulltext-extraction` do not record entries yet, so the
inputs that would resolve from them still have to be passed. Every other edge
resolves.

`fulltext-retrieval-import` keeps `--import-dir` required. No upstream stage
knows which folder a collaborator sent back.

## The collaborator handoff

The manifest is what reduces the handoff to two commands. Once a review is
initialised and screening has run:

```sh
laglitsynth fulltext-retrieval-export --data-dir data
# hand data/pdfs/export/ to a collaborator, receive a folder of PDFs back
laglitsynth fulltext-retrieval-import --import-dir ~/returned --source manual --data-dir data
```

Both append their entry, so the manifest also answers which selection was sent
and which import filled it. See
[fulltext-retrieval.md](fulltext-retrieval.md) for the bundle contents.

## Across machines

The manifest travels with `data/` under rsync, so the run-id crosses machines
with the data rather than in someone's shell history. A collaborator who
receives the catalogue and the verdicts receives the lineage that produced
them. See [cross-machine.md](cross-machine.md) for which parts of `data/` are
safe to sync in which direction.

The manifest carries the shared run-id that makes a cross-machine merge
addressable. Merging two machines' verdicts is a separate problem and is not
what this file does.
