# catalogue-fetch

Search OpenAlex by keyword and write the full result set as a JSONL file.
Each line is a validated [`Work`](data-model.md) record.

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
| `-o` / `--output` | Output JSONL path. Default: `data/catalogue-fetch/<slug>_<timestamp>.jsonl`. |
| `--from-year` | Filter publications from this year onward. |
| `--to-year` | Filter publications up to this year. |
| `--max-records` | Cap on number of results. Omit to fetch all matching works. |

## Output format

Each run produces two files in `data/catalogue-fetch/`:

- **`<slug>_<timestamp>.jsonl`** -- one `Work` JSON object per line. Read
  back with `Work.model_validate_json(line)` or the shared
  `read_works_jsonl()` helper.
- **`<slug>_<timestamp>.meta.json`** -- sidecar with query metadata (tool
  name, query string, timestamp, record count).

Timestamped filenames prevent collisions between runs. The slug is the query
lowercased with non-alphanumeric runs replaced by underscores.

## Authentication

OpenAlex requires a free API key. Register at
<https://openalex.org/settings/api>. When `--api-key` is not passed on the
command line the tool reads `OPENALEX_API_KEY` from `.env` in the working
directory and emits `Loaded OPENALEX_API_KEY from .env` to stderr. Wrapper
scripts (`run-pipeline.sh`, `nesh-pipeline.sbatch`) source `.env` and pass
`--api-key "$OPENALEX_API_KEY"` explicitly — the flag still takes precedence
over any `.env` value.

## Abstract reconstruction

OpenAlex returns abstracts as an inverted index. The fetch tool reconstructs
plain text at ingest time so the JSONL stores human-readable abstracts.
Downstream tools never see the inverted index format.

## Error handling

- `--api-key` is required (either as a CLI flag or via `OPENALEX_API_KEY` in `.env`); the tool exits with a clear error if neither is present.
- Invalid records are logged and skipped (OpenAlex data quality issue
  affecting a small fraction of records); the count is written to
  `validation_skipped` in the meta sidecar.
- Transient API errors (429, 5xx) are retried automatically (3 retries with
  backoff).
