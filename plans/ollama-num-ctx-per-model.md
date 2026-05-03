# Per-stage Ollama tags synthesised in the sbatch

## Goal

Stage 8 of `nesh-pipeline-22062800` started but produced zero records
in 30+ minutes. The first `extraction-codebook` LLM call took >10
minutes and Ollama returned `500 | 10m0s`; the OpenAI client retried
to its limit and the same hang repeated, so
`data/run/extraction-codebook/` stayed empty.

`logs/ollama-22062800.log` is the smoking gun: the runner for
`llama3.1:8b` came up with `num_ctx=262144` requested (line 689) and
loaded with `n_seq_max=4`, `n_ctx_seq=131072`, total `n_ctx=524288`
(lines 824–826) — a ~65 GB KV cache. That doesn't fit on the 80 GB
GPU alongside the model weights, so `load_tensors: offloaded 15/33
layers to GPU` (line 820) and the rest spilled to CPU. Inference
became unusably slow.

The per-request `extra_body={"options": {"num_ctx": 32768}}` in
[`extract.py`](../src/laglitsynth/extraction_codebook/extract.py) and
[`eligibility.py`](../src/laglitsynth/fulltext_eligibility/eligibility.py)
did not size the runner — through the OpenAI-compat path the option
is unreliable. Ollama's only reliable knobs for per-model context
sizing are (a) Modelfile `PARAMETER num_ctx`, (b) the global
`OLLAMA_CONTEXT_LENGTH` env (which we can't differ across stages),
and (c) the per-request hint we just saw fail. Fix: synthesise three
per-stage tags via Modelfiles fed on stdin to `ollama create -f -`,
parameterised by env vars so we can sweep models and context lengths
at submit time without checked-in files.

Stage 7 has the same context budget as stage 8 (both render full
TEI) but runs `gemma3:4b` instead of `llama3.1:8b`; gemma is small
enough that even at the bloated context all 35/35 layers still fit
on GPU, which is why stage 7 looked fine in the same run. Stage 3
sends only abstracts and needs much less context than either.

## Non-goals

Restarting Ollama between stages with stage-specific env. Plausible
at first glance but the wrong axis: stage 7 and stage 8 share the
same context requirement and only the model differs, so a restart
between them wouldn't help. Per-model tags keep the choice
declarative and let one Ollama process serve all three LLM stages.

Tuning `OLLAMA_NUM_PARALLEL` per stage. Would require a restart and
the codebook stage's safe value is a fine global.

Sweeping over temperature in this plan. The `_TEMPERATURE = 0.8`
constants in stages 3, 7, 8 are not env-overridable today and
plumbing that needs Python edits — out of scope here, listed as a
follow-up. `num_ctx` and `model` are the two knobs this plan
exposes; both fit in a Modelfile.

Adding `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0`.
Established VRAM wins from
[bench-ollama.sbatch](../docs/explorations/bench-ollama/bench-ollama.sbatch)
but separate from this fix; listed as a follow-up.

Code changes. Python constants and prompt code stay untouched. The
artefact this plan ships is sbatch wiring.

## Target state

[`scripts/nesh-pipeline.sbatch`](../scripts/nesh-pipeline.sbatch)
gains six env-var defaults — one model and one `num_ctx` per LLM
stage:

```sh
SCREENING_MODEL="${SCREENING_MODEL:-gemma3:4b}"
SCREENING_NUM_CTX="${SCREENING_NUM_CTX:-8192}"
ELIGIBILITY_MODEL="${ELIGIBILITY_MODEL:-gemma3:4b}"
ELIGIBILITY_NUM_CTX="${ELIGIBILITY_NUM_CTX:-32768}"
EXTRACTION_MODEL="${EXTRACTION_MODEL:-llama3.1:8b}"
EXTRACTION_NUM_CTX="${EXTRACTION_NUM_CTX:-32768}"
```

After the existing `ollama pull` loop (which still pulls the upstream
model tags), three `ollama create` invocations synthesise the
per-stage tags from stdin. No Modelfile lives on disk:

```sh
"$OLLAMA_DIR/bin/ollama" create laglit-screen -f - <<EOF
FROM ${SCREENING_MODEL}
PARAMETER num_ctx ${SCREENING_NUM_CTX}
EOF

"$OLLAMA_DIR/bin/ollama" create laglit-eligibility -f - <<EOF
FROM ${ELIGIBILITY_MODEL}
PARAMETER num_ctx ${ELIGIBILITY_NUM_CTX}
EOF

"$OLLAMA_DIR/bin/ollama" create laglit-extract -f - <<EOF
FROM ${EXTRACTION_MODEL}
PARAMETER num_ctx ${EXTRACTION_NUM_CTX}
EOF
```

The `ollama pull` loop continues to pull the **upstream** tags
(`$SCREENING_MODEL` etc.) because `ollama create` needs the base
model present locally. After `create`, the pipeline references the
local tags exclusively.

The three model env vars exported to
[`scripts/run-pipeline.sh`](../scripts/run-pipeline.sh) flip from the
upstream tags to the local tags:

```sh
export SCREENING_MODEL=laglit-screen
export ELIGIBILITY_MODEL=laglit-eligibility
export EXTRACTION_MODEL=laglit-extract
```

(Done with a separate set of variable assignments after `ollama
create` so the original upstream values stay readable for the `pull`
and `create` steps — don't reuse the same names for both roles.)

`run-pipeline.sh` already takes the model names as env vars and
passes them through `--model`; no edit needed there.

The per-request `extra_body={"options": {"num_ctx": ...}}` in
[`extract.py`](../src/laglitsynth/extraction_codebook/extract.py) and
[`eligibility.py`](../src/laglitsynth/fulltext_eligibility/eligibility.py)
is left in place — harmless, and matches what the Modelfile bakes in
when the env defaults are used.

## Design decisions

Synthesise on stdin rather than check in three `.Modelfile` files.
The bake recipe (one `FROM` line + one `PARAMETER` line) is too
trivial to deserve a tracked file, and an on-disk file freezes the
values in two places. With heredoc-from-env, the sbatch is the
single source of truth and a sweep over `EXTRACTION_NUM_CTX=16384
24576 32768` is a one-liner at submit time.

Three tags rather than two. We do want the per-stage axis explicit,
even if eligibility and extraction happen to share `num_ctx=32768`
today — sweeps may diverge them, and naming
`laglit-eligibility`/`laglit-extract` separately makes log grep and
sweep matrices unambiguous.

`num_ctx` only in the Modelfile; not `temperature` or `seed`.
Temperature works fine via the per-request OpenAI field today
(unlike `num_ctx`, it doesn't trigger a runner reload), and seed is
already drawn per-call. Keeping `num_ctx` as the only baked
parameter keeps the Modelfile single-purpose: "size this runner
correctly."

## Implementation sequence

1. Edit [`scripts/nesh-pipeline.sbatch`](../scripts/nesh-pipeline.sbatch):
   add the six env-var defaults, the three heredoc `ollama create`
   invocations after the existing `ollama pull` loop, and the
   re-export of `*_MODEL` to the local tags before
   `pixi run scripts/run-pipeline.sh`. Ensure the `ollama pull` loop
   still receives the upstream tags. Update the comment block at the
   top to mention the new env vars among the override examples.
2. Update [README.md](../README.md) NESH section to list the new
   sweep knobs (`SCREENING_NUM_CTX`, `ELIGIBILITY_NUM_CTX`,
   `EXTRACTION_NUM_CTX`) and show one `sbatch --export=...` example
   that varies a `num_ctx`.
3. Smoke-test on NESH with the same `QUERY` / `N` as the failed run.
   Confirm `data/run/extraction-codebook/records.jsonl` grows and
   that `logs/ollama-*.log` shows `offloaded 33/33 layers to GPU`
   for the `laglit-extract` runner. Capture per-record latency for
   comparison with the failed run.

## Follow-ups

- Plumb temperature as an env-overridable per-stage knob (needs CLI
  flags or env reads in `screen.py`, `eligibility.py`, `extract.py`)
  so a sweep can vary it alongside model and `num_ctx`.
- Export `OLLAMA_FLASH_ATTENTION=1` and `OLLAMA_KV_CACHE_TYPE=q8_0`
  in the sbatch — known wins from
  [bench-ollama.sbatch](../docs/explorations/bench-ollama/bench-ollama.sbatch).
  Cheap, but the per-stage `num_ctx` fix is sufficient on its own.
- `OLLAMA_MAX_LOADED_MODELS=1` to forbid runner coexistence — only
  worth it if a future run shows the `laglit-screen` runner
  lingering into stage 8 and pressuring VRAM.

## Risks

`ollama create` rejects the synthesised Modelfile (e.g. heredoc
quoting, missing base model). Mitigation: `set -e` in the sbatch
already aborts on non-zero exit, and the upstream tag is `pull`ed
in the preceding loop. The smoke test catches any wiring slip.

## Critical files

- [scripts/nesh-pipeline.sbatch](../scripts/nesh-pipeline.sbatch)
- [scripts/run-pipeline.sh](../scripts/run-pipeline.sh)
- [src/laglitsynth/extraction_codebook/extract.py](../src/laglitsynth/extraction_codebook/extract.py)
- [src/laglitsynth/fulltext_eligibility/eligibility.py](../src/laglitsynth/fulltext_eligibility/eligibility.py)
- [src/laglitsynth/screening_abstracts/screen.py](../src/laglitsynth/screening_abstracts/screen.py)
- [docs/llm-concurrency.md](../docs/llm-concurrency.md)
- [docs/explorations/bench-ollama/bench-ollama.sbatch](../docs/explorations/bench-ollama/bench-ollama.sbatch)
