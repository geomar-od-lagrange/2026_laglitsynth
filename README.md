# Lagrangian Oceanography Literature Synthesis

Tools for conducting structured literature reviews — from keyword search to
citation analysis to structured synthesis.

## Setup

Requires [pixi](https://pixi.sh) for environment management.

```bash
pixi install
```

### OpenAlex API key

Since February 2026, OpenAlex requires a (free) API key. Register at
<https://openalex.org/settings/api> and set the environment variable:

```bash
export OPENALEX_API_KEY="your-key-here"
```

## Usage

### Fetch publications from OpenAlex

```bash
pixi run fetch-publications "lagrangian oceanography"
```

Options:

- `-o` / `--output` — custom output path (default: `data/openalex/<slug>_<timestamp>.jsonl`)
- `--from-year` — filter publications from this year onward
- `--to-year` — filter publications up to this year
- `--max-results` — cap on number of results

Output is a JSONL file where each line is a validated `Work` record, plus a
`.meta.json` sidecar with query metadata.
