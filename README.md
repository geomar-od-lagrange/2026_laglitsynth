# Lagrangian Oceanography Literature Synthesis

Tools for conducting structured literature reviews — from keyword search
and PDF acquisition to a self-contained corpus, and from the corpus to
structured extraction per paper.

## Setup

Requires [pixi](https://pixi.sh) for environment management.

```bash
pixi install
cp .env.example .env  # then fill in OPENALEX_API_KEY, UNPAYWALL_EMAIL and LLM_API_KEY
```

`data/` is a git submodule holding the PDF store (`data/pdfs/`, in Git
LFS) in the private repo
[willi-rath/2026_laglitsynth_data](https://git.geomar.de/willi-rath/2026_laglitsynth_data).
With access, fetch it with `git lfs install && git submodule update --init`.
That downloads every PDF; prefix the command with
`GIT_LFS_SKIP_SMUDGE=1` to fetch only the pointers and run
`git -C data lfs pull` later. Without access, leave `data/` uninitialised;
the pipeline works the same. Everything else the pipeline writes under
`data/` is ignored by the submodule and stays local. See
[docs/pdf-store.md](docs/pdf-store.md) for what the store holds and how PDFs
are added.

In a checkout that already has a populated `data/`, `git submodule update
--init` refuses to clone into it. Move it aside, initialise, then move the
local subdirectories back (except `pdfs/`, which the submodule now
provides):

```bash
mv data data.local
git submodule update --init
rsync -a --exclude pdfs/ data.local/ data/
```

Stages 1 and 5 read `OPENALEX_API_KEY` and `UNPAYWALL_EMAIL` from `.env`
automatically when their flags are absent; the LLM stages read
`LLM_API_KEY` from the environment or `.env`. See
[`docs/external-services.md`](docs/external-services.md) for the full
credential setup.

## Tools

All tools are accessed via the `laglitsynth` CLI:

```bash
laglitsynth --help
```

The pipeline has two halves that meet at the corpus
([`docs/pipeline.md`](docs/pipeline.md)): stages 1, 2, 3 and 5 find the
literature and acquire PDFs, `corpus-assemble` writes the self-contained
`data/corpus/`, and stages 6, 7 and 8 analyse the papers in it. Stage
numbers 4 and 9 are unused. A final synthesis step is planned but not
built. Each stage has its own doc under [`docs/`](docs/).

- Stage 1 — `laglitsynth catalogue-fetch` — search OpenAlex by keyword and
  store validated bibliographic records as JSONL. See
  [`docs/catalogue-fetch.md`](docs/catalogue-fetch.md).
- Stage 2 — `laglitsynth catalogue-dedup` — deduplicate a catalogue on
  OpenAlex ID, DOI, and title+author+year; accepts globs and multiple
  inputs. See [`docs/catalogue-dedup.md`](docs/catalogue-dedup.md).
- Stage 3 — `laglitsynth screening-abstracts` — score every abstract for
  relevance with a decisions model on the review's OpenAI-compatible
  endpoint, emitting a `ScreeningVerdict` sidecar. See [`docs/screening-abstracts.md`](docs/screening-abstracts.md).
- Stage 5 — `laglitsynth fulltext-retrieval` — for every work in the
  deduplicated catalogue, fill a persistent, work-keyed PDF store under `data/pdfs/` from OA URLs and
  Unpaywall, with `fulltext-retrieval-export` /
  `fulltext-retrieval-import` to diversify the still-missing margin through
  collaborators' institutional access. See
  [`docs/fulltext-retrieval.md`](docs/fulltext-retrieval.md).
- `laglitsynth corpus-assemble` — apply the screening threshold and copy
  the selected works, their verdicts and their PDFs into `data/corpus/`,
  the only input of stages 6–8. See [`docs/corpus.md`](docs/corpus.md).
- Stage 6 — `laglitsynth fulltext-extraction` — parse the corpus PDFs
  into structured section text via GROBID. See
  [`docs/fulltext-extraction.md`](docs/fulltext-extraction.md).
- Stage 7 — `laglitsynth fulltext-eligibility` — assess each corpus work
  on its full text with a decisions model, emitting an
  `EligibilityVerdict` sidecar. See [`docs/eligibility.md`](docs/eligibility.md).
- Stage 8 — `laglitsynth extraction-codebook` — for the eligible corpus
  works, extract structured codebook records (numerical choices,
  reproducibility indicators, stated rationale, sub-discipline tags) with
  an LLM; `extraction-codebook-export` writes a review workbook. See
  [`docs/extraction-codebook.md`](docs/extraction-codebook.md).

## Running the pipeline

[`scripts/run-pipeline.sh [config.yaml]`](scripts/run-pipeline.sh) runs
stages 1..8 end-to-end, writing outputs under `data/run/`. The runner
exports each `.env` variable that the environment leaves unset or empty
(an exported value wins over `.env`) and passes the OpenAlex `--api-key`
and `--email` as flags to stages 1 and 5. The LLM key (`LLM_API_KEY`) is never passed as a flag:
the LLM stages read it from the environment, so it stays out of the
process list.

The optional positional is a **per-review config** — one YAML that pins
the query, year window, record cap, criteria/codebook paths, the
screening threshold, the LLM endpoint URLs, and the per-stage
models/concurrency. The runner
validates it via `laglitsynth review-config` and applies **env > config >
default** precedence to every knob. With no argument it uses the committed
default
[`examples/reviews/lagrangian-oceanography.yaml`](examples/reviews/lagrangian-oceanography.yaml),
the smoke run. The screening threshold has no default: set
`screening_threshold` in the config or export `SCREENING_THRESHOLD`, or
the runner exits once the run reaches stage 5, after which it runs
`corpus-assemble` with it; a run with `STOP_AFTER_STAGE` below 5 needs none. The historical `QUERY` and `N` env overrides
still work and still win over the config. See
[`docs/configs.md`](docs/configs.md#per-review-config-the-runners-config)
for the config shape and the full `CFG_*` knob list.

Defaults:

- `N=5` — max records pulled from OpenAlex.
  The runner passes `--max-records "$N"` to stage 1 (catalogue-fetch)
  only; downstream stages consume the already-capped catalogue, so the
  cap propagates through the data rather than as a re-passed flag.
  Override via the `N` env var, or pin it as `max_records` in a review
  config.
- `STOP_AFTER_STAGE=8` — full pipeline. Set to a smaller integer to
  cut runs short while iterating on upstream stages.
- LLM endpoint and models have no default. `LLM_BASE_URL` (or
  `llm_base_url`), `LLM_API_KEY` and `SCREENING_MODEL` are required from
  stage 3 on, `ELIGIBILITY_MODEL` from stage 7 and `EXTRACTION_MODEL` from
  stage 8; the runner exits naming whichever is missing.
  `LLM_DECISIONS_URL` (or `llm_decisions_url`) overrides the
  decisions URL of stages 3 and 7. `EXTRACTION_REASONING_EFFORT` (or
  `models.extraction_reasoning_effort`) sets stage 8's reasoning effort;
  unset, none is sent.
- `RUN_ID` names the screening run (stage 3, `corpus-assemble`, the
  export) and defaults to the one in the data dir's `manifest.json`.
  `ANALYSIS_RUN_ID` names the stage 7 and 8 run and defaults to the
  corpus's `analysis_run_id`, which changes whenever the corpus does.

### Starting a run

Start GROBID yourself first (see [GROBID](#grobid-for-full-text-extraction)
below), set the LLM endpoint (see [LLM endpoint](#llm-endpoint)), then
invoke the runner:

```bash
docker run --rm -p 8070:8070 grobid/grobid:0.9.0-crf        # stage 6

SCREENING_THRESHOLD=50 scripts/run-pipeline.sh                                 # default review config, N=5
scripts/run-pipeline.sh examples/reviews/my-review.yaml                        # a review config that sets screening_threshold
SCREENING_THRESHOLD=50 QUERY="particle dispersion" N=200 scripts/run-pipeline.sh  # env overrides win over the config
STOP_AFTER_STAGE=3 scripts/run-pipeline.sh                                     # stages 1..3 only, no threshold needed
```

### Production runs

For a real corpus run, raise `N` (or set `max_records` in the review
config). `LLM_CONCURRENCY` (default `1`) sets the in-flight requests of
the LLM stages (see
[LLM concurrency](docs/interfaces.md#llm-concurrency)) and
`GROBID_CONCURRENCY` (default `3`) those of stage 6 (see
[GROBID concurrency](docs/interfaces.md#grobid-concurrency)).

```bash
SCREENING_THRESHOLD=50 QUERY="Lagrangian particle tracking" N=500 \
    scripts/run-pipeline.sh
```

Notes:

- Stage 8 (extraction-codebook) is the throughput floor. Stages 7 and 8
  both honour `--concurrency` (dispatching through
  `laglitsynth.concurrency.map_concurrent`); the runner forwards
  `LLM_CONCURRENCY` as that flag to stages 3 and 7, and
  `EXTRACTION_CONCURRENCY` (default `1`) to stage 8.
- The runner starts each LLM stage's output afresh (stage 6 runs with
  `--skip-existing`), so an interrupted run means re-running the LLM
  stage it stopped in. To split a long run, use `STOP_AFTER_STAGE=6`
  first to land catalogue, retrieval, corpus and extraction, then run
  stages 7 and 8 by hand, where `--skip-existing` resumes an interrupted
  run.

### Reviewer export

The pipeline writes JSONL only. To spot-check stage 8, run
`extraction-codebook-export` after the pipeline lands its output. It
writes an XLSX workbook: an Index sheet for navigation plus one tab per
work as the working surface, with `--n-subset` / `--subset-seed` for a
reproducible random sample.

```bash
# Stage 8 — codebook extraction records (needs the run's --codebook)
laglitsynth extraction-codebook-export \
    --records data/run/extraction-codebook/<run-id>/records.jsonl \
    --codebook examples/codebooks/lagrangian-oceanography.yaml \
    --catalogue data/run/corpus/works.jsonl \
    --n-subset 30 --subset-seed 1
```

See [docs/extraction-codebook.md](docs/extraction-codebook.md) for the
export schema.

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

## LLM endpoint

Stages 3 ([`screening-abstracts`](docs/screening-abstracts.md)), 7
([`fulltext-eligibility`](docs/eligibility.md)), and 8
([`extraction-codebook`](docs/extraction-codebook.md)) call one
OpenAI-compatible endpoint per review: `--base-url` (including `/v1`),
`--model`, and the key from `LLM_API_KEY` in the environment or `.env`:

```
LLM_API_KEY=your-key-here
```

Stages 3 and 7 ask decisions at `<base-url>/decisions` unless
`--decisions-url` names another URL; stage 8 uses chat completions. See
[`docs/external-services.md`](docs/external-services.md#llm-endpoint).

## GROBID (for full-text extraction)

The extraction stage requires a running GROBID server. One-shot via Docker:

```bash
docker run --rm -p 8070:8070 grobid/grobid:0.9.0-crf
```

The image is multi-arch (amd64 and arm64), so it runs natively on Apple
Silicon.

See [`docs/fulltext-extraction.md`](docs/fulltext-extraction.md) for details.

See [`docs/external-services.md`](docs/external-services.md) for memory
tuning and macOS workarounds.

## Documentation

- [`docs/`](docs/) — component documentation
- [`docs/pipeline.md`](docs/pipeline.md) — end-to-end pipeline overview
- [`docs/corpus.md`](docs/corpus.md) — the corpus where the two halves
  meet: contents, assembly, identity, the analysis run-id
- [`docs/interfaces.md`](docs/interfaces.md) — artifacts, CLI contract,
  models
- [`docs/run-manifest.md`](docs/run-manifest.md) — `data/manifest.json`, the
  run-id, and the fixed `data/` layout every stage reads from
- [`docs/pdf-store.md`](docs/pdf-store.md) — the PDF store in the `data/`
  submodule: contents, sources, how PDFs are added
- [`plans/`](plans/) — implementation plans (active and archived)
- [`AGENTS.md`](AGENTS.md) — project principles and structure
