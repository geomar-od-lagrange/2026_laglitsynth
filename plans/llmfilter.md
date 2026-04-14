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
```

## Data flow

```
input.jsonl  →  read Work  →  send abstract + prompt to Ollama  →  yes/no  →  output.jsonl
```

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

- `classify_abstract(abstract: str, prompt: str, *, model: str, base_url: str)
  -> FilterVerdict` — Sends one abstract to Ollama via its OpenAI-compatible
  `/v1/chat/completions` endpoint. Uses a system prompt that instructs the LLM
  to return structured JSON (`{"relevance_score": 0-100, "reason": "..."}`).
  Returns parsed `FilterVerdict`.
- `filter_works(input_path: Path, prompt: str, ...) -> Iterator[tuple[Work,
  FilterVerdict]]` — Reads JSONL line by line, calls `classify_abstract` for
  each work that has a non-None abstract, yields `(work, verdict)` pairs. Works
  with `abstract is None` are skipped (logged).
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
- `--dry-run`: print verdicts to stderr without writing output (useful for
  prompt tuning)

## Design decisions

1. **Use Ollama's OpenAI-compatible API** (`/v1/chat/completions`). The only
   dependency is `httpx` — no Ollama-specific client library. Also makes it
   trivial to swap in any OpenAI-compatible endpoint later.

2. **Structured JSON output, not free-text parsing.** The system prompt forces
   the LLM to respond with `{"relevance_score": int, "reason": str}`. Ollama
   supports JSON mode. Parse with Pydantic. If the LLM returns garbage, log a
   warning and skip the work (same pattern as the fetch tool).

3. **Sequential, not batched.** Local LLMs are the bottleneck, not network.
   Send one abstract at a time. Keeps memory flat and progress reporting
   simple.

4. **Output is the same `Work` JSONL format.** The filtered file is readable
   by any downstream tool that reads `Work` records — no new format. The
   sidecar `.meta.json` records the filter prompt, model used, and
   accept/reject counts.

5. **Verdict log.** Write a `_verdicts.jsonl` sidecar with one
   `FilterVerdict` per input work (including rejects). This is the audit
   trail — lets you inspect why things were filtered without re-running.

## Dependencies

- `httpx` — for calling the Ollama API. Add to `pyproject.toml` dependencies.

## Unified CLI

All commands live under a single `laglitsynth` entrypoint using argparse
subparsers. This is a cross-cutting change that also migrates the existing
`fetch-publications` command.

### New file: `src/laglitsynth/cli.py`

Top-level CLI dispatcher using `argparse` with `add_subparsers`. Each
component registers its subcommand via a `build_subparser(subparsers)`
function. Unified `laglitsynth --help` lists all available commands.

### Changes to `pyproject.toml`

```toml
# Replace individual script entrypoints with one:
[project.scripts]
laglitsynth = "laglitsynth.cli:main"

# Remove pixi tasks that just wrap entrypoints (fetch-publications, etc.)
```

### Migration of `fetch.py`

Refactor `fetch.py:main()` to expose `build_subparser(subparsers)` and
`run(args)`, matching the same pattern as `filter.py`. The existing
`main(argv)` can remain for backwards compatibility / direct invocation
during the transition but is no longer the primary entrypoint.

## What this plan does NOT cover

- Batching / async concurrency (premature for local LLM)
- Prompt templates or presets (user passes the prompt directly)
- Re-filtering or incremental filtering (rerun from scratch; files are cheap)
- Embedding-based filtering (different approach, different tool)
