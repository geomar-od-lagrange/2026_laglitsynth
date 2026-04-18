#!/usr/bin/env bash
#SBATCH --job-name=ollama-concurrency-bench
#SBATCH --partition=gpu
#SBATCH --constraint=V100
#SBATCH --gpus-per-node=1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=16G
#SBATCH --time=00:30:00
#SBATCH --output=bench-ollama-%j.out
#SBATCH --error=bench-ollama-%j.err
#
# Sweep (OLLAMA_NUM_PARALLEL, client threads) over {1,2,3,4} x {1,2,3,4}
# using scripts/bench-ollama-concurrency.py against a local Ollama on a
# single GPU. Each server setting requires restarting Ollama (the env
# var is read at `ollama serve` start).
#
# Prereqs (same as nesh-test-run.sbatch):
#   - Repo at $REPO_DIR (default $WORK/2026_laglitsynth).
#   - Ollama install at $OLLAMA_DIR (default $HOME/.local/share/ollama-install).
#   - pixi on PATH.
#
# Override at submit time: sbatch --export=ALL,MODEL=gemma3:12b,N_CALLS=50 \
#     scripts/bench-ollama-concurrency.sbatch

set -euo pipefail

export http_proxy="${http_proxy:-http://10.0.7.235:3128}"
export https_proxy="${https_proxy:-http://10.0.7.235:3128}"
export no_proxy="${no_proxy:-localhost,127.0.0.1}"

REPO_DIR="${REPO_DIR:-$WORK/2026_laglitsynth}"
OLLAMA_DIR="${OLLAMA_DIR:-$HOME/.local/share/ollama-install}"
MODEL="${MODEL:-gemma3:4b}"
N_CALLS="${N_CALLS:-30}"
PARALLELS=(${PARALLELS:-1 2 3 4})
THREADS=(${THREADS:-1 2 3 4})

cd "$REPO_DIR"
mkdir -p logs

[[ -x "$OLLAMA_DIR/bin/ollama" ]] || {
    echo "Ollama not installed at $OLLAMA_DIR — see docs/explorations/nesh-ollama.md" >&2
    exit 3
}

echo "==> Node $(hostname), CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-unset}"
echo "==> Model $MODEL, N_CALLS=$N_CALLS"

OLLAMA_PID=""
cleanup() {
    [[ -n "${OLLAMA_PID:-}" ]] && kill "$OLLAMA_PID" 2>/dev/null || true
}
trap cleanup EXIT

start_ollama() {
    local parallel="$1"
    local log="logs/bench-ollama-${SLURM_JOB_ID:-local}-p${parallel}.log"
    export OLLAMA_NUM_PARALLEL="$parallel"
    export OLLAMA_HOST="127.0.0.1:11434"
    "$OLLAMA_DIR/bin/ollama" serve >"$log" 2>&1 &
    OLLAMA_PID=$!
    for _ in $(seq 1 30); do
        curl -fsS http://127.0.0.1:11434/ >/dev/null 2>&1 && return 0
        sleep 1
    done
    echo "Ollama did not come up within 30s (parallel=$parallel); see $log" >&2
    return 4
}

stop_ollama() {
    [[ -n "${OLLAMA_PID:-}" ]] && kill "$OLLAMA_PID" 2>/dev/null || true
    wait "${OLLAMA_PID:-0}" 2>/dev/null || true
    OLLAMA_PID=""
    # Give the port a moment to free up before the next `ollama serve`.
    sleep 2
}

# One-shot model pull (through the proxy). Uses a throwaway Ollama
# instance with parallel=1 so we don't measure download time in the sweep.
echo "==> Pre-pulling $MODEL"
start_ollama 1
"$OLLAMA_DIR/bin/ollama" pull "$MODEL"
stop_ollama

RESULTS="logs/bench-ollama-${SLURM_JOB_ID:-local}.tsv"
echo -e "parallel\tthreads\tn_calls\twall_s\tthroughput_cps" | tee "$RESULTS"

for parallel in "${PARALLELS[@]}"; do
    start_ollama "$parallel"
    for threads in "${THREADS[@]}"; do
        line=$(pixi run python scripts/bench-ollama-concurrency.py \
            --base-url "http://127.0.0.1:11434" \
            --model "$MODEL" \
            --n-calls "$N_CALLS" \
            --concurrency "$threads")
        # line: "<threads> <n_calls> <wall> <throughput>"
        wall=$(echo "$line" | awk '{print $3}')
        tp=$(echo "$line" | awk '{print $4}')
        echo -e "${parallel}\t${threads}\t${N_CALLS}\t${wall}\t${tp}" | tee -a "$RESULTS"
    done
    stop_ollama
done

echo
echo "==> Results saved to $RESULTS"
