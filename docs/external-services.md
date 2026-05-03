# External services setup

Runbook for the two external services the pipeline depends on: **Ollama**
(LLM inference for stages 3, 7, 8) and **GROBID** (PDF extraction for
stage 6). Read this before running any LLM or full-text stage. The
[NESH HPC cluster](#nesh-hpc-cluster) section at the bottom covers
batch operation on Kiel's GPU cluster.

## Ollama

### Install

**Linux** — extract the full tarball, not just the binary. The
`lib/ollama/` subtree carries bundled CUDA libraries that Ollama needs
for GPU discovery:

```bash
OLLAMA_DIR="$HOME/.local/share/ollama-install"
mkdir -p "$OLLAMA_DIR"
curl -fSL https://ollama.com/download/ollama-linux-amd64.tar.zst \
    | zstd -d | tar xf - -C "$OLLAMA_DIR"
export PATH="$OLLAMA_DIR/bin:$PATH"
```

**macOS** — the Homebrew cask is the simplest path:

```bash
brew install --cask ollama
```

Alternatively download from <https://ollama.com/download>.

### Start Ollama

`OLLAMA_NUM_PARALLEL` must be set **before** `ollama serve` is called.
Exporting it in a separate shell or after the process starts is a silent
no-op — Ollama reads it exactly once at startup. See
[llm-concurrency.md](llm-concurrency.md) for the relationship between
the server cap and the `--concurrency` flag on stages 3, 7, and 8.

```bash
OLLAMA_NUM_PARALLEL=2 ollama serve
```

For a single-user local run with no parallelism the default
(`OLLAMA_NUM_PARALLEL=1`) is fine; increase it when you also raise
`--concurrency` on a stage.

### Pull the models

Stage 3 (`screening-abstracts`) and stage 7 (`fulltext-eligibility`)
use `gemma3:4b` by default. Stage 8 (`extraction-codebook`) uses
`llama3.1:8b` by default. Pull both before running the pipeline:

```bash
ollama pull gemma3:4b
ollama pull llama3.1:8b
```

`gemma3:4b` is unsuitable for the full codebook payload in stage 8 —
it returns empty JSON. See [extraction-codebook.md](extraction-codebook.md)
for model-sizing guidance.

### Bake Modelfiles for `num_ctx`

Ollama's OpenAI-compatible endpoint accepts a `num_ctx` hint via
`extra_body`, but does not guarantee it is forwarded to the underlying
model runner. The reliable approach is to bake `num_ctx` into a named
Modelfile tag using `laglitsynth bake-model`:

```bash
laglitsynth bake-model --tag laglit-screen      --base gemma3:4b      --num-ctx 8192
laglitsynth bake-model --tag laglit-eligibility --base gemma3:4b      --num-ctx 32768
laglitsynth bake-model --tag laglit-extract     --base llama3.1:8b    --num-ctx 32768
```

Pass the baked tag as `--model` to the stage:

```bash
laglitsynth fulltext-eligibility --model laglit-eligibility ...
laglitsynth extraction-codebook  --model laglit-extract     ...
```

See [bake-model.md](bake-model.md) for the full subcommand reference.
The `--num-ctx N` flag on stages 7 and 8 is a lighter-weight alternative
for one-off runs, but the `extra_body` path is less reliable than a baked
tag. The NESH batch wrapper (`scripts/nesh-pipeline.sbatch`) bakes all
three tags automatically at job start; local users who call
`laglitsynth bake-model` themselves get the same guarantee.

### Remote Ollama via SSH tunnel

Point any stage at a remote Ollama with `--base-url http://localhost:11434`
(the default) after opening an SSH tunnel:

```bash
ssh -L 11434:localhost:11434 user@remote-host
```

For a two-hop setup where Ollama runs on an internal node not directly
reachable from your laptop, chain a forward tunnel from your machine to
the login node and a reverse tunnel from the compute node back:

```
local:11434 --(ssh -L)--> login-node:11434 <--(ssh -R)-- compute-node:11434
```

The NESH section below shows the concrete `srun` invocation. The full
investigation is in
[explorations/nesh-ollama.md](explorations/nesh-ollama.md).

## GROBID

### Install

GROBID runs as a Docker container. The image is amd64-only; on Apple
Silicon it runs under Rosetta emulation (functional but slower):

```bash
# Apple Silicon: install Rosetta first if prompted
softwareupdate --install-rosetta

docker run --rm -p 8070:8070 lfoppiano/grobid:0.8.0
```

Startup takes 30–60 seconds while the JVM and models load. Poll the
health endpoint before running stage 6:

```bash
curl http://localhost:8070/api/isalive
```

### Memory tuning

Docker Desktop's default VM allocation is 2 GB. GROBID's JVM heap alone
needs roughly 2 GB under normal load; spikes during batch processing can
exceed that. Set Docker Desktop's memory limit to at least **6–8 GB**
(Preferences → Resources → Memory) before running stage 6 on a real
corpus. Under-allocation causes silent JVM crashes mid-corpus without
a helpful error from the container.

See [explorations/nesh-grobid.md](explorations/nesh-grobid.md) for
memory observations from the NESH setup (32 GB Slurm allocation; GROBID
ran without issue).

### macOS `localhost:8070` workaround

On some macOS Docker Desktop versions, host calls to `localhost:8070`
are silently blocked by network sandboxing even when the container is
running and healthy. The workaround is to issue the GROBID request from
a helper container joined to GROBID's network namespace:

```bash
docker run --rm --network container:<grobid-container-name> \
  -v "$PWD/pdfs":/pdfs curlimages/curl \
  -s -S -f -o - \
  -F input=@"/pdfs/sample.pdf" \
  http://localhost:8070/api/processFulltextDocument
```

The `fulltext-extraction` stage uses the Python GROBID client, which
makes the same HTTP call. If stage 6 fails with connection errors on
macOS despite the container being alive, try this network-namespace
approach. Full context in [grobid-lessons.md](grobid-lessons.md).

### `consolidateCitations=0`

Stage 6 sends `consolidateCitations=0` by default. Setting it to 1
instructs GROBID to call Crossref and Semantic Scholar to enrich
extracted references with DOIs. That lookup requires internet access
during processing and noticeably slows extraction. The pipeline keeps
OpenAlex as the authoritative metadata source, so citation enrichment
adds no value. See [grobid-lessons.md](grobid-lessons.md) for the
original rationale.

### Mid-corpus crash recovery

If GROBID or the Docker container crashes during a long stage-6 run,
restart the container and re-run with `--skip-existing`:

```bash
docker run --rm -p 8070:8070 lfoppiano/grobid:0.8.0 &
# wait for /api/isalive ...
laglitsynth fulltext-extraction --skip-existing --run-id <existing-run-id> ...
```

`--skip-existing` loads any prior `extraction-records.jsonl` from the
run directory and skips `work_id`s that already have a completed record.
Only new or previously failed PDFs are submitted to GROBID.

## NESH HPC cluster

NESH (Kiel University HPC) uses Singularity rather than Docker and
requires a proxy for internet access from compute nodes. The production
batch wrapper is `scripts/nesh-pipeline.sbatch`; this section documents
the key invocations for users writing their own sbatch scripts.

### Ollama on NESH

The full tarball install is mandatory on NESH. The plain binary (without
`lib/ollama/`) causes Ollama to fall back to CPU silently:

```bash
OLLAMA_DIR="$HOME/.local/share/ollama-install"
mkdir -p "$OLLAMA_DIR"
curl -fSL https://ollama.com/download/ollama-linux-amd64.tar.zst \
    | zstd -d | tar xf - -C "$OLLAMA_DIR"
```

NESH's `tar` (GNU tar 1.30) does not support `--zstd`; pipe through the
standalone `zstd -d` instead.

On compute nodes, set the proxy before `ollama pull`:

```bash
export https_proxy=http://10.0.7.235:3128
export http_proxy=http://10.0.7.235:3128
```

Start Ollama and open the reverse tunnel back to the login node:

```bash
"$OLLAMA_DIR/bin/ollama" serve &
sleep 5
"$OLLAMA_DIR/bin/ollama" pull gemma3:4b
ssh -o StrictHostKeyChecking=no \
    -R 11434:localhost:11434 \
    -N "$LOGIN_HOST"
```

`LOGIN_HOST` must be the specific login node your outer SSH session
landed on (not the load-balancer alias). The outer session is:

```bash
ssh -A -L 11434:localhost:11434 user@nesh-login.rz.uni-kiel.de \
    "LOGIN_HOST=\$(hostname) && export LOGIN_HOST && srun --pty \
     --partition=gpu --constraint=V100 --gpus-per-node=1 \
     --export=ALL bash -c '<compute-node script>'"
```

See [explorations/nesh-ollama.md](explorations/nesh-ollama.md) for the
full PoC script, timing benchmarks, and the explanation of why the
reverse tunnel is needed.

#### Baking tags on NESH

`scripts/nesh-pipeline.sbatch` bakes all three model tags automatically.
Users running their own sbatch scripts can replicate this with
`laglitsynth bake-model` (same as local setup above) or with an explicit
`ollama create` call:

```bash
MODELFILES_DIR="data/nesh/modelfiles"
mkdir -p "$MODELFILES_DIR"
cat >"$MODELFILES_DIR/laglit-extract.Modelfile" <<EOF
FROM llama3.1:8b
PARAMETER num_ctx 32768
EOF
"$OLLAMA_DIR/bin/ollama" create laglit-extract \
    -f "$MODELFILES_DIR/laglit-extract.Modelfile"
```

Files are written to a path on `$WORK` rather than `/tmp` because
`/tmp` on NESH compute nodes is not always usable. See the sbatch
wrapper (lines 175–192) for the full three-tag sequence.

### GROBID on NESH (Singularity)

Pull the SIF once and cache it on `$WORK`:

```bash
module load gcc12-env/12.3.0
module load singularity

GROBID_SIF="data/nesh/grobid-0.8.0.sif"
[[ -f "$GROBID_SIF" ]] || \
    singularity pull "$GROBID_SIF" docker://lfoppiano/grobid:0.8.0
```

Singularity ignores the Dockerfile `WORKDIR`, so `--pwd /opt/grobid` is
mandatory. GROBID writes to two paths inside the read-only image layer
(`grobid-home/tmp` and `logs`); replace `--writable-tmpfs` with explicit
bind mounts:

```bash
GROBID_WORK="data/nesh/grobid-work"
mkdir -p "$GROBID_WORK/tmp" "$GROBID_WORK/logs"

singularity run --pwd /opt/grobid \
    --bind "$PWD/$GROBID_WORK/tmp:/opt/grobid/grobid-home/tmp" \
    --bind "$PWD/$GROBID_WORK/logs:/opt/grobid/logs" \
    "$GROBID_SIF" >"logs/grobid-${SLURM_JOB_ID}.log" 2>&1 &
```

The bind mounts survive the job; `grobid-work/logs/grobid-service.log`
is available for post-mortem inspection.

GROBID needs 30–90 seconds to start on a cold node. Poll
`http://localhost:8070/api/isalive` before submitting PDFs.

See [explorations/nesh-grobid.md](explorations/nesh-grobid.md) for the
full debugging log and the explanation of each flag.
