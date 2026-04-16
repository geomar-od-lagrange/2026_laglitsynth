# Running Ollama on NESH GPU nodes

Exploration of using NESH (Kiel University HPC cluster) GPU nodes to run
Ollama for the `filter-abstracts` pipeline stage.  Tested 2026-04-16.

## Motivation

The `filter-abstracts` stage sends every abstract to an LLM for relevance
classification.  A local Mac runs `gemma3:4b` at ~2 s per abstract.  NESH
has V100 (32 GB) and H100 (80 GB) GPUs that can run larger models faster.
The question: can we transparently point the existing CLI at a remote Ollama
on NESH without changing any application code?

## Answer

Yes.  The existing `--base-url` flag on `filter-abstracts` is sufficient.
The only infrastructure needed is an SSH tunnel.

## NESH quick reference

- Login: `nesh-login.rz.uni-kiel.de` (load-balances across login1-3)
- GPU partition: `gpu`
  - 4 H100 nodes (`nesh-gpu[01-04]`), 2 GPUs each, 80 GB VRAM
  - 2 V100 nodes (`nesh-gpu[05-06]`), 4 GPUs each, 32 GB VRAM
  - Use `--constraint=V100` or `--constraint=H100` to target a type
- Default walltime: 48 h
- Compute nodes need `https_proxy=http://10.0.7.235:3128` for internet
- Slurm sets `CUDA_VISIBLE_DEVICES` automatically; do not unset it
- [NESH user docs](https://www.hiperf.rz.uni-kiel.de/nesh)

## How it works

One SSH session chains three tunnels together:

```
local:11434 --(ssh -L)--> login-node:11434 <--(ssh -R)-- compute-node:11434
```

1. `ssh -A -L 11434:localhost:11434 user@nesh-login` forwards the local
   port to the login node, with agent forwarding enabled.
2. Inside that session, `srun` allocates a GPU node and runs a bash script.
3. On the GPU node, `ollama serve` binds `127.0.0.1:11434`, then a reverse
   SSH tunnel (`ssh -R`) carries that port back to the specific login node
   we landed on.

Ollama only listens on localhost, so access is restricted to the tunnel
holder.  Ctrl+C tears down the SSH, srun exits, and Slurm reclaims the job.

### Why the reverse tunnel?

The login node cannot directly reach `localhost:11434` on the compute node
(that would require `OLLAMA_HOST=0.0.0.0`, exposing Ollama on the cluster
network).  The reverse tunnel keeps Ollama on localhost and is the more
secure option.

### The login-node identity problem

`nesh-login.rz.uni-kiel.de` load-balances across three physical nodes.  The
reverse tunnel from the compute node must target the exact login node the
outer SSH landed on.  We capture `$(hostname)` on the login node *before*
`srun` and pass it into the compute-node script via `--export=ALL`.

## Installing Ollama on NESH

Ollama is not available as a module.  The static Linux binary works, but the
**full tarball** must be extracted (not just `bin/ollama`) because Ollama
needs its bundled CUDA libraries under `lib/ollama/cuda_v12/`.

```bash
ssh user@nesh-login.rz.uni-kiel.de bash -s <<'REMOTE'
    OLLAMA_DIR="$HOME/.local/share/ollama-install"
    mkdir -p "$OLLAMA_DIR"
    curl -fSL https://ollama.com/download/ollama-linux-amd64.tar.zst \
        | zstd -d | tar xf - -C "$OLLAMA_DIR"
REMOTE
```

### Lessons learned during install

- The download URL `ollama-linux-amd64` (without `.tar.zst`) returned 404 as
  of April 2026.  The tarball is the only option.
- NESH's `tar` (GNU tar 1.30) does not support `--zstd`, but standalone
  `zstd` is available.  Pipe through `zstd -d` instead.
- Extracting only `bin/ollama` and discarding `lib/` causes Ollama to fall
  back to CPU.  It sets `OLLAMA_LIBRARY_PATH` to the binary's parent
  directory and looks for CUDA runner libraries there.  With the full
  tarball, the layout is `bin/ollama` + `lib/ollama/cuda_v12/` and GPU
  discovery works.
- No `module load` is required.  Ollama bundles its own CUDA runtime and
  links against the system driver at `/usr/lib64/libcuda.so`.

## PoC script

The following script was used during testing.  It is not yet production
quality (no error handling for tunnel failures, no cleanup on partial
failure, hardcoded proxy address) but demonstrates the full flow.

```bash
#!/usr/bin/env bash
# Launch Ollama on a NESH GPU node and tunnel it to localhost.
#
# Usage:
#   NESH_USER=your-nesh-user ./nesh-ollama start [--model gemma3:4b] [--constraint V100]
#   NESH_USER=your-nesh-user ./nesh-ollama install
#
# Requires: ssh key + agent forwarding to nesh-login.rz.uni-kiel.de.
# Ctrl+C tears down the tunnel, srun, and the Slurm job in one go.

set -euo pipefail

NESH_LOGIN="nesh-login.rz.uni-kiel.de"
NESH_USER="${NESH_USER:?Set NESH_USER to your NESH username}"
LOCAL_PORT="${LOCAL_PORT:-11434}"
OLLAMA_PORT=11434
OLLAMA_DIR="\$HOME/.local/share/ollama-install"

_ssh() {
    ssh -A -o BatchMode=yes -o ConnectTimeout=10 "$NESH_USER@$NESH_LOGIN" "$@"
}

_install() {
    echo ">> Checking for Ollama binary on NESH..."
    _ssh bash -s <<'REMOTE'
        OLLAMA_DIR="$HOME/.local/share/ollama-install"
        if [[ -x "$OLLAMA_DIR/bin/ollama" ]]; then
            echo "   Ollama already installed at $OLLAMA_DIR"
            "$OLLAMA_DIR/bin/ollama" --version 2>&1 || true
            exit 0
        fi
        echo "   Downloading ollama-linux-amd64.tar.zst (~1.9 GB)..."
        mkdir -p "$OLLAMA_DIR"
        curl -fSL https://ollama.com/download/ollama-linux-amd64.tar.zst \
            | zstd -d | tar xf - -C "$OLLAMA_DIR"
        echo "   Done: $OLLAMA_DIR"
        "$OLLAMA_DIR/bin/ollama" --version 2>&1 || true
REMOTE
}

_start() {
    local model="gemma3:4b"
    local constraint="V100"
    local walltime="02:00:00"
    local mem="10000"
    local cpus="2"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --model)      model="$2"; shift 2 ;;
            --constraint) constraint="$2"; shift 2 ;;
            --time)       walltime="$2"; shift 2 ;;
            --mem)        mem="$2"; shift 2 ;;
            --cpus)       cpus="$2"; shift 2 ;;
            *) echo "Unknown flag: $1" >&2; exit 1 ;;
        esac
    done

    local constraint_flag=""
    if [[ -n "$constraint" ]]; then
        constraint_flag="--constraint=$constraint"
    fi

    _install

    echo ""
    echo ">> Requesting GPU node..."
    echo "   Once running, Ollama will be at http://localhost:$LOCAL_PORT"
    echo "   Press Ctrl+C to stop everything."
    echo ""

    ssh -A \
        -o ConnectTimeout=10 \
        -L "$LOCAL_PORT:localhost:$OLLAMA_PORT" \
        "$NESH_USER@$NESH_LOGIN" \
        "LOGIN_HOST=\$(hostname) && export LOGIN_HOST && srun --pty \
            --partition=gpu \
            ${constraint_flag} \
            --gpus-per-node=1 \
            --cpus-per-task=${cpus} \
            --mem=${mem} \
            --time=${walltime} \
            --export=ALL \
            bash -c '
export https_proxy=http://10.0.7.235:3128
export http_proxy=http://10.0.7.235:3128
echo \">> Node: \$(hostname) | Login: \$LOGIN_HOST\"
\$HOME/.local/share/ollama-install/bin/ollama serve &
OLLAMA_PID=\$!
sleep 5
echo \">> Pulling model ${model}...\"
\$HOME/.local/share/ollama-install/bin/ollama pull ${model}
echo \">> Model ready. Ollama serving on localhost:${OLLAMA_PORT}\"
echo \">> Reverse-tunnelling to \${LOGIN_HOST}:${OLLAMA_PORT}\"
ssh -o StrictHostKeyChecking=no \
    -R ${OLLAMA_PORT}:localhost:${OLLAMA_PORT} \
    -N \$LOGIN_HOST
'"
}

case "${1:-}" in
    start)   shift; _start "$@" ;;
    install) _install ;;
    *)
        echo "Usage: $0 {start|install}" >&2
        echo "  start [--model M] [--constraint V100|H100] [--time HH:MM:SS]" >&2
        echo "  install  Download ollama to NESH" >&2
        exit 1
        ;;
esac
```

## Timings

Tested with `filter-abstracts` on 20 records (14 with abstracts, 6 skipped).
Prompt: *"relevant to Lagrangian particle tracking in physical oceanography"*.

| Model | Where | Total | Per abstract | Notes |
|-------|-------|-------|-------------|-------|
| gemma3:4b (Q4_K_M) | Local Mac (Apple Silicon) | 27.7 s | ~2.0 s | CPU inference |
| gemma3:4b (Q4_K_M) | NESH V100 32 GB | 16.9 s | ~1.2 s | All 35 layers on GPU |
| gemma3:12b (Q4_K_M) | NESH V100 32 GB | 40.3 s | ~2.9 s | All layers on GPU |

The V100 is ~1.6x faster than Apple Silicon for `gemma3:4b`.  The 12b model
runs at roughly the same speed as 4b on the Mac, but with substantially
better reasoning capability.

## Models that fit on V100 32 GB (Q4_K_M)

| Model | VRAM estimate | Notes |
|-------|--------------|-------|
| gemma3:4b | ~3 GB | Current default, fast |
| gemma3:12b | ~8 GB | Good quality/speed trade-off |
| gemma3:27b | ~16 GB | Near-frontier quality |
| qwen3:32b | ~19 GB | Strong reasoning |
| mistral-small (24B) | ~14 GB | Solid all-rounder |

Models already cached on NESH (`~/.ollama/models/`): `gemma3:4b`, `gemma3:12b`.

## Issues encountered

### Ollama found no GPU (0/35 layers offloaded)

Extracting only the `ollama` binary from the tarball and placing it in
`~/bin/` caused GPU discovery to fail silently.  Ollama reported
`total_vram="0 B"` and fell back to CPU.  Root cause: Ollama sets
`OLLAMA_LIBRARY_PATH` to the binary's parent directory and expects
`lib/ollama/cuda_v12/` relative to that.  Fix: extract the full tarball to
a dedicated directory.

### CUDA_VISIBLE_DEVICES must not be unset

Slurm sets `CUDA_VISIBLE_DEVICES` to the assigned GPU index (e.g. `2`).
Ollama logs a warning suggesting to unset it, but doing so makes the
variable empty under `set -euo pipefail` and breaks the script.  More
importantly, Ollama needs the variable to find the correct GPU.  Leave it
alone.

### Model pull fails on compute nodes (network unreachable)

Compute nodes cannot reach the internet directly.  The proxy
`http://10.0.7.235:3128` must be set as `https_proxy` and `http_proxy`
before running `ollama pull`.  This is documented in the NESH software
docs (TensorFlow example).

### Reverse SSH hostname was empty

The initial attempt passed `$LOGIN_HOST` through nested single/double
quotes in the SSH + srun + bash chain, which resulted in an empty variable.
Fix: export `LOGIN_HOST` on the login node and use `--export=ALL` in srun
so Slurm propagates it to the compute node's environment.

### tar does not support --zstd

GNU tar 1.30 on NESH does not have built-in zstd support.  Standalone
`zstd` is available, so pipe: `curl ... | zstd -d | tar xf -`.

## Open questions

- Is the proxy address `10.0.7.235:3128` stable, or should it be
  discovered at runtime?
- Would `sbatch` (background job) + separate tunnel be more robust for
  long-running filtering sessions?
- For large-scale runs (thousands of abstracts), is it worth parallelising
  with `OLLAMA_NUM_PARALLEL` or running multiple Ollama instances?
- H100 nodes: how much faster are they?  Worth testing with `--constraint=H100`.
