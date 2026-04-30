# Ollama throughput on NESH V100/H100

Benchmark sweep comparing Ollama throughput across NESH GPU hardware
(V100 × 4 nodes, H100 × 2 nodes) and prompt shapes (stage-3 screening,
stage-8 extraction), to decide where to schedule which stage at scale.
Run on 2026-04-18 via [bench-ollama-concurrency.py](bench-ollama/bench-ollama-concurrency.py)
driven by [bench-ollama.sbatch](bench-ollama/bench-ollama.sbatch). Raw TSVs live at
`logs/bench-*-<jobid>.tsv` on the local machine; this doc captures the
numbers that decisions depend on.

## Method

- **Model**: `gemma3:4b` for short/synthetic-long prompts (screening
  regime); `llama3.1:8b` for real TEI prompts (stage-8 regime). Pulled
  once with a throwaway Ollama before the sweep so model-download is
  not measured.
- **Client**: `ThreadPoolExecutor` firing `N_CALLS` chat-completion
  requests; one warmup call per Ollama endpoint so model-load is not
  in the timed window.
- **Server**: one `ollama serve` per GPU, restarted per
  `OLLAMA_NUM_PARALLEL` value (the env var is read at startup).
- **Grid**: `OLLAMA_NUM_PARALLEL ∈ {1,2,3,4}` × client
  `threads ∈ {1,2,3,4}` (short prompt) or `{1,2,4}` (TEI). Multi-GPU
  runs use per-subshell `CUDA_VISIBLE_DEVICES=<one index>` to pin each
  Ollama to one GPU; client round-robins across URLs.
- **Prompt shapes**:
  - **short** — ~300-token screening-style question.
  - **long (synthetic)** — ~1.3k-token fake methods section.
  - **TEI** — real TEI rendered through stage 8's
    `extraction_codebook.prompts` helpers (~15k tokens,
    structured-JSON output).

## Single GPU, short prompt (screening regime)

`gemma3:4b`, `N_CALLS=30`.

### V100 (job 21963559)

| parallel \ threads | 1 | 2 | 3 | 4 |
|---:|---:|---:|---:|---:|
| 1 | 1.42 | 2.29 | 2.31 | 2.32 |
| 2 | 1.45 | 2.33 | 2.33 | 2.32 |
| 3 | 1.48 | 2.05 | 2.31 | 2.30 |
| 4 | 1.40 | 2.37 | 2.36 | 2.32 |

Peak **2.37 cps** (effectively flat ≥2.3 once `threads≥2`). Server-side
`OLLAMA_NUM_PARALLEL` does **nothing** — V100 is already at its
memory-bandwidth ceiling at batch=1, so adding parallel slots just
queues.

### H100 (job 21963560)

| parallel \ threads | 1 | 2 | 3 | 4 |
|---:|---:|---:|---:|---:|
| 1 | 2.41 | 3.83 | 3.80 | 3.82 |
| 2 | 2.44 | 3.96 | 5.94 | **6.02** |
| 3 | 2.43 | 4.47 | 5.58 | 5.99 |
| 4 | 2.42 | 3.99 | 5.43 | 5.79 |

Peak **6.02 cps** at `(parallel=2, threads=4)`. H100 benefits from
server-side parallelism — HBM3 bandwidth (~3.35 TB/s vs V100's
~0.9 TB/s) is not saturated at batch=1, so concurrent requests raise
arithmetic intensity.

### H100 + FlashAttention + q8_0 KV cache (job 21963713)

| parallel \ threads | 1 | 2 | 3 | 4 |
|---:|---:|---:|---:|---:|
| 1 | 2.42 | 3.80 | 3.79 | 3.77 |
| 2 | 2.42 | 3.87 | 6.07 | 5.45 |
| 3 | 2.41 | 3.96 | 5.40 | 5.93 |
| 4 | 2.41 | 3.98 | 5.38 | **6.27** |

Peak **6.27 cps** — a **+4%** lift over the plain H100 baseline,
within run-to-run noise. For short prompts, attention is a small share
of total compute (prefill is only ~300 tokens), so FlashAttention's
savings don't materialize; and `q8_0` KV cache saves memory, not
bandwidth-per-token.

**Verdict for stage 3**: V100 peaks at ~2.37 cps, H100 at ~6.02 cps.
H100 is ~2.5× faster per GPU.

Multi-GPU within one node was also measured but is parked in the
[appendix](#appendix-multi-gpu-scale-up) — single-node single-GPU is
the prod shape; multi-GPU only matters once that becomes the
bottleneck.

## Single GPU, long prompt

Where the answer starts to differ by hardware.

### H100 synthetic long prompt + FA + q8_0 (job 21963751)

`gemma3:4b`, ~1.3k-token synthetic methods section, `N_CALLS=20`,
`OLLAMA_CONTEXT_LENGTH=4096`.

| parallel \ threads | 1 | 2 | 3 | 4 |
|---:|---:|---:|---:|---:|
| 1 | 1.10 | 1.33 | 1.32 | 1.32 |
| 2 | 1.10 | 1.63 | 1.85 | 1.79 |
| 3 | 1.11 | 1.61 | 1.83 | 2.03 |
| 4 | 1.11 | 1.61 | 1.83 | **2.05** |

Peak 2.05 cps — ~3× slower than short-prompt. Optimal
`OLLAMA_NUM_PARALLEL` shifts from 2 (short) to 3–4 (long).

### H100 real TEI + FA + q8_0 (job 21963960)

`llama3.1:8b`, ~15k-token real paper, `N_CALLS=8`,
`OLLAMA_CONTEXT_LENGTH=32768`.

| parallel \ threads | 1 | 2 | 4 |
|---:|---:|---:|---:|
| 1 | 0.10 | 0.10 | 0.10 |
| 2 | 0.10 | 0.10 | 0.09 |
| 4 | 0.09 | 0.10 | 0.10 |

**Completely flat at 0.10 cps.** No knob moves the needle.

### V100 real TEI (job 21963961)

`llama3.1:8b`, same TEI prompt, no FA (Volta unsupported).

| parallel \ threads | 1 | 2 | 4 |
|---:|---:|---:|---:|
| 1 | 0.03 | 0.04 | 0.05 |
| 2 | 0.03 | 0.04 | 0.05 |

Peak **0.05 cps**, exactly half of H100's. `threads` does lift V100
even at `OLLAMA_NUM_PARALLEL=1` — most likely because the long decode
phase on V100 (~10 s of each 34 s call) gives room for the next
request's CPU-side setup to run in parallel. H100's decode is too
short for this overlap to show.

### Why the H100 curve is flat (stage 8)

The stage-8 prompt is ~15k tokens in, ~500 tokens out. For each
request:

- Prefill dominates (~90% of call time) — one big forward pass over
  the whole prompt.
- Decode is short (~10% of call time) — 500 token-by-token passes.

Ollama's `OLLAMA_NUM_PARALLEL` interleaves **decode** across
concurrent requests but **serializes prefill**. Since prefill
dominates, the total stays roughly `N × prefill_time` regardless of
concurrent slots. This is the single biggest architectural gap
between Ollama and engines like **vLLM / SGLang**, which do
*continuous batching* — merging concurrent requests into one larger
GPU forward pass, prefill included. That's where the "5–10× on
concurrent workloads" number from external benchmarks comes from,
and it's exactly this regime.

## Bottom-line numbers (single GPU)

Peak throughput per single GPU, real data for stage 3, extrapolated
for stage 8:

| GPU | Stage 3 (measured) | Stage 8 (H100 measured, V100 extrapolated) |
|---|---|---|
| V100 | 2.37 cps | 0.05 cps |
| H100 | 6.02 cps | 0.10 cps |

Multi-GPU and multi-node projections (Slurm-array shard scale-out)
live in the [appendix](#appendix-multi-gpu-scale-up). At 100k-scale
the dominant wall-time cost is not LLM inference but Unpaywall rate
limits and GROBID throughput.

## Practical defaults

The prod runner sets `OLLAMA_NUM_PARALLEL=2` and derives client
`--concurrency = N + 2 = 4` (see
[../llm-concurrency.md](../llm-concurrency.md)). `parallel=2` is a
defensible compromise: V100 is GPU-bound at any setting; H100 short
prompts peak there; H100 long prompts are within ~10% of their
plateau. Override `OLLAMA_NUM_PARALLEL` at submit time if a stage
becomes the bottleneck — the client knob follows.

- **Stage 8 ceiling**: `OLLAMA_NUM_PARALLEL=4` on H100 lifts the
  long-prompt curve only marginally above `parallel=1`; on V100 it
  doesn't matter. **No Ollama knob gets you to vLLM's numbers** —
  prefill serialisation is the architectural ceiling.
- **Scaling out**: prefer the Slurm-array shard shape — one Ollama
  per GPU, one shard per job — over a multi-endpoint client. Simpler
  code, no cross-node HTTP, verdict JSONL files merge with
  `cat shard-*.jsonl`.

## Things we did not measure

- **vLLM / SGLang** against the same TEI prompt. High expected
  payoff; high integration cost (different API, different container
  story on NESH). Worth revisiting if stage 8 becomes the dominant
  wall-time cost.
- **Smaller models on TEI prompt.** The 7B class (mistral, llama2) is
  ~27% faster than llama3.1:8b on pure decode in external benchmarks,
  but prefill (which dominates our workload) scales differently.
  Would need a same-prompt A/B plus a quality check on the 30-field
  JSON schema — the reason llama3.1:8b was chosen was that 4B models
  failed the schema.
- **Flash attention on long prompts specifically.** The FA-enabled
  runs (H100 short, H100 synthetic long, H100 TEI) do not have
  no-FA counterparts at the same prompt shape, so we can't isolate
  the FA contribution on long prompts. The short-prompt no-result
  (+4%) is all we measured.
- **H100 × 2 on stage 8.** The H100 TEI bench was single-GPU. Peak
  node projection (~0.2 cps) assumes multi-GPU scales the same way
  as for short prompts; we don't have direct confirmation.

## Appendix: multi-GPU scale-up

Kept here for the future day when one GPU stops being enough. Prod
runs single-GPU (one Ollama, one shard per Slurm job).

Setup: one Ollama per GPU on different ports, client round-robins
across base URLs.

### V100 × 4, `OLLAMA_NUM_PARALLEL=1` per server (job 21963591)

`gemma3:4b` short prompt, `N_CALLS=60`.

| GPUs \ threads | 1 | 2 | 4 | 8 |
|---:|---:|---:|---:|---:|
| 1 | 1.60 | 2.71 | 2.69 | 2.69 |
| 2 | 1.62 | 3.11 | 5.11 | 5.06 |
| 3 | 1.61 | 3.12 | 5.77 | 7.21 |
| 4 | 1.60 | 3.11 | 5.95 | **8.56** |

Near-linear scaling (3.16× from 4 GPUs, ~0.79× per-GPU efficiency).

### H100 × 2, `OLLAMA_NUM_PARALLEL=2` per server (job 21963592)

`gemma3:4b` short prompt, `N_CALLS=60`.

| GPUs \ threads | 1 | 2 | 4 | 8 |
|---:|---:|---:|---:|---:|
| 1 | 2.40 | 3.62 | 6.05 | 5.84 |
| 2 | 2.40 | 4.51 | 7.31 | **9.32** |

Sub-linear: 1.54× from 2 GPUs at `parallel=2` (each GPU is already
working harder, so the marginal GPU buys less).

### Per-node bottom line

| Node | Stage 3 peak cps | At |
|---|---|---|
| V100 × 4 | **8.56** | parallel=1, threads=8 |
| H100 × 2 | **9.32** | parallel=2, threads=8 |

Per-node a wash (~9% apart). Per-GPU H100 is ~2.2× faster, but V100
nodes have 2× the GPUs, so they converge. **Queue wait will dominate
the choice between V100 and H100 nodes.**

With 2 H100 + 4 V100 nodes assigned concurrently (Slurm-array shards,
no cross-node coordination):

- Stage 3: ~53 cps → 50 000 abstracts in ~16 min.
- Stage 8: ~1.2 cps → 5 000 papers in ~1.2 h, 100 000 in ~24 h.

For realistic pipeline sizes (stage 8 sees a fraction of the
catalogue after screening + retrieval + eligibility), LLM inference
is not the dominant wall-time cost at 100k scale. **Unpaywall rate
limits (~100k/day)** and **GROBID throughput** (CPU scale-out on a
Slurm array) are the real bottlenecks.
