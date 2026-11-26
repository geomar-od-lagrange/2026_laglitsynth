#!/usr/bin/env bash
#SBATCH --job-name=laglitsynth-pipeline
#SBATCH --partition=base
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=4
#SBATCH --mem=32G
#SBATCH --time=02:00:00
#SBATCH --output=nesh-pipeline-%j.out
#SBATCH --error=nesh-pipeline-%j.err
#
# Run scripts/run-pipeline.sh on one NESH compute node. GROBID runs as a
# background process on the same node from a repo-local Singularity
# image pulled from Docker Hub on first use. The LLM stages call a hosted
# endpoint, reached through the compute-node proxy (see
# docs/explorations/nesh-ollama.md for the proxy rationale).
#
# Prerequisites:
#   - Repo cloned under $WORK (the fast per-user scratch; $HOME is small
#     and backed up). Default REPO_DIR is $WORK/2026_laglitsynth;
#     override via the env var if you clone elsewhere.
#   - $REPO_DIR/.env with OPENALEX_API_KEY and UNPAYWALL_EMAIL.
#   - `singularity` module available; `pixi` on PATH.
#
# Usage:
#   mkdir -p logs
#   sbatch --export=ALL,SCREENING_THRESHOLD=50 scripts/nesh-pipeline.sbatch
#   sbatch --export=ALL,SCREENING_THRESHOLD=50,QUERY="particle dispersion",N=20 scripts/nesh-pipeline.sbatch
#   sbatch --export=ALL,STOP_AFTER_STAGE=3,N=200 scripts/nesh-pipeline.sbatch
#
# SCREENING_THRESHOLD has no default: the runner exits without it when the
# run reaches stage 7 (or stage 5 with EXPORT_GAP=1), unless the review
# config sets screening_threshold. The default config leaves it unset.
#
# The job drives the runner through env overrides (QUERY, N,
# STOP_AFTER_STAGE, SCREENING_THRESHOLD, *_MODEL, …), which win over the
# config under the runner's env > config > default precedence. With no
# REVIEW_CONFIG the runner uses its default config
# (examples/reviews/lagrangian-oceanography.yaml). To drive a different
# review from NESH, set REVIEW_CONFIG=path/to/review.yaml (it is forwarded
# as the runner's positional argument).

set -euo pipefail

# NESH compute-node proxy (see docs/explorations/nesh-ollama.md).
# Compute nodes cannot reach the public internet without this.
export http_proxy="${http_proxy:-http://10.0.7.235:3128}"
export https_proxy="${https_proxy:-http://10.0.7.235:3128}"
export no_proxy="${no_proxy:-localhost,127.0.0.1}"

REPO_DIR="${REPO_DIR:-$WORK/2026_laglitsynth}"
GROBID_SIF="${GROBID_SIF:-data/nesh/grobid-0.8.0.sif}"
GROBID_IMAGE="${GROBID_IMAGE:-docker://lfoppiano/grobid:0.8.0}"
QUERY="${QUERY:-Lagrangian particle tracking}"
N="${N:-10}"

cd "$REPO_DIR"
mkdir -p logs

[[ -f .env ]] || {
    echo "Missing $REPO_DIR/.env (OPENALEX_API_KEY, UNPAYWALL_EMAIL)" >&2
    exit 2
}
set -a
source .env
set +a

echo "==> Node $(hostname)"

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

cleanup() {
    [[ -n "${GROBID_PID:-}" ]] && kill "$GROBID_PID" 2>/dev/null || true
}
trap cleanup EXIT

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

# Run the smoke-test end-to-end. QUERY / N reach the runner as env
# overrides (exported below), not as positionals — the runner reads them
# under env > config > default. The only positional is the optional
# per-review config (REVIEW_CONFIG); unset means the runner's default.
export GROBID_URL="http://localhost:8070"
export QUERY N
export STOP_AFTER_STAGE="${STOP_AFTER_STAGE:-8}"

pixi run scripts/run-pipeline.sh ${REVIEW_CONFIG:+"$REVIEW_CONFIG"}
