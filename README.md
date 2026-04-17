# Lagrangian Oceanography Literature Synthesis

Tools for conducting structured literature reviews — from keyword search to
citation analysis to structured synthesis.

## Setup

Requires [pixi](https://pixi.sh) for environment management.

```bash
pixi install
cp .env.example .env  # then fill in OPENALEX_API_KEY and UNPAYWALL_EMAIL
```

## Tools

All tools are accessed via the `laglitsynth` CLI:

```bash
laglitsynth --help
```

Stages 1–6 are implemented. Each stage has its own doc under [`docs/`](docs/).

- `laglitsynth catalogue-fetch` — search OpenAlex by keyword and store
  validated bibliographic records as JSONL. See
  [`docs/catalogue-fetch.md`](docs/catalogue-fetch.md).
- `laglitsynth catalogue-dedup` — deduplicate a combined catalogue on
  DOI and title. See [`docs/catalogue-dedup.md`](docs/catalogue-dedup.md).
- `laglitsynth screening-abstracts` — score every abstract for relevance
  with a local Ollama-hosted LLM, emitting a `ScreeningVerdict` sidecar.
  See [`docs/screening-abstracts.md`](docs/screening-abstracts.md).
- `laglitsynth screening-adjudication` — apply a threshold to the
  screening verdicts, emit `AdjudicationVerdict` records and an
  `included.jsonl` convenience file for stage 5. See
  [`docs/screening-adjudication.md`](docs/screening-adjudication.md).
- `laglitsynth fulltext-retrieval` — fetch PDFs via manual pickup, OA
  URLs, and Unpaywall. See
  [`docs/fulltext-retrieval.md`](docs/fulltext-retrieval.md).
- `laglitsynth fulltext-extraction` — parse retrieved PDFs into
  structured section text via GROBID. See
  [`docs/fulltext-extraction.md`](docs/fulltext-extraction.md).

Stage 7+ are specified in [`docs/pipeline.md`](docs/pipeline.md) but not
yet implemented.

## OpenAlex API key

Since February 2026, OpenAlex requires a (free) API key. Register at
<https://openalex.org/settings/api> and set the environment variable in `.env`:

```
OPENALEX_API_KEY=your-key-here
```

## Unpaywall email

Full-text retrieval queries the Unpaywall API, which requires a contact
email. Set it in `.env`:

```
UNPAYWALL_EMAIL=you@example.com
```

## Ollama (for LLM screening)

The screening stage requires a running Ollama instance:

```bash
ollama serve
ollama pull gemma3:4b
```

See [`docs/screening-abstracts.md`](docs/screening-abstracts.md) for details.

## GROBID (for full-text extraction)

The extraction stage requires a running GROBID server. One-shot via Docker:

```bash
docker run --rm -p 8070:8070 lfoppiano/grobid:0.8.0
```

See [`docs/fulltext-extraction.md`](docs/fulltext-extraction.md) for details.

## Documentation

- [`docs/`](docs/) — component documentation
- [`docs/pipeline.md`](docs/pipeline.md) — end-to-end pipeline overview
- [`plans/`](plans/) — implementation plans (active and archived)
- [`AGENTS.md`](AGENTS.md) — project principles and structure
