# Plan: LLM-based abstract filtering via Ollama

Filter OpenAlex JSONL files by sending each abstract to a local Ollama-hosted LLM
with a user-provided relevance prompt. Output a new JSONL containing only the works
that pass the filter.

## Example usage

```
pixi run filter-abstracts data/openalex/lagrangian_oceanography_2026-...jsonl \
  "Make sure this abstract is about Lagrangian particle tracking and touches on submesoscale dynamics"
```

## Data flow

```
input.jsonl  →  read Work  →  send abstract + prompt to Ollama  →  yes/no  →  output.jsonl
```

## New package: `src/laglitsynth/llmfilter/`

Three files:

### `models.py` — filter result model

- `FilterVerdict`: Pydantic model with `work_id: str`, `accepted: bool`,
  `reason: str` (short LLM-generated explanation of why it passed/failed).
  This is the structured output the LLM returns.

### `filter.py` — core logic + CLI

- `classify_abstract(abstract: str, prompt: str, *, model: str, base_url: str)
  -> FilterVerdict` — Sends one abstract to Ollama via its OpenAI-compatible
  `/v1/chat/completions` endpoint. Uses a system prompt that instructs the LLM
  to return structured JSON (`{"accepted": true/false, "reason": "..."}`).
  Returns parsed `FilterVerdict`.
- `filter_works(input_path: Path, prompt: str, ...) -> Iterator[tuple[Work,
  FilterVerdict]]` — Reads JSONL line by line, calls `classify_abstract` for
  each work that has a non-None abstract, yields `(work, verdict)` pairs. Works
  with `abstract is None` are skipped (logged).
- `main()` — CLI entrypoint with argparse.

### `__init__.py` — empty

## CLI interface

```
filter-abstracts INPUT_JSONL PROMPT [options]
```

Arguments:
- Positional: input JSONL path, filter prompt string
- `-o` / `--output`: output JSONL path (default:
  `<input_stem>_filtered_<timestamp>.jsonl` in the same directory)
- `--model`: Ollama model name (default: TBD, e.g. `llama3.2` or `gemma3`)
- `--base-url`: Ollama API base URL (default: `http://localhost:11434`)
- `--reject-file`: optional path to write rejected works (for auditing)
- `--dry-run`: print verdicts to stderr without writing output (useful for
  prompt tuning)

## Design decisions

1. **Use Ollama's OpenAI-compatible API** (`/v1/chat/completions`). The only
   dependency is `httpx` — no Ollama-specific client library. Also makes it
   trivial to swap in any OpenAI-compatible endpoint later.

2. **Structured JSON output, not free-text parsing.** The system prompt forces
   the LLM to respond with `{"accepted": bool, "reason": str}`. Ollama
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

## pixi task + entrypoint

```toml
# pyproject.toml
[project.scripts]
filter-abstracts = "laglitsynth.llmfilter.filter:main"

[tool.pixi.tasks]
filter-abstracts = { cmd = "filter-abstracts", description = "Filter abstracts with a local LLM via Ollama" }
```

## What this plan does NOT cover

- Batching / async concurrency (premature for local LLM)
- Prompt templates or presets (user passes the prompt directly)
- Re-filtering or incremental filtering (rerun from scratch; files are cheap)
- Embedding-based filtering (different approach, different tool)
