# screening-abstracts

Screen a JSONL file of `Work` records by sending each abstract to a local
Ollama-hosted LLM whose system prompt — the classifier contract plus the
relevance criterion — is loaded from an external screening-criteria YAML.
Outputs a `ScreeningVerdict` sidecar covering every work in the input — one
verdict per work regardless of score.

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

# pin the run-id (otherwise a fresh <iso>_<12hex> is generated)
laglitsynth screening-abstracts input.jsonl --run-id 2026-05-03T14-30-00_abc123def456

# rerun with the configuration of a prior run (fresh run-id)
laglitsynth screening-abstracts input.jsonl --config data/screening-abstracts/<run-id>/config.yaml

# prompt tuning: process first 20 works, print verdicts, don't write output
laglitsynth screening-abstracts input.jsonl --dry-run --max-records 20
```

## CLI arguments

| Argument | Description |
|---|---|
| `INPUT` (positional) | Input JSONL file path. Resolved from `data/manifest.json`'s `catalogue-dedup` entry when omitted; required when there is no manifest. See [run-manifest.md](run-manifest.md). |
| `--screening-criteria` | Screening-criteria YAML carrying a `system_prompt` field (default: [`examples/screening-criteria/lagrangian-oceanography.yaml`](../examples/screening-criteria/lagrangian-oceanography.yaml)). |
| `--data-dir` | Bucket root for stage outputs (default: `data/`). |
| `--run-id` | Run identifier. The flag wins; otherwise the manifest's shared `run_id`; otherwise a generated `<iso>_<12hex>`. A review with a manifest therefore reuses one run directory across invocations, and a re-run rewrites that run's `verdicts.jsonl` — pass `--run-id` explicitly to keep an earlier run. |
| `--config` | YAML config file whose values seed argparse defaults; explicit CLI flags override. |
| `--model` | Ollama model name (default: `gemma3:4b`). |
| `--screening-threshold` | Relevance score cutoff, 0--100 as a `float` (default: `50.0`). |
| `--base-url` | Ollama API base URL (default: `http://localhost:11434`). |
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
```

## Output format

Each run produces three files in `<data-dir>/screening-abstracts/<run-id>/`:

- **`verdicts.jsonl`** — one `ScreeningVerdict` per input work, appended
  per-record so a partial file from a killed run is still valid JSONL.
- **`screening-meta.json`** — `ScreeningMeta` sidecar with nested `run`
  and `llm` blocks, threshold, input path, input count, `criterion` (the
  loaded `system_prompt`), and above/below/skipped counts. Written upfront
  with zeroed counts when the run starts and rewritten with the real counts
  at the end, so a mid-run reviewer export still sees the criterion + LLM
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

Three fixed strings mark non-LLM outcomes:

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

All other `reason` values are the LLM's free-text justification.
`raw_response` is set on successful verdicts and on `llm-parse-failure`
sentinels; it is `None` on `no-abstract` and `llm-timeout` sentinels.

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
- **`llm`** (`LlmMeta`): `model`, `temperature` (explicit; currently `0.8`),
  `prompt_sha256` (sha256 of `system_prompt + "\n" + USER_TEMPLATE`, 64 hex
  chars, where `USER_TEMPLATE` is the user-render marker).

The loaded `system_prompt` is also stored verbatim on the meta as
`criterion`, so the reviewer export embeds the exact question the LLM saw.

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

## Human review export

`laglitsynth screening-abstracts-export` joins `verdicts.jsonl` with the
dedup catalogue and writes an XLSX review workbook for human
spot-checking. The export is XLSX-only — `verdicts.jsonl` is the
machine-readable form, the workbook is the human one. (The earlier CSV
path existed only to round-trip `reviewer_decision` / `reviewer_reason`
into a stage-4 ingestor that the verdicts-only cutover deleted; it has
been retired along with the `--format` flag.)

The workbook has one `Index` sheet plus one tab per included work. The
per-work tab puts the bibliographic block (title, authors, journal,
year, doi, openalex link, abstract) at the top, the screening criterion
+ reviewer-score cells in the middle, and the LLM verdict in a collapsed
group at the bottom — so the reviewer scores without being primed by the
LLM's number, with the LLM's verdict one click away for cross-check.

```bash
laglitsynth screening-abstracts-export \
    --verdicts data/screening-abstracts/<run-id>/verdicts.jsonl \
    --catalogue data/catalogue-dedup/deduplicated.jsonl

# Spot-check a reproducible random sample of 30 works.
laglitsynth screening-abstracts-export \
    --verdicts data/screening-abstracts/<run-id>/verdicts.jsonl \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --n-subset 30 --subset-seed 1
```

Default output: `<verdicts parent>/review.xlsx`. Override with `--output`.
`--meta` defaults to `<verdicts parent>/screening-meta.json` and is read
to embed the screening criterion (the loaded `system_prompt`) and the LLM
fingerprint (`model`, `temperature`, `prompt_sha256`) into each per-work
sheet. A `work_id` present in the verdicts file but absent from the
catalogue aborts the export — the two inputs are expected to come from
the same pipeline run.

### Sampling

`--n-subset N` draws a uniform random sample of `N` verdicts using
`--subset-seed` (default: `0`) and emits them in their original verdict-file
order. When `N >= len(verdicts)` or `--n-subset` is unset the whole set is
emitted — the same command covers both "spot-check 30" and "all of them."

### Sheet layout

The `Index` sheet has three reviewer-identity rows above the table —
`reviewer_name`, `reviewer_email`, `review_date` — followed by the
header row at row 5 and one data row per included work from row 6:
`work_id`, `title`, `authors`, `journal`, `year`, `sheet` (hyperlink
into the per-work tab). The Index carries no LLM-derived columns:
the reviewer triages by bibliography only and forms an opinion before
seeing what the LLM said.

Each per-work `W<id>` sheet uses a vertical `Field | Value` layout:

- Row 1: `← back to Index` hyperlink.
- Rows 3–9: `title`, `authors`, `journal`, `publication_year`, `doi`
  (clickable), `openalex` (clickable), `abstract` (wrapped).
- Row 11: `criterion` — the loaded `system_prompt` verbatim from
  `screening-meta.json`.
- Row 12: `scoring_instructions` — `Score 0% (not relevant) to 100%
  (perfectly relevant)`.
- Rows 13–15: `reviewer_score`, `reviewer_reason`, `reviewer_remarks`
  (free-form general feedback on the record), pre-filled with
  placeholder strings the reviewer overwrites.
- Row 17: `LLM details (expand to peek)` — header for a collapsed
  block that holds `llm_score` (percent), `llm_reason`, `llm_model`,
  `llm_temperature`, `llm_prompt_sha256`, `llm_raw_response`. Excel,
  LibreOffice and Numbers all honour openpyxl's row outlining; the
  reviewer has to actively expand to see the LLM's verdict.

Sheet names are the trailing OpenAlex id (e.g. `W3213722062`);
collisions are suffixed `_2`, `_3`, …. `no-abstract` verdicts are
filtered out — there is nothing for the human to score from. The
other null-score sentinels (`llm-parse-failure`, `llm-timeout`) still
get a per-work sheet because the abstract is present: `llm_score` is
blank, `llm_reason` carries the sentinel string, and `llm_raw_response`
is blank for `llm-timeout` and carries the malformed text for
`llm-parse-failure`.

The export is read-only: edits to LLM cells are ignored at ingest.
