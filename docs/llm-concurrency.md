# LLM-stage concurrency

The LLM-driven stages (currently only
[screening-abstracts](screening-abstracts.md); eligibility and
extraction will follow) can dispatch multiple in-flight LLM requests
against a single Ollama server. Throughput scales roughly linearly
with the product of two knobs — **client-side concurrency** and
**server-side `OLLAMA_NUM_PARALLEL`** — up to the GPU's
memory/compute limit.

## How it works

Each LLM-driven stage runs a `ThreadPoolExecutor` of configurable
size. The main thread submits one LLM call per input work to the
pool, then drains results via `concurrent.futures.as_completed` and
appends them to the output JSONL. Workers are pure LLM-call
functions; only the main thread writes the output file, so there's
no lock, no shared file handle, no producer/consumer queue.

Sentinel verdicts for works that need no LLM call (e.g.
`no-abstract` in stage 3) are emitted synchronously by the main
thread during the submit loop, so no worker is wasted on them.

## Configuration

Two settings must match for real parallelism:

### Client: `--concurrency N`

Each LLM-driven stage's CLI takes `--concurrency N` (default `1` —
legacy sequential behaviour). This is the `ThreadPoolExecutor` size
and therefore the maximum number of LLM requests in flight from this
stage at any moment.

### Server: `OLLAMA_NUM_PARALLEL=N`

Ollama reads the `OLLAMA_NUM_PARALLEL` environment variable at
`ollama serve` start time. It caps how many requests the server will
process concurrently against a loaded model (the rest queue). With
`OLLAMA_NUM_PARALLEL=1` (the Ollama default) a higher
`--concurrency` is wasted — client requests just queue on the
server.

Set `OLLAMA_NUM_PARALLEL` **before** starting `ollama serve`; it is
not honoured if exported afterwards.

## Ordering

Verdicts appear in the output JSONL in this order:

1. All sentinel verdicts (no LLM call) in catalogue order.
2. All LLM-backed verdicts in **completion order** (not catalogue
   order).

JSONL is keyed by `work_id`, and every downstream consumer joins on
that key, so completion order is functionally invisible after the
stage. Adjudication exports re-sort by whatever the reviewer prefers
(see [screening-abstracts.md](screening-abstracts.md)).

At `--concurrency 1` the ordering degrades to catalogue order (one
request at a time), matching the pre-parallel behaviour.

## Clean rerun, not resume

The output file is truncated at the start of each run and verdicts
are appended as workers complete. A killed job leaves a valid
partial JSONL — every line parses — but the stage does **not**
support resuming mid-catalogue. Every rerun starts from scratch.
Resume would require matching input hashes, prompt digests, and
model/seed state; that engineering is deferred until throughput
itself stops being the bottleneck.

## VRAM trade-off

On a V100 32 GB running `gemma3:4b` (~3 GB model), Ollama's per-slot
KV cache eats VRAM linearly with `OLLAMA_NUM_PARALLEL`. Screening
prompts are short so the KV cache per slot is small; the practical
ceiling is more like 8–16 before you hit context-budget warnings.
For larger models (`gemma3:12b`, `llama3.1:8b`), start lower —
`OLLAMA_NUM_PARALLEL=2–4` — and watch the server log for
`ggml_backend_cuda_buffer_type_alloc_buffer` or similar OOMs.

## Scaling past one GPU

Two shapes, both still on the table:

1. **Multi-endpoint client.** Run one Ollama per GPU (different
   ports), pass multiple base URLs to the CLI, round-robin in the
   client. Needs code.
2. **Catalogue shards as a Slurm array.** Split the deduplicated
   catalogue into K shards, submit a K-job array, each job runs a
   single-node parallel stage 3 against its own local Ollama; merge
   shard verdicts with `cat shard-*.jsonl`. No code change beyond
   shard/merge shell.

Single-node parallelism should exhaust the single-V100 throughput
first; the array shard is the next step once one GPU stops being
enough.

## Practical example

On NESH, [scripts/nesh-test-run.sbatch](../scripts/nesh-test-run.sbatch)
exports `OLLAMA_NUM_PARALLEL=3` before `ollama serve` and
`SCREENING_CONCURRENCY=3` before invoking the smoke-test.
[scripts/test-run-all-stages.sh](../scripts/test-run-all-stages.sh)
forwards `SCREENING_CONCURRENCY` to the stage-3 CLI as
`--concurrency`. Override either env var at `sbatch --export=...`
time to try different settings.
