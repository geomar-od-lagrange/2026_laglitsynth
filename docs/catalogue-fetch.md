# catalogue-fetch

Search OpenAlex by keyword and write the full result set as a JSONL file.
Each line is a validated [`Work`](data-model.md) record.

## Prerequisites

- Inputs: none; the search query is the positional argument.
- Services: the OpenAlex API (network access).
- Credentials: a free OpenAlex API key, registered at
  <https://openalex.org/settings/api>, as `--api-key` or `OPENALEX_API_KEY`
  in `.env` (see [external-services.md](external-services.md#openalex-and-unpaywall)).

## Usage

```bash
laglitsynth catalogue-fetch "lagrangian oceanography" --api-key $OPENALEX_API_KEY
laglitsynth catalogue-fetch "lagrangian oceanography" --api-key $OPENALEX_API_KEY --from-year 2020 --to-year 2025
laglitsynth catalogue-fetch "submesoscale dynamics" --api-key $OPENALEX_API_KEY -o data/catalogue-fetch/custom.jsonl --max-records 500
```

## CLI arguments

| Argument | Description |
|---|---|
| `QUERY` (positional) | Search query string (required). |
| `--api-key` | OpenAlex API key. Falls back to `OPENALEX_API_KEY` in `.env` when omitted. |
| `-o` / `--output` | Output JSONL path. Default: `<data-dir>/catalogue-fetch/<slug>_<timestamp>.jsonl`. |
| `--data-dir` | Bucket root for stage outputs (default: `data/`). |
| `--from-year` | Filter publications from this year onward. |
| `--to-year` | Filter publications up to this year. |
| `--max-records` | Cap on number of results. Omit to fetch all matching works. |

## Output format

Each run produces two files in `data/catalogue-fetch/`:

- **`<slug>_<timestamp>.jsonl`** -- one `Work` JSON object per line. Read
  back with `Work.model_validate_json(line)` or the shared
  `read_jsonl(path, Work)` helper.
- **`<slug>_<timestamp>.meta.json`** -- `FetchMeta` sidecar with query
  metadata: the embedded `run` (tool name, timestamp), `query`,
  `total_count` (works matched by the query) and `records_written`
  (works actually written to the JSONL).

The sidecar path is derived from the output path by suffix replacement
(`with_suffix`), so a custom `-o foo.jsonl` writes its meta to
`foo.meta.json` — the `.jsonl` suffix is replaced, not appended to.

Timestamped filenames prevent collisions between runs. The slug is the query
lowercased with non-alphanumeric runs replaced by underscores.

## Authentication

Without `--api-key` the stage reads `OPENALEX_API_KEY` from `.env` in the
working directory and says so on stderr (`Loaded OPENALEX_API_KEY from
.env`); an exported `OPENALEX_API_KEY` is not read. With neither, it exits
naming the variable. The runner ([run-pipeline.sh](../scripts/run-pipeline.sh))
always passes `--api-key`.

## Abstract reconstruction

OpenAlex returns abstracts as an inverted index. The fetch tool reconstructs
plain text at ingest time so the JSONL stores human-readable abstracts.
Downstream tools never see the inverted index format.

## Error handling

- Invalid records are logged and skipped (OpenAlex data quality issue
  affecting a small fraction of records); the count is written to
  `validation_skipped` in the meta sidecar.
- Transient API errors (429, 5xx) are retried automatically (3 retries with
  a `0.5` backoff factor). Results are paged from OpenAlex at `per_page=200`.
