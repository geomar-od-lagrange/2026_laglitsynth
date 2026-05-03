#!/usr/bin/env bash
#SBATCH --job-name=laglitsynth-pipeline
#SBATCH --partition=gpu
#SBATCH --constraint=V100
#SBATCH --gpus-per-node=1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --output=nesh-pipeline-%j.out
#SBATCH --error=nesh-pipeline-%j.err
#
# Run scripts/run-pipeline.sh on one NESH GPU node. Ollama and GROBID
# both run as background processes on the same compute node: Ollama
# from a bundled install under $OLLAMA_DIR; GROBID from a repo-local
# Singularity image pulled from Docker Hub on first use. See
# docs/explorations/nesh-ollama.md for the Ollama install story and
# the proxy rationale.
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
#   sbatch scripts/nesh-pipeline.sbatch
#   sbatch --export=ALL,QUERY="particle dispersion",N=20 scripts/nesh-pipeline.sbatch
#   sbatch --export=ALL,STOP_AFTER_STAGE=3,N=200 scripts/nesh-pipeline.sbatch
#
# Per-stage Ollama runner sizing — see plans/done/ollama-num-ctx-per-model.md.
# Each LLM stage gets its own synthesised Ollama tag (laglit-screen,
# laglit-eligibility, laglit-extract) created from the upstream model
# with PARAMETER num_ctx baked in via Modelfile heredoc. Override the
# model and the context window per stage:
#   sbatch --export=ALL,EXTRACTION_NUM_CTX=16384 scripts/nesh-pipeline.sbatch
#   sbatch --export=ALL,EXTRACTION_MODEL=qwen2.5:14b,EXTRACTION_NUM_CTX=24576 \
#       scripts/nesh-pipeline.sbatch

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
SCREENING_NUM_CTX="${SCREENING_NUM_CTX:-8192}"
ELIGIBILITY_MODEL="${ELIGIBILITY_MODEL:-gemma3:4b}"
ELIGIBILITY_NUM_CTX="${ELIGIBILITY_NUM_CTX:-32768}"
EXTRACTION_MODEL="${EXTRACTION_MODEL:-llama3.1:8b}"
EXTRACTION_NUM_CTX="${EXTRACTION_NUM_CTX:-32768}"

cd "$REPO_DIR"
mkdir -p logs

[[ -f .env ]] || {
    echo "Missing $REPO_DIR/.env (OPENALEX_API_KEY, UNPAYWALL_EMAIL)" >&2
    exit 2
}
set -a
source .env
set +a
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
GROBID_WORK="data/nesh/grobid-work"
mkdir -p "$GROBID_WORK/tmp" "$GROBID_WORK/logs"
echo "==> singularity run $GROBID_SIF (log: $GROBID_LOG)"
# --pwd /opt/grobid: the image sets WORKDIR there; its entrypoint uses
#   a relative path and the logback appender writes to logs/grobid-service.log.
# --bind ...: GROBID needs to write to /opt/grobid/grobid-home/tmp
#   (intermediate PDF extract) and /opt/grobid/logs (service log).
#   Those paths are read-only in the Singularity image, so the service
#   comes up (isalive ok) but every real request 500s with
#   "Could not create temporary file". Bind host dirs on $WORK so the
#   writes land on fast scratch and survive the job for inspection.
singularity run --pwd /opt/grobid \
    --bind "$PWD/$GROBID_WORK/tmp:/opt/grobid/grobid-home/tmp" \
    --bind "$PWD/$GROBID_WORK/logs:/opt/grobid/logs" \
    "$GROBID_SIF" >"$GROBID_LOG" 2>&1 &
GROBID_PID=$!

# --- Ollama: Slurm has already set CUDA_VISIBLE_DEVICES; do not unset ---
# OLLAMA_NUM_PARALLEL caps in-flight GPU work. Client overbooks by +2 to
# mask per-request HTTP / JSON / pydantic overhead so the GPU never waits
# on the next prompt. See docs/llm-concurrency.md.
export OLLAMA_NUM_PARALLEL="${OLLAMA_NUM_PARALLEL:-2}"
export LLM_CONCURRENCY="${LLM_CONCURRENCY:-$((OLLAMA_NUM_PARALLEL + 2))}"
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
t0=$SECONDS
for _ in $(seq 1 30); do
    curl -fsS http://127.0.0.1:11434/ >/dev/null 2>&1 && break
    sleep 1
done
elapsed=$((SECONDS - t0))
if curl -fsS http://127.0.0.1:11434/ >/dev/null 2>&1; then
    echo "==> Ollama up in ${elapsed}s"
else
    echo "Ollama did not come up within ${elapsed}s; see $OLLAMA_LOG" >&2
    exit 4
fi

# Wait for GROBID (slower cold start — JVM + model loading).
t0=$SECONDS
for _ in $(seq 1 90); do
    curl -fsS http://localhost:8070/api/isalive >/dev/null 2>&1 && break
    sleep 2
done
elapsed=$((SECONDS - t0))
if curl -fsS http://localhost:8070/api/isalive >/dev/null 2>&1; then
    echo "==> GROBID up in ${elapsed}s"
else
    echo "GROBID did not come up within ${elapsed}s; see $GROBID_LOG" >&2
    exit 5
fi

# Pull upstream LLM models via proxy. Each is the FROM base for the
# per-stage tag synthesised next. sort -u so a shared base (e.g.
# screening+eligibility on gemma3:4b) is only pulled once.
for m in $(printf '%s\n' "$SCREENING_MODEL" "$ELIGIBILITY_MODEL" "$EXTRACTION_MODEL" | sort -u); do
    echo "==> ollama pull $m"
    "$OLLAMA_DIR/bin/ollama" pull "$m"
done

# Synthesise per-stage tags with PARAMETER num_ctx baked in. The
# per-request num_ctx hint via OpenAI extra_body does not reliably
# size the runner — only Modelfile-baked num_ctx does. Each Modelfile
# is two lines, written to a tempfile because `ollama create -f -`
# (stdin) is not supported by the Ollama version on NESH ("no
# Modelfile or safetensors files found"). See
# plans/done/ollama-num-ctx-per-model.md.
create_tag() {
    local tag="$1" base="$2" num_ctx="$3"
    local mf
    mf=$(mktemp)
    cat >"$mf" <<EOF
FROM $base
PARAMETER num_ctx $num_ctx
EOF
    echo "==> ollama create $tag (num_ctx=$num_ctx, FROM $base)"
    "$OLLAMA_DIR/bin/ollama" create "$tag" -f "$mf"
    rm -f "$mf"
}

create_tag laglit-screen       "$SCREENING_MODEL"   "$SCREENING_NUM_CTX"
create_tag laglit-eligibility  "$ELIGIBILITY_MODEL" "$ELIGIBILITY_NUM_CTX"
create_tag laglit-extract      "$EXTRACTION_MODEL"  "$EXTRACTION_NUM_CTX"

# Flip the *_MODEL exports from upstream tags (used by pull + create
# above) to the local synthesised tags consumed by run-pipeline.sh.
SCREENING_MODEL=laglit-screen
ELIGIBILITY_MODEL=laglit-eligibility
EXTRACTION_MODEL=laglit-extract

# Run the smoke-test end-to-end.
export OLLAMA_BASE="http://127.0.0.1:11434"
export GROBID_URL="http://localhost:8070"
export SCREENING_MODEL ELIGIBILITY_MODEL EXTRACTION_MODEL LLM_CONCURRENCY
export STOP_AFTER_STAGE="${STOP_AFTER_STAGE:-8}"

pixi run scripts/run-pipeline.sh "$QUERY" "$N"
