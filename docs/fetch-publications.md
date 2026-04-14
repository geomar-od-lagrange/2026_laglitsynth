# fetch-publications

Search OpenAlex by keyword and write the full result set as a JSONL file.
Each line is a validated [`Work`](data-model.md) record.

## Usage

```bash
laglitsynth fetch-publications "lagrangian oceanography"
laglitsynth fetch-publications "lagrangian oceanography" --from-year 2020 --to-year 2025
laglitsynth fetch-publications "submesoscale dynamics" -o data/openalex/custom.jsonl --max-results 500
```

## CLI arguments

| Argument | Description |
|---|---|
| `QUERY` (positional) | Search query string (required). |
| `-o` / `--output` | Output JSONL path. Default: `data/openalex/<slug>_<timestamp>.jsonl`. |
| `--from-year` | Filter publications from this year onward. |
| `--to-year` | Filter publications up to this year. |
| `--max-results` | Cap on number of results. Defaults to 199 as a safety cap -- pass explicitly to fetch more. |

## Output format

Each run produces two files in `data/openalex/`:

- **`<slug>_<timestamp>.jsonl`** -- one `Work` JSON object per line. Read
  back with `Work.model_validate_json(line)` or the shared
  `read_works_jsonl()` helper.
- **`<slug>_<timestamp>.meta.json`** -- sidecar with query metadata (tool
  name, query string, timestamp, record count).

Timestamped filenames prevent collisions between runs. The slug is the query
lowercased with non-alphanumeric runs replaced by underscores.

## Authentication

OpenAlex requires a free API key. Register at
<https://openalex.org/settings/api> and set the `OPENALEX_API_KEY` environment
variable (or add it to a `.env` file in the project root).

## Abstract reconstruction

OpenAlex returns abstracts as an inverted index. The fetch tool reconstructs
plain text at ingest time so the JSONL stores human-readable abstracts.
Downstream tools never see the inverted index format.

## Error handling

- Missing API key fails fast with a link to the registration page.
- Invalid records are logged and skipped (OpenAlex data quality issue
  affecting a small fraction of records).
- Transient API errors (429, 5xx) are retried automatically (3 retries with
  backoff).
