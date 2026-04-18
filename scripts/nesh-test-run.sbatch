#!/usr/bin/env bash
#SBATCH --job-name=laglitsynth-test-run
#SBATCH --partition=gpu
#SBATCH --constraint=V100
#SBATCH --gpus-per-node=1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --output=nesh-test-run-%j.out
#SBATCH --error=nesh-test-run-%j.err
#
# Run scripts/test-run-all-stages.sh on one NESH V100 node. Ollama and
# GROBID both run as background processes on the same compute node:
# Ollama from a bundled install under $OLLAMA_DIR; GROBID from a
# repo-local Singularity image pulled from Docker Hub on first use.
# See docs/explorations/nesh-ollama.md for the Ollama install story
# and the proxy rationale.
#
# Prerequisites:
#   - Repo cloned under $WORK (the fast per-user scratch; $HOME is small
#     and backed up). Default REPO_DIR is $WORK/2026_laglitsynth;
#     override via the env var if you clone elsewhere.
#   - $REPO_DIR/.env with OPENALEX_API_KEY and UNPAYWALL_EMAIL.
#   - Ollama binary + bundled CUDA libs at $OLLAMA_DIR (default
#     $HOME/.local/share/ollama-install). See
#     docs/explorations/nesh-ollama.md — the full tarball is needed,
#     not just bin/ollama.
#   - `singularity` module available; `pixi` on PATH.
#
# Usage:
#   mkdir -p logs
#   sbatch scripts/nesh-test-run.sbatch
#   sbatch --export=ALL,QUERY="particle dispersion",N=20 scripts/nesh-test-run.sbatch

set -euo pipefail

# NESH compute-node proxy (see docs/explorations/nesh-ollama.md).
# Compute nodes cannot reach the public internet without this.
export http_proxy="${http_proxy:-http://10.0.7.235:3128}"
export https_proxy="${https_proxy:-http://10.0.7.235:3128}"
export no_proxy="${no_proxy:-localhost,127.0.0.1}"

REPO_DIR="${REPO_DIR:-$WORK/2026_laglitsynth}"
OLLAMA_DIR="${OLLAMA_DIR:-$HOME/.local/share/ollama-install}"
GROBID_SIF="${GROBID_SIF:-data/nesh/grobid-0.8.0.sif}"
GROBID_IMAGE="${GROBID_IMAGE:-docker://lfoppiano/grobid:0.8.0}"
QUERY="${QUERY:-Lagrangian particle tracking}"
N="${N:-10}"
SCREENING_MODEL="${SCREENING_MODEL:-gemma3:4b}"
ELIGIBILITY_MODEL="${ELIGIBILITY_MODEL:-gemma3:4b}"
EXTRACTION_MODEL="${EXTRACTION_MODEL:-llama3.1:8b}"

cd "$REPO_DIR"
mkdir -p logs

[[ -f .env ]] || {
    echo "Missing $REPO_DIR/.env (OPENALEX_API_KEY, UNPAYWALL_EMAIL)" >&2
    exit 2
}
[[ -x "$OLLAMA_DIR/bin/ollama" ]] || {
    echo "Ollama not installed at $OLLAMA_DIR — see docs/explorations/nesh-ollama.md" >&2
    exit 3
}

echo "==> Node $(hostname), CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-unset}"

module load gcc12-env/12.3.0
module load singularity

# --- GROBID: repo-local SIF, pulled on first use ---
mkdir -p "$(dirname "$GROBID_SIF")"
if [[ ! -f "$GROBID_SIF" ]]; then
    echo "==> singularity pull $GROBID_SIF $GROBID_IMAGE"
    singularity pull "$GROBID_SIF" "$GROBID_IMAGE"
fi

GROBID_LOG="logs/grobid-${SLURM_JOB_ID:-local}.log"
echo "==> singularity run $GROBID_SIF (log: $GROBID_LOG)"
singularity run "$GROBID_SIF" >"$GROBID_LOG" 2>&1 &
GROBID_PID=$!

# --- Ollama: Slurm has already set CUDA_VISIBLE_DEVICES; do not unset ---
export OLLAMA_HOST="127.0.0.1:11434"
OLLAMA_LOG="logs/ollama-${SLURM_JOB_ID:-local}.log"
"$OLLAMA_DIR/bin/ollama" serve >"$OLLAMA_LOG" 2>&1 &
OLLAMA_PID=$!

cleanup() {
    [[ -n "${OLLAMA_PID:-}" ]] && kill "$OLLAMA_PID" 2>/dev/null || true
    [[ -n "${GROBID_PID:-}" ]] && kill "$GROBID_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Wait for Ollama.
for _ in $(seq 1 30); do
    curl -fsS http://127.0.0.1:11434/ >/dev/null 2>&1 && break
    sleep 1
done
curl -fsS http://127.0.0.1:11434/ >/dev/null 2>&1 || {
    echo "Ollama did not come up within 30s; see $OLLAMA_LOG" >&2
    exit 4
}

# Wait for GROBID (slower cold start — JVM + model loading).
for _ in $(seq 1 90); do
    curl -fsS http://localhost:8070/api/isalive >/dev/null 2>&1 && break
    sleep 2
done
curl -fsS http://localhost:8070/api/isalive >/dev/null 2>&1 || {
    echo "GROBID did not come up within 180s; see $GROBID_LOG" >&2
    exit 5
}

# Pull LLM models via proxy.
for m in "$SCREENING_MODEL" "$ELIGIBILITY_MODEL" "$EXTRACTION_MODEL"; do
    echo "==> ollama pull $m"
    "$OLLAMA_DIR/bin/ollama" pull "$m"
done

# Run the smoke-test end-to-end.
export OLLAMA_BASE="http://127.0.0.1:11434"
export GROBID_URL="http://localhost:8070"
export SCREENING_MODEL ELIGIBILITY_MODEL EXTRACTION_MODEL

pixi run scripts/test-run-all-stages.sh "$QUERY" "$N"
