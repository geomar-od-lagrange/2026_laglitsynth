#!/usr/bin/env bash
#SBATCH --job-name=ollama-h100-tei-bench
#SBATCH --partition=gpu
#SBATCH --constraint=H100
#SBATCH --gpus-per-node=1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=bench-h100tei-%j.out
#SBATCH --error=bench-h100tei-%j.err
#
# Stage-8 realistic benchmark on one H100: llama3.1:8b against a real
# TEI rendered through the extraction-codebook prompt helpers
# (~15k-token input, structured JSON output). Flash attention + q8_0
# KV cache on, because the long-prompt regime is where they should
# earn their keep. Pair with scripts/bench-ollama-v100-tei.sbatch for
# the V100 baseline (V100 = Volta, no flash attention support).
#
# rsync a real TEI to $REPO_DIR/W2967479283.tei.xml before submitting.
# The TEI file is user-local (not in the repo — copyright concern).

set -euo pipefail

export http_proxy="${http_proxy:-http://10.0.7.235:3128}"
export https_proxy="${https_proxy:-http://10.0.7.235:3128}"
export no_proxy="${no_proxy:-localhost,127.0.0.1}"

REPO_DIR="${REPO_DIR:-$WORK/2026_laglitsynth}"
OLLAMA_DIR="${OLLAMA_DIR:-$HOME/.local/share/ollama-install}"
MODEL="${MODEL:-llama3.1:8b}"
TEI_PATH="${TEI_PATH:-W2967479283.tei.xml}"
N_CALLS="${N_CALLS:-8}"
PARALLELS=(${PARALLELS:-1 2 4})
THREADS=(${THREADS:-1 2 4})

export OLLAMA_FLASH_ATTENTION="${OLLAMA_FLASH_ATTENTION:-1}"
export OLLAMA_KV_CACHE_TYPE="${OLLAMA_KV_CACHE_TYPE:-q8_0}"
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-32768}"

cd "$REPO_DIR"
mkdir -p logs

[[ -x "$OLLAMA_DIR/bin/ollama" ]] || {
    echo "Ollama not installed at $OLLAMA_DIR — see docs/explorations/nesh-ollama.md" >&2
    exit 3
}
[[ -f "$TEI_PATH" ]] || {
    echo "TEI file not found at $TEI_PATH — rsync one in first." >&2
    exit 4
}

echo "==> Node $(hostname), CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-unset}"
echo "==> Model $MODEL, TEI $TEI_PATH, N_CALLS=$N_CALLS"
echo "==> OLLAMA_FLASH_ATTENTION=$OLLAMA_FLASH_ATTENTION OLLAMA_KV_CACHE_TYPE=$OLLAMA_KV_CACHE_TYPE OLLAMA_CONTEXT_LENGTH=$OLLAMA_CONTEXT_LENGTH"

OLLAMA_PID=""
cleanup() {
    [[ -n "${OLLAMA_PID:-}" ]] && kill "$OLLAMA_PID" 2>/dev/null || true
}
trap cleanup EXIT

start_ollama() {
    local parallel="$1"
    local log="logs/bench-h100tei-ollama-${SLURM_JOB_ID:-local}-p${parallel}.log"
    export OLLAMA_NUM_PARALLEL="$parallel"
    export OLLAMA_HOST="127.0.0.1:11434"
    "$OLLAMA_DIR/bin/ollama" serve >"$log" 2>&1 &
    OLLAMA_PID=$!
    for _ in $(seq 1 30); do
        curl -fsS http://127.0.0.1:11434/ >/dev/null 2>&1 && return 0
        sleep 1
    done
    echo "Ollama did not come up within 30s (parallel=$parallel); see $log" >&2
    return 5
}

stop_ollama() {
    [[ -n "${OLLAMA_PID:-}" ]] && kill "$OLLAMA_PID" 2>/dev/null || true
    wait "${OLLAMA_PID:-0}" 2>/dev/null || true
    OLLAMA_PID=""
    sleep 2
}

echo "==> Pre-pulling $MODEL"
start_ollama 1
"$OLLAMA_DIR/bin/ollama" pull "$MODEL"
stop_ollama

RESULTS="logs/bench-h100tei-${SLURM_JOB_ID:-local}.tsv"
echo -e "parallel\tthreads\tn_calls\twall_s\tthroughput_cps" | tee "$RESULTS"

for parallel in "${PARALLELS[@]}"; do
    start_ollama "$parallel"
    for threads in "${THREADS[@]}"; do
        line=$(pixi run python scripts/bench-ollama-concurrency.py \
            --base-url "http://127.0.0.1:11434" \
            --model "$MODEL" \
            --n-calls "$N_CALLS" \
            --concurrency "$threads" \
            --prompt-kind tei \
            --tei-path "$TEI_PATH")
        wall=$(echo "$line" | awk '{print $3}')
        tp=$(echo "$line" | awk '{print $4}')
        echo -e "${parallel}\t${threads}\t${N_CALLS}\t${wall}\t${tp}" | tee -a "$RESULTS"
    done
    stop_ollama
done

echo
echo "==> Results saved to $RESULTS"
