# screening-abstracts

Screen a JSONL file of `Work` records by scoring each abstract against a
relevance criterion loaded from an external screening-criteria YAML.
Outputs a `ScreeningVerdict` sidecar covering every work in the input — one
verdict per work regardless of score. Two backends do the scoring: a local
Ollama-hosted chat LLM given the YAML's `system_prompt` (the default), or a
hosted decisions model asked the YAML's `decision` question; see
[Backends](#backends).

The system prompt lives in a screening-criteria YAML (default
[`examples/screening-criteria/lagrangian-oceanography.yaml`](../examples/screening-criteria/lagrangian-oceanography.yaml)),
exactly as stage 7 loads its eligibility-criteria YAML. Swapping the review
topic is configuration work — edit the `system_prompt` field — not a code
change.

## Prerequisites

See [external-services.md](external-services.md) for Ollama and GROBID setup.
Quick start: `ollama pull gemma3:4b` (the default model, ~2.5 GB).

## Usage

```bash
# basic screening (uses the default screening-criteria YAML)
laglitsynth screening-abstracts data/catalogue-dedup/deduplicated.jsonl

# point at a different review topic
laglitsynth screening-abstracts input.jsonl \
  --screening-criteria examples/screening-criteria/my-topic.yaml

# stricter threshold (float)
laglitsynth screening-abstracts input.jsonl --screening-threshold 70

# bucket root override (e.g. for sweeps writing into a sibling tree)
laglitsynth screening-abstracts input.jsonl --data-dir runs/sweep-A

# keep an earlier run: write under a run-id other than the manifest's
laglitsynth screening-abstracts input.jsonl --run-id 2026-05-03T14-30-00_abc123def456

# rerun with the configuration of a prior run
laglitsynth screening-abstracts input.jsonl --config data/screening-abstracts/<run-id>/config.yaml

# prompt tuning: process first 20 works, print verdicts, don't write output
laglitsynth screening-abstracts input.jsonl --dry-run --max-records 20

# hosted decisions model instead of local Ollama (key from .env)
laglitsynth screening-abstracts --backend decisions --model typesafe/jev-1.14 --concurrency 4
```

## CLI arguments

| Argument | Description |
|---|---|
| `INPUT` (positional) | Input JSONL file path (default: `<data-dir>/catalogue-dedup/deduplicated.jsonl`). |
| `--screening-criteria` | Screening-criteria YAML carrying a `system_prompt` field (default: [`examples/screening-criteria/lagrangian-oceanography.yaml`](../examples/screening-criteria/lagrangian-oceanography.yaml)). |
| `--data-dir` | Bucket root for stage outputs (default: `data/`). |
| `--run-id` | Run identifier. The flag wins; otherwise the `run_id` in `<data-dir>/manifest.json`; otherwise the stage exits. A re-run under the same run-id rewrites that run's `verdicts.jsonl` and says so on stderr — pass `--run-id` to keep an earlier run. See [run-manifest.md](run-manifest.md). |
| `--config` | YAML config file whose values seed argparse defaults; explicit CLI flags override. |
| `--backend` | `ollama` (default) or `decisions`. See [Backends](#backends). |
| `--model` | Model name for the chosen backend (default: `gemma3:4b`, an Ollama tag). |
| `--api-key` | API key for `--backend decisions`; falls back to `OPENROUTER_API_KEY` in `.env`. |
| `--decisions-url` | Decisions endpoint (default: `https://openrouter.ai/api/alpha/decisions`). |
| `--screening-threshold` | Relevance score cutoff, 0--100 as a `float` (default: `50.0`). |
| `--base-url` | Ollama API base URL (default: `http://localhost:11434`); unused by the decisions backend. |
| `--pass-all` | Write an `unscreened` verdict per work and call no model. See [Passing everything through](#passing-everything-through---pass-all). |
| `--max-records` | Process only the first N works. Useful with `--dry-run`. |
| `--dry-run` | Print verdicts to stderr without writing any output files. |
| `--concurrency` | In-flight LLM requests (default: `1`). See [llm-concurrency.md](llm-concurrency.md). |

The resolved output directory is `<data-dir>/screening-abstracts/<run-id>/`.
See [configs.md](configs.md) for the config-file precedence rule and the
inlining behaviour of file-valued args: the `screening_criteria` YAML is
inlined into the saved `config.yaml` on every run (like the eligibility-criteria
and codebook stages) so a run stays interpretable after the source file moves.

## Screening-criteria YAML

The `system_prompt` field carries the whole system message: the classifier
contract (return JSON with `relevance_score` 0--100 and `reason`, JSON only)
and the relevance criterion (the question the work is scored against). The
shape mirrors the stage-7 eligibility-criteria YAML, so the same loader reads
both. The user message is then just the work listing —
`title`/`authors`/`year`/`abstract` — with no criterion prefix; the criterion
lives entirely in the system prompt.

```yaml
id: lagrangian-oceanography
description: |
  Relevance criterion for a systematic review ...
system_prompt: |-
  You are a relevance classifier for academic paper abstracts.
  ...
  On a scale from 0% (not relevant) to 100% (perfectly relevant), how
  relevant is this work to Lagrangian particle tracking in oceanography?
  ...
  Return ONLY the JSON object, nothing else.
decision:
  instructions: >-
    Is this work relevant to Lagrangian particle tracking in oceanography?
  criteria:
    "true": The work tracks particles, tracers or objects in an ocean flow field, ...
    "false": The work is about something else, or applies Lagrangian particle tracking outside the ocean.
```

The optional `decision` block is the same criterion as one yes/no question
for the decisions backend; keep the two in step when the scope changes.

## Backends

### `ollama` (default)

A chat completion against the Ollama server at `--base-url`, system prompt
from the YAML, user message the work listing, JSON response parsed into
the verdict. Temperature `0.8` with a random per-call seed, both recorded.
The Ollama preflight (server reachable, model pulled) runs first.

### `decisions`

One request per work to a decisions model through OpenRouter's decisions
endpoint (`--decisions-url`), authenticated with `--api-key` or
`OPENROUTER_API_KEY` from `.env`. The request carries the same work listing
as `state` and the YAML's `decision` block as a single `noul` (yes/no)
question; the answer is a probability that the work is relevant, which
becomes `relevance_score = round(100 * p)`. The model returns no
justification, so `reason` is `null`; the raw answer JSON (with the exact
probability) is kept in `raw_response`. No Ollama preflight runs; a missing
key or a criteria file without a `decision` block exits before any request.
Retries: transport errors, HTTP 5xx and 429 are retried four times with a
growing backoff, then the work gets the `llm-timeout` sentinel; a 2xx body
without the expected answer gets `llm-parse-failure`. In the meta,
`llm.temperature` is `null` and `prompt_sha256` hashes the question's
canonical JSON plus the user-render marker, so two runs with the same
question compare equal. Measured on TypeSafe Jev 1.14: about 0.3 s per
abstract sequentially and 0.00003 USD each.

Decisions models on OpenRouter are listed by
`GET /api/v1/models?output_modalities=decisions`; `typesafe/jev-1.14` and
`openai/gpt-6-luna-decisions` were both probed and agreed on 37 of 40
abstracts.

## Output format

Each run produces three files in `<data-dir>/screening-abstracts/<run-id>/`:

- **`verdicts.jsonl`** — one `ScreeningVerdict` per input work, appended
  per-record so a partial file from a killed run is still valid JSONL.
- **`screening-meta.json`** — `ScreeningMeta` sidecar with nested `run`
  and `llm` blocks, threshold, input path, input count, `criterion` (the
  loaded `system_prompt`), and above/below/skipped counts. Written upfront
  with zeroed counts when the run starts and rewritten with the real counts
  at the end, so a mid-run reader still sees the criterion + LLM
  fingerprint.
- **`config.yaml`** — fully-resolved CLI+config values for this run,
  excluding `run_id` and `--config` itself. Replay via
  `--config <run-dir>/config.yaml` (a fresh run-id is generated).

### ScreeningVerdict fields

| Field | Type | Description |
|---|---|---|
| `work_id` | `str` | OpenAlex work ID |
| `relevance_score` | `int \| null` | 0–100 from LLM; `null` for sentinel reasons |
| `reason` | `str \| null` | LLM justification string, or a sentinel reason code |
| `seed` | `int \| null` | Ollama random seed passed for this call; `null` for sentinel reasons |
| `raw_response` | `str \| null` | LLM's raw message content; `null` when no call was made |

### Sentinel reason values

Four fixed strings mark non-LLM outcomes:

- `reason="no-abstract"` — the work had no abstract; the LLM was not
  called. `relevance_score=null`, `seed=null`, `raw_response=null`.
- `reason="llm-parse-failure"` — the LLM returned a response that could
  not be parsed. `relevance_score=null`, `seed=null`, `raw_response`
  carries the raw message for audit.
- `reason="llm-timeout"` — the OpenAI client raised `APITimeoutError` /
  `APIConnectionError` after all retries were exhausted. The
  per-work call site catches the exception so a single hang does not
  kill the whole stage. `relevance_score=null`, `seed=null`,
  `raw_response=null`.
- `reason="unscreened"` — the run was made with `--pass-all` (below); no
  LLM was called for any work. `relevance_score=null`, `seed=null`,
  `raw_response=null`.

All other `reason` values are the LLM's free-text justification, or `null`
on a decisions-backend verdict (the model gives none). `raw_response` is
set on successful verdicts and on `llm-parse-failure` sentinels; it is
`None` on the other sentinels.

Downstream stages never filter on a `null` score, so every sentinel passes
the threshold gate.

### Passing everything through: `--pass-all`

`screening-abstracts --pass-all` writes an `unscreened` verdict for every
input work and contacts no model: no Ollama preflight, no criteria file
loaded. The run dir is otherwise normal, so stage 5 and the export take the
whole catalogue through the usual join. Use it to retrieve PDFs before
deciding on screening; a later real run under a new run-id screens the same
catalogue, and the PDFs already held are kept. The meta's `llm` block is
`null` and `criterion` is empty on such a run; `config.yaml` records
`pass_all: true`.

### LLM input format

The system message is the loaded `system_prompt` (contract + criterion).
The user message is a bare "standard literature listing" block — title,
authors, year, abstract — so the human reviewer (who sees the same fields)
and the LLM score against symmetric context. Missing title or year render
as `<unknown>`; an empty author list renders as `<unknown>`. Works with
no abstract get the `no-abstract` sentinel and skip the LLM call.

### Timeouts and retries

The OpenAI client is constructed with `timeout=60s` and
`max_retries=3` (4 total attempts, exponential backoff). These are
caps for "is the model wedged," not estimates of expected generation
latency — adjust the constants in `screen.py` if a real run hits them.

### ScreeningMeta fields

The meta sidecar nests two shared blocks:

- **`run`** (`RunMeta`): `tool`, `tool_version`, `run_at`, `validation_skipped`.
- **`llm`** (`LlmMeta | null`): `model`, `temperature` (`0.8` for the
  ollama backend, `null` for decisions), `prompt_sha256` (sha256 of
  `system_prompt + "\n" + USER_TEMPLATE` for ollama, of the decision
  question's canonical JSON + `"\n"` + `USER_TEMPLATE` for decisions; 64 hex
  chars, where `USER_TEMPLATE` is the user-render marker). `null` on a
  `--pass-all` run.

The loaded `system_prompt` (or the rendered decision question) is also
stored verbatim on the meta as `criterion`, so the exact question the
model saw is on record.

Alongside `above_threshold_count`, `below_threshold_count`, and
`skipped_count`, the meta records two failure counters:
`llm_parse_failure_count` (LLM responses that did not validate against the
verdict schema) and `llm_timeout_count` (calls that timed out or lost the
connection after retries). Both are `0` for a clean run.

The `prompt_sha256` is stable across runs with the same prompt wording.
Comparing it across meta files confirms that two runs used identical prompts.

## Tips for prompt tuning

Use `--dry-run --max-records N` to iterate on your screening criteria
without writing output files. This processes the first N works and prints
each verdict to stderr so you can quickly check whether the LLM is scoring
sensibly.

```bash
laglitsynth screening-abstracts input.jsonl \
  --screening-criteria examples/screening-criteria/my-topic.yaml \
  --dry-run --max-records 10
```

Edit the `system_prompt` field in the screening-criteria YAML and adjust
`--screening-threshold` until the above/below split looks right, then run
without `--dry-run` for the full set.

## Reproducibility

Stage 3 runs at `temperature=0.8` with a per-call integer `seed` drawn
independently for each work. The `seed` is recorded on the
`ScreeningVerdict` so a reviewer can see what was passed, but replaying the
same stage with the same prompt and model at `temperature > 0` will produce
different scores — Ollama honours `seed` only as a starting point, not as
a full deterministic replay guarantee. `prompt_sha256` does not cover the
seed, so two runs that differ only in their per-call seeds produce the same
hash. Use `prompt_sha256` to confirm prompt identity across runs; accept
score variance as inherent to non-zero temperature. If full reproducibility
is needed, set `temperature=0` and note that this changes the hash.
