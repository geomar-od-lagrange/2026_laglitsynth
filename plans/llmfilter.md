# Plan: LLM-based abstract filtering via Ollama

## Prerequisites

Ollama must be running locally (or reachable via SSH tunnel). Setup:

```
ollama serve                # start the server (default: localhost:11434)
ollama pull gemma3:4b       # download the default model (~2.5 GB)
```

Default model is `gemma3:4b` — small enough for a mid-sized laptop with Apple
Silicon, fast at classification tasks. For a machine with a discrete GPU or more
RAM, `gemma3:12b` or `llama3.2` are worth trying. The `--model` flag overrides
the default.

If tunneling to a remote GPU machine, expose the port locally and pass
`--base-url http://localhost:<port>`.

---

Filter OpenAlex JSONL files by sending each abstract to a local Ollama-hosted LLM
with a user-provided relevance prompt. Output a new JSONL containing only the works
that pass the filter.

## Example usage

```
laglitsynth filter-abstracts data/openalex/lagrangian_oceanography_2026-...jsonl \
  "Make sure this abstract is about Lagrangian particle tracking and touches on submesoscale dynamics"

# with a stricter threshold (default: 50)
laglitsynth filter-abstracts input.jsonl "..." --threshold 70

# prompt tuning: process first 20 works, print verdicts, don't write output
laglitsynth filter-abstracts input.jsonl "..." --dry-run --limit 20
```

## Data flow

```
input.jsonl  →  read Work  →  send abstract + prompt to Ollama  →  score + reason  →  output.jsonl
```

## Shared utility: JSONL reader

There is no existing function to read `Work` records back from JSONL. This
component needs one, and every future downstream tool will too. Add a shared
reader in `src/laglitsynth/io.py`:

```python
def read_works_jsonl(path: Path) -> Iterator[Work]:
    """Yield validated Work records from a JSONL file."""
    ...
```

This is the read-side counterpart to `fetch.write_jsonl`. Lives at the package
level since it's not specific to any one component.

## New package: `src/laglitsynth/llmfilter/`

Three files:

### `models.py` — filter result model

- `FilterVerdict`: Pydantic model with `work_id: str`,
  `relevance_score: int` (0–100, LLM's confidence in relevance),
  `accepted: bool` (derived: `relevance_score >= threshold`),
  `reason: str` (short LLM-generated explanation).
  The LLM returns `relevance_score` and `reason`; `accepted` is computed
  client-side from the threshold so you can re-threshold without re-running.

### `filter.py` — core logic

- `classify_abstract(work_id: str, abstract: str, prompt: str, *, model: str,
  base_url: str) -> FilterVerdict` — Sends one abstract to Ollama via the
  OpenAI-compatible `/v1/chat/completions` endpoint using the `openai` Python
  library. Sets `response_format={"type": "json_object"}` to activate Ollama's
  JSON mode. The system prompt instructs the LLM to return
  `{"relevance_score": 0-100, "reason": "..."}`. Returns parsed
  `FilterVerdict` with `work_id` attached.
- `filter_works(input_path: Path, prompt: str, ...) -> Iterator[tuple[Work,
  FilterVerdict]]` — Uses `read_works_jsonl` to iterate records, calls
  `classify_abstract` for each work that has a non-None abstract, yields
  `(work, verdict)` pairs. Works with `abstract is None` are skipped (logged).
- `build_subparser(subparsers)` — registers the `filter-abstracts` subcommand
  with its arguments on a parent subparsers object.
- `run(args)` — executes the command from parsed args.

### `__init__.py` — empty

## CLI interface

```
laglitsynth filter-abstracts INPUT_JSONL PROMPT [options]
```

Arguments:
- Positional: input JSONL path, filter prompt string
- `-o` / `--output`: output JSONL path (default:
  `<input_stem>_filtered_<timestamp>.jsonl` in the same directory)
- `--model`: Ollama model name (default: `gemma3:4b`)
- `--threshold`: relevance score cutoff, 0–100 (default: 50)
- `--base-url`: Ollama API base URL (default: `http://localhost:11434`)
- `--reject-file`: optional path to write rejected works (for auditing)
- `--limit`: process only the first N works (useful with `--dry-run` for
  prompt tuning)
- `--dry-run`: print verdicts to stderr without writing output

## Error handling

On startup, before processing any works, make a lightweight request to Ollama
(e.g. list models or classify a dummy input). If the connection fails, exit
immediately with a clear message: "Cannot reach Ollama at {base_url}. Is
`ollama serve` running?" Same pattern as the fetch tool's API key check.

## Progress reporting

Print progress to stderr after each work:

```
  [42/1337] accepted (score: 87) — W2741352... "Lagrangian tracking of..."
  [43/1337] rejected (score: 12) — W9182635... "Phylogenetic analysis..."
```

Include: index/total, verdict, score, truncated work ID and title. At
completion, print summary: accepted/rejected/skipped counts and elapsed time.

## Design decisions

1. **Use the `openai` Python library** against Ollama's OpenAI-compatible
   endpoint (`/v1/chat/completions`). This is a mature, typed library that
   handles retries, connection errors, and streaming. Using it with
   `OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")` requires
   no Ollama-specific code and makes it trivial to point at any
   OpenAI-compatible backend. Preferred over raw `httpx` per the "use what
   exists" principle.

2. **JSON mode via API parameter, not just prompting.** Set
   `response_format={"type": "json_object"}` in the API request so Ollama
   constrains output to valid JSON. The system prompt specifies the schema;
   the API parameter enforces the format. Parse the result with Pydantic. If
   the LLM returns valid JSON that doesn't match the schema, log a warning
   and skip the work (same pattern as the fetch tool).

3. **Sequential, not batched.** Local LLMs are the bottleneck, not network.
   Send one abstract at a time. Keeps memory flat and progress reporting
   simple.

4. **Output is the same `Work` JSONL format.** The filtered file is readable
   by any downstream tool that reads `Work` records — no new format. The
   sidecar `.meta.json` records the filter prompt, model used, threshold,
   and accept/reject/skip counts.

5. **Verdict log.** Write a `_verdicts.jsonl` sidecar with one
   `FilterVerdict` per input work (including rejects and skips). Naming
   convention: if output is `foo_filtered_2026-...jsonl`, verdicts go to
   `foo_filtered_2026-...verdicts.jsonl` and metadata to
   `foo_filtered_2026-...meta.json` (same stem, different suffix — matching
   the fetch tool's `.meta.json` pattern via `path.with_suffix()`).

## Dependencies

- `openai` — for calling the Ollama API via its OpenAI-compatible endpoint.
  Add to `pyproject.toml` dependencies.

## Unified CLI

All commands live under a single `laglitsynth` entrypoint using argparse
subparsers. This is a cross-cutting change that also migrates the existing
`fetch-publications` command.

### Subcommand contract

Each component exposes two functions as its CLI interface:

- `build_subparser(subparsers)` — registers the subcommand name, help text,
  and arguments on an `argparse._SubParsersAction`. Returns the subparser.
- `run(args: argparse.Namespace)` — executes the command from parsed args.

These live in the component module itself (e.g. `llmfilter/filter.py`,
`openalex/fetch.py`) since the arguments are tightly coupled to the logic.
`cli.py` imports and wires them — it has no command-specific knowledge.

### New file: `src/laglitsynth/cli.py`

Top-level CLI dispatcher using `argparse` with `add_subparsers`. Imports
`build_subparser` from each component, calls them to register subcommands,
parses args, dispatches to the appropriate `run`. Unified
`laglitsynth --help` lists all available commands.

### Changes to `pyproject.toml`

```toml
# Replace individual script entrypoints with one:
[project.scripts]
laglitsynth = "laglitsynth.cli:main"

# Remove: fetch-publications entrypoint
# Remove: all pixi tasks that just wrap entrypoints
```

### Migration of `fetch.py`

Refactor `fetch.py:main()` to expose `build_subparser(subparsers)` and
`run(args)`. The standalone `main(argv)` is removed — green field, no
backwards compatibility.

## What this plan does NOT cover

- Batching / async concurrency (premature for local LLM)
- Prompt templates or presets (user passes the prompt directly)
- Re-filtering or incremental filtering (rerun from scratch; files are cheap)
- Embedding-based filtering (different approach, different tool)
