# OpenAlex fetch tool

Searches OpenAlex by keyword and writes the full result set as a JSONL file.
Each line is a validated `Work` record (see [data model](data-model.md)).

## Usage

```bash
pixi run fetch-publications "lagrangian oceanography"
```

Run `pixi run fetch-publications --help` for all options.

## Output

Each run produces two files:

- **`<slug>_<timestamp>.jsonl`** — one JSON object per line, each a serialized
  `Work`. Read back with `Work.model_validate_json(line)`.
- **`<slug>_<timestamp>.meta.json`** — sidecar with query metadata: tool name,
  query string, timestamp, record count.

Default output directory is [`data/openalex/`](../data/openalex/). Timestamped
filenames prevent collisions between runs.

## Abstract reconstruction

OpenAlex returns abstracts as an inverted index (word -> list of positions).
The fetch tool reconstructs plain text at ingest time so the JSONL stores
human-readable abstracts. Downstream tools never see the inverted index.

## Error handling

- Missing `OPENALEX_API_KEY` fails fast with a link to the registration page.
- The key can be set in a `.env` file in the project root.
- Invalid records (null where the API schema says non-null) are logged and
  skipped. This is an OpenAlex data quality issue affecting a small fraction
  of records.
- Transient API errors (429, 5xx) are retried automatically (3 retries with
  backoff).

## Fetch performance

At ~80 records/sec with full payload:

| Result count | Time     |
|-------------|----------|
| 1,000       | ~13s     |
| 10,000      | ~2 min   |
| 16,000      | ~3.5 min |
| 100,000     | ~21 min  |
