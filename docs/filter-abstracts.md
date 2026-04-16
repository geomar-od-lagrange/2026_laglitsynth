# screening-abstracts

Filter a JSONL file of `Work` records by sending each abstract to a local
Ollama-hosted LLM with a relevance prompt. Outputs a new JSONL containing
only the works that pass the relevance threshold.

## Prerequisites

Ollama must be running locally (or reachable via SSH tunnel):

```bash
ollama serve                # start the server (default: localhost:11434)
ollama pull gemma3:4b       # download the default model (~2.5 GB)
```

For a machine with a discrete GPU or more RAM, `gemma3:12b` or `llama3.2`
are worth trying. Use the `--model` flag to override the default.

If tunneling to a remote GPU machine, expose the port locally and pass
`--base-url http://localhost:<port>`.

## Usage

```bash
# basic filtering
laglitsynth screening-abstracts data/catalogue-fetch/lagrangian_oceanography_2026-...jsonl \
  "Is this abstract about Lagrangian particle tracking in submesoscale dynamics?"

# stricter threshold
laglitsynth screening-abstracts input.jsonl "..." --threshold 70

# custom output path
laglitsynth screening-abstracts input.jsonl "..." -o data/screening-abstracts/my_output.jsonl

# prompt tuning: process first 20 works, print verdicts, don't write output
laglitsynth screening-abstracts input.jsonl "..." --dry-run --max-records 20

# save rejected works for auditing
laglitsynth screening-abstracts input.jsonl "..." --reject-file data/screening-abstracts/rejected.jsonl
```

## CLI arguments

| Argument | Description |
|---|---|
| `INPUT` (positional) | Input JSONL file path (required). |
| `PROMPT` (positional) | Relevance filter prompt string (required). |
| `-o` / `--output` | Output JSONL path. Default: `data/screening-abstracts/<input_stem>_filtered_<timestamp>.jsonl`. |
| `--model` | Ollama model name (default: `gemma3:4b`). |
| `--threshold` | Relevance score cutoff, 0--100 (default: 50). |
| `--base-url` | Ollama API base URL (default: `http://localhost:11434`). |
| `--reject-file` | Optional path to write rejected works (for auditing). |
| `--max-records` | Process only the first N works. Useful with `--dry-run`. |
| `--dry-run` | Print verdicts to stderr without writing any output files. |

## Output format

Each run produces up to three files in `data/screening-abstracts/` (or the directory of
the `-o` path):

- **`<stem>_filtered_<timestamp>.jsonl`** -- accepted `Work` records, same
  format as the input. Readable by any tool that handles `Work` JSONL.
- **`<stem>_filtered_<timestamp>.verdicts.jsonl`** -- one `FilterVerdict`
  per processed work (including rejects), with `work_id`,
  `relevance_score`, `accepted`, and `reason`.
- **`<stem>_filtered_<timestamp>.meta.json`** -- sidecar with filter
  metadata: prompt, model, threshold, accept/reject/skip counts.

## Tips for prompt tuning

Use `--dry-run --max-records N` to iterate on your prompt without writing output
files. This processes the first N works and prints each verdict to stderr so
you can quickly check whether the LLM is scoring sensibly.

```bash
laglitsynth screening-abstracts input.jsonl \
  "Does this abstract study Lagrangian particle dispersion?" \
  --dry-run --max-records 10
```

Adjust the prompt wording and `--threshold` until the accept/reject split
looks right, then run without `--dry-run` for the full set.
