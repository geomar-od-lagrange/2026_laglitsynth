#!/usr/bin/env bash
#SBATCH --job-name=ollama-multigpu-bench
#SBATCH --partition=gpu
#SBATCH --constraint=V100
#SBATCH --gpus-per-node=4
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=00:45:00
#SBATCH --output=bench-multi-%j.out
#SBATCH --error=bench-multi-%j.err
#
# Sweep GPU count x client threads on a multi-GPU node. One Ollama per
# allocated GPU, on ports 11434, 11435, ... — client round-robins across
# them (scripts/bench-ollama-concurrency.py takes a comma-separated
# --base-url). Per-server OLLAMA_NUM_PARALLEL is fixed for a given sweep
# so the "scale-out across GPUs" signal isn't tangled with the
# "scale-up within one GPU" signal — tune per hardware using
# scripts/bench-ollama-concurrency.sbatch first.
#
# Defaults (override via sbatch --export=ALL,...=...):
#   GPUS="1 2 3 4"          GPU counts to sweep.
#   THREADS="1 2 4 8"       Client thread counts to sweep.
#   OLLAMA_NUM_PARALLEL=1   Per-server parallelism. V100: keep 1.
#                           H100: try 2 or 3 (see 21963560.tsv).
#   N_CALLS=60              Calls per cell; higher than single-GPU bench
#                           so 4-GPU cells aren't sub-second.
#   MODEL=gemma3:4b
#
# Prereqs (same as scripts/bench-ollama-concurrency.sbatch):
#   - Repo at $REPO_DIR (default $WORK/2026_laglitsynth).
#   - Ollama install at $OLLAMA_DIR (default $HOME/.local/share/ollama-install).
#   - pixi on PATH.

set -euo pipefail

export http_proxy="${http_proxy:-http://10.0.7.235:3128}"
export https_proxy="${https_proxy:-http://10.0.7.235:3128}"
export no_proxy="${no_proxy:-localhost,127.0.0.1}"

REPO_DIR="${REPO_DIR:-$WORK/2026_laglitsynth}"
OLLAMA_DIR="${OLLAMA_DIR:-$HOME/.local/share/ollama-install}"
MODEL="${MODEL:-gemma3:4b}"
N_CALLS="${N_CALLS:-60}"
GPUS=(${GPUS:-1 2 3 4})
THREADS=(${THREADS:-1 2 4 8})
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-1}"

cd "$REPO_DIR"
mkdir -p logs

[[ -x "$OLLAMA_DIR/bin/ollama" ]] || {
    echo "Ollama not installed at $OLLAMA_DIR — see docs/explorations/nesh-ollama.md" >&2
    exit 3
}

echo "==> Node $(hostname), CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-unset}"
echo "==> Model $MODEL, N_CALLS=$N_CALLS, OLLAMA_NUM_PARALLEL=$OLLAMA_NUM_PARALLEL"

# Slurm hands us the allocated physical GPU indices in
# CUDA_VISIBLE_DEVICES (e.g. "0,1,2,3"). We pin each Ollama to one of
# them via a per-subshell CUDA_VISIBLE_DEVICES override.
IFS=',' read -ra ALL_GPUS <<< "${CUDA_VISIBLE_DEVICES:-}"
TOTAL_GPUS="${#ALL_GPUS[@]}"
echo "==> Allocated ${TOTAL_GPUS} GPU(s): ${ALL_GPUS[*]:-none}"
(( TOTAL_GPUS >= 1 )) || {
    echo "No GPUs visible — did sbatch allocate --gpus-per-node?" >&2
    exit 4
}

OLLAMA_PIDS=()
cleanup() {
    for pid in "${OLLAMA_PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT

wait_ready() {
    local url="$1"
    for _ in $(seq 1 30); do
        curl -fsS "$url/" >/dev/null 2>&1 && return 0
        sleep 1
    done
    return 1
}

start_servers() {
    local n="$1"
    OLLAMA_PIDS=()
    local urls=()
    for i in $(seq 0 $((n - 1))); do
        local port=$((11434 + i))
        local gpu="${ALL_GPUS[$i]}"
        local log="logs/bench-multi-ollama-${SLURM_JOB_ID:-local}-g${n}-i${i}.log"
        CUDA_VISIBLE_DEVICES="$gpu" \
            OLLAMA_HOST="127.0.0.1:${port}" \
            "$OLLAMA_DIR/bin/ollama" serve >"$log" 2>&1 &
        OLLAMA_PIDS+=("$!")
        urls+=("http://127.0.0.1:${port}")
    done
    for url in "${urls[@]}"; do
        wait_ready "$url" || {
            echo "Ollama did not come up at $url; see logs/bench-multi-ollama-${SLURM_JOB_ID:-local}-*.log" >&2
            return 5
        }
    done
    URLS_CSV=$(IFS=','; echo "${urls[*]}")
}

stop_servers() {
    for pid in "${OLLAMA_PIDS[@]:-}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
    OLLAMA_PIDS=()
    sleep 2  # let the ports free before the next batch
}

# Pre-pull the model once with a throwaway single-GPU instance so
# download cost doesn't land in the sweep.
echo "==> Pre-pulling $MODEL"
start_servers 1
OLLAMA_HOST="127.0.0.1:11434" "$OLLAMA_DIR/bin/ollama" pull "$MODEL"
stop_servers

RESULTS="logs/bench-multi-${SLURM_JOB_ID:-local}.tsv"
echo -e "n_gpus\tparallel_per_gpu\tthreads\tn_calls\twall_s\tthroughput_cps" | tee "$RESULTS"

for n_gpus in "${GPUS[@]}"; do
    if (( n_gpus > TOTAL_GPUS )); then
        echo "(skipping n_gpus=$n_gpus: only $TOTAL_GPUS allocated)" >&2
        continue
    fi
    start_servers "$n_gpus"
    for threads in "${THREADS[@]}"; do
        line=$(pixi run python scripts/bench-ollama-concurrency.py \
            --base-url "$URLS_CSV" \
            --model "$MODEL" \
            --n-calls "$N_CALLS" \
            --concurrency "$threads")
        # "<threads> <n_calls> <wall> <throughput>"
        wall=$(echo "$line" | awk '{print $3}')
        tp=$(echo "$line" | awk '{print $4}')
        echo -e "${n_gpus}\t${OLLAMA_NUM_PARALLEL}\t${threads}\t${N_CALLS}\t${wall}\t${tp}" \
            | tee -a "$RESULTS"
    done
    stop_servers
done

echo
echo "==> Results saved to $RESULTS"
