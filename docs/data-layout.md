# Data layout

All pipeline data lives under `data/` in the project root. This directory is
gitignored -- data files are local to each machine and not checked in.

```
data/
  openalex/          # raw OpenAlex fetches
    <slug>_<ts>.jsonl
    <slug>_<ts>.meta.json
  filtered/          # LLM filter output
    <stem>_filtered_<ts>.jsonl
    <stem>_filtered_<ts>.verdicts.jsonl
    <stem>_filtered_<ts>.meta.json
```

## Directories

### `data/openalex/`

Output from [`laglitsynth fetch-publications`](fetch-publications.md). Each
run produces a `.jsonl` file of `Work` records and a `.meta.json` sidecar.
Timestamped filenames prevent collisions between runs of the same query.

### `data/filtered/`

Output from [`laglitsynth filter-abstracts`](filter-abstracts.md). Contains
the filtered `Work` JSONL, a `.verdicts.jsonl` with per-work scoring
details, and a `.meta.json` sidecar with filter configuration and counts.

## Sidecar conventions

Every JSONL output file has a companion `.meta.json` sidecar (same stem,
`.meta.json` suffix). The sidecar records tool name, version, run
parameters, and summary statistics. This keeps the JSONL as a pure stream
of records with no special first-line handling.

The filter tool also produces a `.verdicts.jsonl` sidecar with one
`FilterVerdict` per processed work (including rejects and skips). This
enables re-thresholding or auditing without re-running the LLM.

## Note on paths

Tools create parent directories automatically (`mkdir -p` equivalent). You
can override default output paths with the `-o` flag on any command, but
the convention is to keep raw data in `data/openalex/` and derived products
in `data/filtered/`.
