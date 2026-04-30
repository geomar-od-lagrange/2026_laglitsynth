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

Stages 1–8 are implemented. Each stage has its own doc under [`docs/`](docs/).

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
- `laglitsynth fulltext-eligibility` — assess full-text eligibility of
  included works with a local LLM, emitting an `EligibilityVerdict`
  sidecar plus an `eligible.jsonl` convenience file. See
  [`docs/eligibility.md`](docs/eligibility.md).
- `laglitsynth extraction-codebook` — extract structured codebook
  records (numerical choices, reproducibility indicators,
  sub-discipline tags) from each eligible work with a local LLM. See
  [`docs/extraction-codebook.md`](docs/extraction-codebook.md).

Stage 9+ are specified in [`docs/pipeline.md`](docs/pipeline.md) but not
yet implemented.

## Running the pipeline

[`scripts/run-pipeline.sh`](scripts/run-pipeline.sh) runs stages 1..8
end-to-end, writing outputs under `data/run/`. The runner sources
`.env` and passes `--api-key` / `--email` as flags to the tools — no
env-var leakage into the Python side.

Defaults that apply to both local and NESH runs:

- `N=5` (local) or `N=10` (NESH) — max records pulled from OpenAlex
  and propagated as `--max-records` to each downstream stage. Override
  as the runner's second positional arg (locally) or via `N=...` in
  `sbatch --export=` (NESH).
- `STOP_AFTER_STAGE=8` — full pipeline. Set to a smaller integer to
  cut runs short while iterating on upstream stages.
- Models: `gemma3:4b` for stages 3 and 7, `llama3.1:8b` for stage 8.
  Override via `SCREENING_MODEL`, `ELIGIBILITY_MODEL`, `EXTRACTION_MODEL`.

### Locally

Start Ollama and GROBID yourself first (see [Ollama](#ollama-for-llm-stages)
and [GROBID](#grobid-for-full-text-extraction) below), then invoke the
runner:

```bash
ollama serve                                                # stages 3, 7, 8
docker run --rm -p 8070:8070 lfoppiano/grobid:0.8.0         # stage 6

scripts/run-pipeline.sh                                     # defaults, N=5
scripts/run-pipeline.sh "particle dispersion" 200           # custom query, N=200
STOP_AFTER_STAGE=3 scripts/run-pipeline.sh                  # stages 1..3 only
```

### On NESH (GPU compute node)

[`scripts/nesh-pipeline.sbatch`](scripts/nesh-pipeline.sbatch) wraps the
runner with the cluster's plumbing: it pulls (once) and starts a
Singularity GROBID container, launches a bundled Ollama, pulls the LLM
models via the NESH proxy, then invokes the runner. You don't need to
start anything yourself.

```bash
mkdir -p logs
sbatch scripts/nesh-pipeline.sbatch                                                     # smoke, N=10
sbatch --export=ALL,QUERY="particle dispersion",N=200 scripts/nesh-pipeline.sbatch
sbatch --export=ALL,STOP_AFTER_STAGE=3,N=500 scripts/nesh-pipeline.sbatch
```

Per-user prerequisites:

- Repo cloned under `$WORK/2026_laglitsynth` (override with `REPO_DIR`).
- `$REPO_DIR/.env` populated with `OPENALEX_API_KEY` and `UNPAYWALL_EMAIL`.
- Bundled Ollama install at `$HOME/.local/share/ollama-install` — the
  full tarball, not just the binary. See
  [docs/explorations/nesh-ollama.md](docs/explorations/nesh-ollama.md).

The job header requests `--partition=gpu --constraint=V100
--gpus-per-node=1 --cpus-per-task=4 --mem=32G --time=02:00:00`. Edit
the sbatch script for H100 nodes; override the time budget on the
command line with `sbatch --time=...` (see below).

#### Production-run cheatsheet

The sbatch header's 2 h is smoke-sized. For a real corpus run,
override `N` and `--time`. `OLLAMA_NUM_PARALLEL=2` (and the derived
`LLM_CONCURRENCY=4`) is the documented production default — see
[docs/llm-concurrency.md](docs/llm-concurrency.md) for the
benchmarking that landed there. Leave as-is unless your model's
specific benchmark says otherwise.

```bash
sbatch \
    --time=12:00:00 \
    --export=ALL,QUERY="Lagrangian particle tracking",N=500 \
    scripts/nesh-pipeline.sbatch
```

Notes:

- `--time` is an sbatch flag, not an `--export` variable; pass it
  separately on the `sbatch` line.
- Stage 8 (extraction-codebook) is the throughput floor; budget
  `--time` against it. Stages 7 and 8 do not yet honour
  `LLM_CONCURRENCY` — they call Ollama sequentially.
- Each stage's output is truncated at run start, so a wall-clock kill
  mid-stage means re-running that stage from scratch on the next
  submission. If you expect a tight budget, prefer running with
  `STOP_AFTER_STAGE=6` first to land catalogue + retrieval +
  extraction (which are cheap to redo with `--skip-existing`), then
  submit a second job with stages 7..8.

### Reviewer exports

The pipeline writes JSONL only — no human-readable spreadsheets are
produced automatically. To spot-check stage 3's verdicts, run
`screening-abstracts-export` after the pipeline lands its output:

```bash
# Flat CSV — one row per work, opens in Excel / Numbers / LibreOffice
laglitsynth screening-abstracts-export --format csv \
    --verdicts data/run/screening-abstracts/verdicts.jsonl \
    --catalogue data/run/catalogue-dedup/deduplicated.jsonl

# XLSX — one tab per work plus an index sheet (better for deep review)
laglitsynth screening-abstracts-export --format xlsx \
    --verdicts data/run/screening-abstracts/verdicts.jsonl \
    --catalogue data/run/catalogue-dedup/deduplicated.jsonl

# Reproducible random subset of 30 works (xlsx only)
laglitsynth screening-abstracts-export --format xlsx \
    --verdicts data/run/screening-abstracts/verdicts.jsonl \
    --catalogue data/run/catalogue-dedup/deduplicated.jsonl \
    --n-subset 30 --subset-seed 1
```

See [docs/screening-abstracts.md](docs/screening-abstracts.md) for the
full export schema. There is no equivalent export for stages 7 or 8 yet.

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

## Ollama (for LLM stages)

Stages 3 ([`screening-abstracts`](docs/screening-abstracts.md)), 7
([`fulltext-eligibility`](docs/eligibility.md)), and 8
([`extraction-codebook`](docs/extraction-codebook.md)) all call a local
Ollama instance via the OpenAI-compatible API.

```bash
ollama serve
ollama pull gemma3:4b
```

`gemma3:4b` (the CLI default) handles stages 3 and 7 comfortably but
struggles with stage 8's 30-field structured JSON on typical paper
bodies. For stage 8 a larger model is usually needed — in our smoke
tests `llama3.1:8b` and `qwen2.5:14b` both produced valid records
where `gemma3:4b` returned empty JSON. Pull whichever you want to use
and pass it via `--model`.

```bash
ollama pull llama3.1:8b        # or qwen2.5:14b, etc.
laglitsynth extraction-codebook --model llama3.1:8b ...
```

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
