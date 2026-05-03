# LLM-stage concurrency

The LLM-driven stages (currently only
[screening-abstracts](screening-abstracts.md); eligibility and
extraction will follow) can dispatch multiple in-flight LLM requests
against a single Ollama server. Throughput is shaped by two knobs —
**client-side concurrency** and **server-side `OLLAMA_NUM_PARALLEL`**
— and the right relationship between them is not 1:1.

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

## Two knobs, one relationship

### Server: `OLLAMA_NUM_PARALLEL=N`

Read by Ollama at `ollama serve` start time (it is **not** honoured
if exported afterwards). Caps how many requests the server will
process concurrently against a loaded model — additional requests
queue inside Ollama. With the Ollama default `1` a higher client
concurrency is wasted: client requests just stack at the server.

### Client: `--concurrency M` (`LLM_CONCURRENCY` in the runner)

Each LLM-driven stage's CLI takes `--concurrency M`. This is the
`ThreadPoolExecutor` size and therefore the maximum number of LLM
requests in flight from this stage at any moment.

### Why M ≠ N — overbook the client

`OLLAMA_NUM_PARALLEL` caps in-flight **GPU work**, but each request
also has CPU-side overhead — HTTP, JSON parse/serialise, pydantic
validation, response assembly. With strict `M = N` the GPU goes
idle every time a worker is between calls. Mild overbooking
(`M = N + 2`, or roughly `1.5 × N`) keeps the server's queue one
deep so the GPU never waits for the next prompt to arrive.

The pattern is visible in the benchmark grid in
[ollama-throughput.md](explorations/ollama-throughput.md): the H100
short-prompt peak (6.02 cps) sits at `parallel=2, threads=4` —
**2× overbooked**. The V100 multi-GPU peak (8.56 cps per node) is
at `parallel=1, threads=8` — **8× overbooked**, because the V100 is
already at its memory-bandwidth ceiling at batch=1 so the server
slots themselves do nothing and the only useful concurrency is
client-side.

Don't push overbooking arbitrarily high: every in-flight client
request also costs server-side KV-cache memory and Python-side
threading overhead. `N + 2` to `2N` is the practical band.

## Production defaults

The runner derives the client knob from the server knob, so they
can't drift:

```bash
OLLAMA_NUM_PARALLEL=2          # server cap
LLM_CONCURRENCY=$((OLLAMA_NUM_PARALLEL + 2))   # client = N + 2 = 4
```

`OLLAMA_NUM_PARALLEL=2` is a defensible single-node compromise across
the LLM stages: V100 is GPU-bound at any setting, H100 short prompts
peak at server `parallel=2`, and H100 long prompts plateau by
`parallel=3–4` (so 2 leaves <10% on the table). If a single stage
becomes the bottleneck, override `OLLAMA_NUM_PARALLEL` at submit
time — `LLM_CONCURRENCY` follows automatically.

[`scripts/nesh-pipeline.sbatch`](../scripts/nesh-pipeline.sbatch)
exports both before launching `ollama serve` and the runner.
[`scripts/run-pipeline.sh`](../scripts/run-pipeline.sh) forwards
`LLM_CONCURRENCY` to the stage-3 CLI as `--concurrency`. Override
either env var at `sbatch --export=...` time.

## Stages 7 and 8 do not yet honour `LLM_CONCURRENCY`

Only `screening-abstracts` (stage 3) currently has a `--concurrency`
flag. `fulltext-eligibility` (stage 7) and `extraction-codebook`
(stage 8) call Ollama sequentially. The runner forwards
`LLM_CONCURRENCY` only to stage 3 — extending the same
ThreadPoolExecutor pattern to stages 7 and 8 is a follow-up.

For stage 8 in particular, the speedup from client-side concurrency
is bounded: prefill dominates the call (~90% of wall time on the
TEI prompt) and Ollama serialises prefill across requests. See
[ollama-throughput.md](explorations/ollama-throughput.md) for the
flat throughput grid and the architectural reason. Real speedup on
stage 8 needs a continuous-batching engine (vLLM, SGLang), not more
client threads.

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

Note: the +2 client overbook does not multiply this — only the
server slots allocate KV cache. The extra in-flight client threads
just queue on the socket.

## Scaling past one GPU

The single-node setup above exhausts a single-GPU node first. Past
that, the natural shape is a **Slurm-array shard**: split the
deduplicated catalogue into K shards, submit a K-job array, each
job runs the runner against its own local Ollama on its own GPU,
merge shard verdicts with `cat shard-*.jsonl`. No code change
beyond shard/merge shell. Multi-GPU within one node and a
multi-endpoint client are alternatives that require code; the
multi-GPU benchmark numbers are kept in
[ollama-throughput.md](explorations/ollama-throughput.md) for when
that becomes relevant.
