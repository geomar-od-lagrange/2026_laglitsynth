#!/usr/bin/env bash
#SBATCH --job-name=ollama-bench
#SBATCH --partition=gpu
#SBATCH --constraint=V100
#SBATCH --gpus-per-node=1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --output=bench-ollama-%j.out
#SBATCH --error=bench-ollama-%j.err
#
# Generic Ollama concurrency benchmark: sweep (N_GPUS, OLLAMA_NUM_PARALLEL,
# client threads) against a single node. One Ollama per GPU, pinned via
# per-subshell CUDA_VISIBLE_DEVICES. Client round-robins across URLs.
#
# Results: logs/bench-ollama-<JOBID>.tsv with columns
#   n_gpus  parallel  threads  n_calls  wall_s  throughput_cps
#
# Dimensions (all env-var overrides at sbatch --export time):
#   GPUS           GPU counts to sweep            (default "1")
#   PARALLELS      OLLAMA_NUM_PARALLEL per server (default "1 2 3 4")
#   THREADS        client thread counts           (default "1 2 3 4")
#   MODEL          ollama model tag               (default gemma3:4b)
#   N_CALLS        timed calls per cell           (default 30)
#   PROMPT_KIND    short|long|tei                 (default short)
#   TEI_PATH       required when PROMPT_KIND=tei  (default W2967479283.tei.xml)
#
# Optional Ollama server knobs (passed through to `ollama serve` start):
#   OLLAMA_FLASH_ATTENTION   0|1      (default 0; H100+ only)
#   OLLAMA_KV_CACHE_TYPE     q8_0|q4_0 (requires FlashAttention)
#   OLLAMA_CONTEXT_LENGTH    integer  (bump for long / TEI prompts)
#
# Slurm overrides at submit time:
#   sbatch --constraint=H100 --gpus-per-node=2 --time=...
#
# Prereqs:
#   - Repo at $REPO_DIR (default $WORK/2026_laglitsynth).
#   - Ollama install at $OLLAMA_DIR (default $HOME/.local/share/ollama-install).
#   - pixi on PATH.
#   - If PROMPT_KIND=tei: the TEI file exists at $TEI_PATH (rsync'd in,
#     not committed — copyright).
#
# Examples reproduced in docs/explorations/ollama-throughput.md:
#
#   # Single-GPU 4x4 sweep (V100 baseline, job 21963559)
#   sbatch scripts/bench-ollama.sbatch
#
#   # Single-GPU H100 baseline
#   sbatch --constraint=H100 scripts/bench-ollama.sbatch
#
#   # Single-GPU H100 + FlashAttention + q8_0 KV (job 21963713)
#   sbatch --constraint=H100 \
#       --export=ALL,OLLAMA_FLASH_ATTENTION=1,OLLAMA_KV_CACHE_TYPE=q8_0 \
#       scripts/bench-ollama.sbatch
#
#   # Multi-GPU V100 scaling (job 21963591)
#   sbatch --gpus-per-node=4 \
#       --export=ALL,GPUS="1 2 3 4",PARALLELS="1",THREADS="1 2 4 8",N_CALLS=60 \
#       scripts/bench-ollama.sbatch
#
#   # H100 real-TEI stage-8 bench (job 21963960)
#   sbatch --constraint=H100 --time=01:00:00 \
#       --export=ALL,MODEL=llama3.1:8b,PROMPT_KIND=tei,N_CALLS=8,PARALLELS="1 2 4",THREADS="1 2 4",OLLAMA_FLASH_ATTENTION=1,OLLAMA_KV_CACHE_TYPE=q8_0,OLLAMA_CONTEXT_LENGTH=32768 \
#       scripts/bench-ollama.sbatch
#
#   # V100 real-TEI stage-8 bench (job 21963961)
#   sbatch --time=02:00:00 \
#       --export=ALL,MODEL=llama3.1:8b,PROMPT_KIND=tei,N_CALLS=8,PARALLELS="1 2",THREADS="1 2 4",OLLAMA_CONTEXT_LENGTH=32768 \
#       scripts/bench-ollama.sbatch

set -euo pipefail

export http_proxy="${http_proxy:-http://10.0.7.235:3128}"
export https_proxy="${https_proxy:-http://10.0.7.235:3128}"
export no_proxy="${no_proxy:-localhost,127.0.0.1}"

REPO_DIR="${REPO_DIR:-$WORK/2026_laglitsynth}"
OLLAMA_DIR="${OLLAMA_DIR:-$HOME/.local/share/ollama-install}"
MODEL="${MODEL:-gemma3:4b}"
N_CALLS="${N_CALLS:-30}"
PROMPT_KIND="${PROMPT_KIND:-short}"
TEI_PATH="${TEI_PATH:-W2967479283.tei.xml}"
GPUS=(${GPUS:-1})
PARALLELS=(${PARALLELS:-1 2 3 4})
THREADS=(${THREADS:-1 2 3 4})

export OLLAMA_FLASH_ATTENTION="${OLLAMA_FLASH_ATTENTION:-0}"
export OLLAMA_KV_CACHE_TYPE="${OLLAMA_KV_CACHE_TYPE:-}"
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-0}"

cd "$REPO_DIR"
mkdir -p logs

[[ -x "$OLLAMA_DIR/bin/ollama" ]] || {
    echo "Ollama not installed at $OLLAMA_DIR — see docs/explorations/nesh-ollama.md" >&2
    exit 3
}
if [[ "$PROMPT_KIND" == "tei" && ! -f "$TEI_PATH" ]]; then
    echo "PROMPT_KIND=tei but TEI file not found at $TEI_PATH — rsync one in first." >&2
    exit 4
fi

echo "==> Node $(hostname), CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES:-unset}"
echo "==> Model=$MODEL  N_CALLS=$N_CALLS  PROMPT_KIND=$PROMPT_KIND"
echo "==> GPUS=(${GPUS[*]})  PARALLELS=(${PARALLELS[*]})  THREADS=(${THREADS[*]})"
echo "==> OLLAMA_FLASH_ATTENTION=$OLLAMA_FLASH_ATTENTION OLLAMA_KV_CACHE_TYPE=${OLLAMA_KV_CACHE_TYPE:-<default>} OLLAMA_CONTEXT_LENGTH=$OLLAMA_CONTEXT_LENGTH"

IFS=',' read -ra ALL_GPUS <<< "${CUDA_VISIBLE_DEVICES:-}"
TOTAL_GPUS="${#ALL_GPUS[@]}"
(( TOTAL_GPUS >= 1 )) || {
    echo "No GPUs visible — did sbatch allocate --gpus-per-node?" >&2
    exit 5
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
    local parallel="$2"
    OLLAMA_PIDS=()
    local urls=()
    for i in $(seq 0 $((n - 1))); do
        local port=$((11434 + i))
        local gpu="${ALL_GPUS[$i]}"
        local log="logs/bench-ollama-${SLURM_JOB_ID:-local}-g${n}-p${parallel}-i${i}.log"
        CUDA_VISIBLE_DEVICES="$gpu" \
            OLLAMA_NUM_PARALLEL="$parallel" \
            OLLAMA_HOST="127.0.0.1:${port}" \
            "$OLLAMA_DIR/bin/ollama" serve >"$log" 2>&1 &
        OLLAMA_PIDS+=("$!")
        urls+=("http://127.0.0.1:${port}")
    done
    for url in "${urls[@]}"; do
        wait_ready "$url" || {
            echo "Ollama did not come up at $url" >&2
            return 6
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
    sleep 2
}

echo "==> Pre-pulling $MODEL"
start_servers 1 1
OLLAMA_HOST="127.0.0.1:11434" "$OLLAMA_DIR/bin/ollama" pull "$MODEL"
stop_servers

RESULTS="logs/bench-ollama-${SLURM_JOB_ID:-local}.tsv"
echo -e "n_gpus\tparallel\tthreads\tn_calls\twall_s\tthroughput_cps" | tee "$RESULTS"

for n_gpus in "${GPUS[@]}"; do
    if (( n_gpus > TOTAL_GPUS )); then
        echo "(skipping n_gpus=$n_gpus: only $TOTAL_GPUS allocated)" >&2
        continue
    fi
    for parallel in "${PARALLELS[@]}"; do
        start_servers "$n_gpus" "$parallel"
        for threads in "${THREADS[@]}"; do
            client_args=(
                --base-url "$URLS_CSV"
                --model "$MODEL"
                --n-calls "$N_CALLS"
                --concurrency "$threads"
                --prompt-kind "$PROMPT_KIND"
            )
            if [[ "$PROMPT_KIND" == "tei" ]]; then
                client_args+=(--tei-path "$TEI_PATH")
            fi
            line=$(pixi run python scripts/bench-ollama-concurrency.py "${client_args[@]}")
            wall=$(echo "$line" | awk '{print $3}')
            tp=$(echo "$line" | awk '{print $4}')
            echo -e "${n_gpus}\t${parallel}\t${threads}\t${N_CALLS}\t${wall}\t${tp}" \
                | tee -a "$RESULTS"
        done
        stop_servers
    done
done

echo
echo "==> Results saved to $RESULTS"
