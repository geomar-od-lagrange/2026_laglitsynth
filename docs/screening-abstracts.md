# screening-abstracts

Screen a JSONL file of `Work` records by sending each abstract to a local
Ollama-hosted LLM with a relevance prompt. Outputs a `ScreeningVerdict`
sidecar covering every work in the input — one verdict per work regardless
of score.

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
# basic screening
laglitsynth screening-abstracts data/catalogue-dedup/deduplicated.jsonl \
  "Is this abstract about Lagrangian particle tracking in submesoscale dynamics?"

# stricter threshold
laglitsynth screening-abstracts input.jsonl "..." --screening-threshold 70

# custom output directory
laglitsynth screening-abstracts input.jsonl "..." --output-dir data/screening-abstracts/

# prompt tuning: process first 20 works, print verdicts, don't write output
laglitsynth screening-abstracts input.jsonl "..." --dry-run --max-records 20
```

## CLI arguments

| Argument | Description |
|---|---|
| `INPUT` (positional) | Input JSONL file path (required). |
| `PROMPT` (positional) | Relevance screening prompt string (required). |
| `--output-dir` | Output directory. Default: `data/screening-abstracts/`. |
| `--model` | Ollama model name (default: `gemma3:4b`). |
| `--screening-threshold` | Relevance score cutoff, 0--100 (default: 50). |
| `--base-url` | Ollama API base URL (default: `http://localhost:11434`). |
| `--max-records` | Process only the first N works. Useful with `--dry-run`. |
| `--dry-run` | Print verdicts to stderr without writing any output files. |
| `--concurrency` | In-flight LLM requests (default: `1`). See [llm-concurrency.md](llm-concurrency.md). |

## Output format

Each run produces two files in `--output-dir`:

- **`verdicts.jsonl`** — one `ScreeningVerdict` per input work.
- **`screening-meta.json`** — `ScreeningMeta` sidecar with nested `run`
  and `llm` blocks, threshold, input path, input count, and
  above/below/skipped counts.

### ScreeningVerdict fields

| Field | Type | Description |
|---|---|---|
| `work_id` | `str` | OpenAlex work ID |
| `relevance_score` | `int \| null` | 0–100 from LLM; `null` for sentinel reasons |
| `reason` | `str \| null` | LLM justification string, or a sentinel reason code |
| `seed` | `int \| null` | Ollama random seed passed for this call; `null` for sentinel reasons |
| `raw_response` | `str \| null` | LLM's raw message content; `null` when no call was made |

### Sentinel reason values

Two fixed strings mark non-LLM outcomes:

- `reason="no-abstract"` — the work had no abstract; the LLM was not
  called. `relevance_score=null`, `seed=null`, `raw_response=null`.
- `reason="llm-parse-failure"` — the LLM returned a response that could
  not be parsed. `relevance_score=null`, `seed=null`, `raw_response`
  carries the raw message for audit.

All other `reason` values are the LLM's free-text justification.
`raw_response` is set on successful verdicts and on `llm-parse-failure`
sentinels; it is `None` on `no-abstract` sentinels.

### ScreeningMeta fields

The meta sidecar nests two shared blocks:

- **`run`** (`_RunMeta`): `tool`, `tool_version`, `run_at`, `validation_skipped`.
- **`llm`** (`_LlmMeta`): `model`, `temperature` (explicit; currently `0.8`),
  `prompt_sha256` (sha256 of `SYSTEM_PROMPT + "\n" + user prompt`, 64 hex chars).

The `prompt_sha256` is stable across runs with the same prompt wording.
Comparing it across meta files confirms that two runs used identical prompts.

## Tips for prompt tuning

Use `--dry-run --max-records N` to iterate on your prompt without writing
output files. This processes the first N works and prints each verdict to
stderr so you can quickly check whether the LLM is scoring sensibly.

```bash
laglitsynth screening-abstracts input.jsonl \
  "Does this abstract study Lagrangian particle dispersion?" \
  --dry-run --max-records 10
```

Adjust the prompt wording and `--screening-threshold` until the
above/below split looks right, then run without `--dry-run` for the full
set.
