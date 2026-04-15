# Lagrangian Oceanography Literature Synthesis

Tools for conducting structured literature reviews — from keyword search to
citation analysis to structured synthesis.

## Setup

Requires [pixi](https://pixi.sh) for environment management.

```bash
pixi install
```

## Tools

All tools are accessed via the `laglitsynth` CLI:

```bash
laglitsynth --help
```

- `laglitsynth fetch-publications` — search OpenAlex by keyword and store
  results as JSONL. See [`docs/fetch-publications.md`](docs/fetch-publications.md).
- `laglitsynth filter-abstracts` — filter abstracts by relevance using a
  local Ollama-hosted LLM. See [`docs/filter-abstracts.md`](docs/filter-abstracts.md).

## OpenAlex API key

Since February 2026, OpenAlex requires a (free) API key. Register at
<https://openalex.org/settings/api> and set the environment variable in `.env`:

```
OPENALEX_API_KEY=your-key-here
```

## Ollama (for LLM filtering)

The filter tool requires a running Ollama instance:

```bash
ollama serve
ollama pull gemma3:4b
```

See [`docs/filter-abstracts.md`](docs/filter-abstracts.md) for details.

## Documentation

- [`docs/`](docs/) — component documentation
- [`plans/`](plans/) — implementation plans
- [`AGENTS.md`](AGENTS.md) — project principles and structure
