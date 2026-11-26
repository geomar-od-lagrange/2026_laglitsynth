# screening-abstracts

Screen a JSONL file of `Work` records by asking a decisions model, for each
abstract, a yes/no relevance question loaded from an external
screening-criteria YAML. Outputs a `ScreeningVerdict` sidecar covering every
work in the input — one verdict per work regardless of score. The answer's
probability that the work is relevant becomes `relevance_score = round(100 *
p)`.

The question lives in the `decision` block of a screening-criteria YAML
(default
[`examples/screening-criteria/lagrangian-oceanography.yaml`](../examples/screening-criteria/lagrangian-oceanography.yaml)).
Swapping the review topic is configuration work — edit the `decision` block
— not a code change.

## Prerequisites

An OpenAI-compatible endpoint that serves decisions, and its API key as
`LLM_API_KEY` (exported, or in `.env`; see
[.env.example](../.env.example)). See
[external-services.md](external-services.md).

## Usage

```bash
# basic screening (uses the default screening-criteria YAML)
laglitsynth screening-abstracts data/catalogue-dedup/deduplicated.jsonl \
  --base-url https://llm.example.org/v1 --model <decisions-model>

# point at a different review topic
laglitsynth screening-abstracts input.jsonl --base-url ... --model ... \
  --screening-criteria examples/screening-criteria/my-topic.yaml

# a provider that serves decisions off <base-url>/decisions
laglitsynth screening-abstracts input.jsonl --base-url ... --model ... \
  --decisions-url https://llm.example.org/alt/decisions

# keep an earlier run: write under a run-id other than the manifest's
laglitsynth screening-abstracts input.jsonl ... --run-id 2026-05-03T14-30-00_abc123def456

# rerun with the configuration of a prior run
laglitsynth screening-abstracts input.jsonl --config data/screening-abstracts/<run-id>/config.yaml

# criteria tuning: process first 20 works, print verdicts, don't write output
laglitsynth screening-abstracts input.jsonl ... --dry-run --max-records 20
```

## CLI arguments

| Argument | Description |
|---|---|
| `INPUT` (positional) | Input JSONL file path (default: `<data-dir>/catalogue-dedup/deduplicated.jsonl`). |
| `--screening-criteria` | Screening-criteria YAML carrying a `decision` block (default: [`examples/screening-criteria/lagrangian-oceanography.yaml`](../examples/screening-criteria/lagrangian-oceanography.yaml)). |
| `--data-dir` | Bucket root for stage outputs (default: `data/`). |
| `--run-id` | Run identifier. The flag wins; otherwise the `run_id` in `<data-dir>/manifest.json`; otherwise the stage exits. A re-run under the same run-id rewrites that run's `verdicts.jsonl` and says so on stderr — pass `--run-id` to keep an earlier run. See [run-manifest.md](run-manifest.md). |
| `--config` | YAML config file whose values seed argparse defaults; explicit CLI flags override. |
| `--base-url` | OpenAI-compatible API base URL, including `/v1`. Required. |
| `--model` | Decisions model name. Required. |
| `--api-key` | API key. Without it the stage reads `LLM_API_KEY` from the environment, then from `.env`, and exits naming `LLM_API_KEY` when none is set. Never written to `config.yaml`. |
| `--decisions-url` | Decisions endpoint (default: `<base-url>/decisions`). |
| `--max-records` | Process only the first N works. Useful with `--dry-run`. |
| `--dry-run` | Print verdicts to stderr without writing any output files. |
| `--concurrency` | In-flight LLM requests (default: `1`). See [llm-concurrency.md](llm-concurrency.md). |

The resolved output directory is `<data-dir>/screening-abstracts/<run-id>/`.
See [configs.md](configs.md) for the config-file precedence rule and the
inlining behaviour of file-valued args: the `screening_criteria` YAML is
inlined into the saved `config.yaml` on every run (like the eligibility-criteria
and codebook stages) so a run stays interpretable after the source file moves.

## Screening-criteria YAML

```yaml
id: lagrangian-oceanography
description: |
  Relevance criterion for a systematic review ...
decision:
  instructions: >-
    Is this work relevant to Lagrangian particle tracking in oceanography?
  criteria:
    "true": The work tracks particles, tracers or objects in an ocean flow field, ...
    "false": The work is about something else, or applies Lagrangian particle tracking outside the ocean.
```

A criteria file without a `decision` block exits before any request,
naming the file.

## Request

One `POST` per work to the decisions URL, in OpenAI's Decisions shape:

```json
{
  "model": "<model>",
  "input": "Title: ...\nAuthors: ...\nYear: ...\nAbstract: ...",
  "questions": [
    {"type": "predicate", "name": "relevant",
     "instructions": "<instructions>\n\nTrue if: <true>\nFalse if: <false>"}
  ]
}
```

A predicate question has no criteria field, so the YAML's `criteria.true`
and `criteria.false` follow its `instructions`. The probability is read
from the answer named `relevant`. The endpoint returns no justification,
so `reason` is `null` on a scored verdict; the response body is kept in
`raw_response`.

The `input` is a "standard literature listing" — title, authors, year,
abstract. Missing title or year render as `<unknown>`; an empty author list
renders as `<unknown>`. Works with no abstract get the `no-abstract`
sentinel and no request.

### Errors and retries

Transport errors, HTTP 5xx and 429 are retried four times with a growing
backoff; then the work gets the `llm-timeout` sentinel. HTTP 401, 402, 403
and 404 fail every work the same way, so the stage exits naming the URL.
Any other non-2xx response gives `llm-rejected` with the error body, and a
2xx body without a probability in [0, 1] for `relevant` gives
`llm-parse-failure` with the body attached.

## Output format

Each run produces three files in `<data-dir>/screening-abstracts/<run-id>/`:

- **`verdicts.jsonl`** — one `ScreeningVerdict` per input work, appended
  per-record so a partial file from a killed run is still valid JSONL.
- **`screening-meta.json`** — `ScreeningMeta` sidecar with nested `run`
  and `llm` blocks, input path, input count, and scored/skipped/sentinel
  counts. Written upfront with zeroed counts when the run starts and
  rewritten with the real counts at the end, so a mid-run reader still
  sees the LLM fingerprint.
- **`config.yaml`** — fully-resolved CLI+config values for this run,
  excluding `run_id`, `api_key` and `--config` itself. Replay via
  `--config <run-dir>/config.yaml` (a fresh run-id is generated).

### ScreeningVerdict fields

| Field | Type | Description |
|---|---|---|
| `work_id` | `str` | OpenAlex work ID |
| `relevance_score` | `int \| null` | `round(100 * p)`; `null` for sentinel reasons |
| `reason` | `str \| null` | A sentinel reason code; `null` on a scored verdict |
| `raw_response` | `str \| null` | Response or error body; `null` when no body was received |

### Sentinel reason values

- `no-abstract` — the work had no abstract; no request was sent.
  `raw_response=null`.
- `llm-timeout` — transport errors, 5xx or 429 on every attempt.
  `raw_response=null`.
- `llm-rejected` — the endpoint refused the request with another 4xx.
  `raw_response` carries the error body.
- `llm-parse-failure` — a 2xx body without a valid probability.
  `raw_response` carries the body.

Every sentinel has `relevance_score=null`.

The stage writes scores only; it applies no threshold. Which scores count
as relevant is decided downstream on `verdicts.jsonl`.

### ScreeningMeta fields

The meta sidecar nests two shared blocks:

- **`run`** (`RunMeta`): `tool`, `tool_version`, `run_at`, `validation_skipped`.
- **`llm`** (`LlmMeta`): `model`, `base_url`, `decisions_url`, and
  `prompt_sha256` — the sha256 of the decision question's canonical JSON
  + `"\n"` + `USER_TEMPLATE` (64 hex chars, where `USER_TEMPLATE` is the
  input-render marker).

Alongside `scored_count` (verdicts with a score) and `skipped_count`
(sentinels), the meta records `llm_parse_failure_count`,
`llm_timeout_count` and `llm_rejected_count`. All are `0` for a clean run.

The `prompt_sha256` is stable across runs with the same question.
Comparing it across meta files confirms that two runs asked the same
question; the exact wording is in the run's `config.yaml`.

## Tips for criteria tuning

Use `--dry-run --max-records N` to iterate on the screening criteria
without writing output files. This processes the first N works and prints
each verdict to stderr.

```bash
laglitsynth screening-abstracts input.jsonl --base-url ... --model ... \
  --screening-criteria examples/screening-criteria/my-topic.yaml \
  --dry-run --max-records 10
```

Edit the `decision` block until the scores look right, then run without
`--dry-run` for the full set.
