#!/usr/bin/env bash
# Run pipeline stages 1..STOP_AFTER_STAGE end-to-end.
#
# Defaults to a tiny smoke-sized run; raise N (and override QUERY) for real
# runs. Outputs are written under data/run/ so successive runs can share the
# catalogue / dedup output without re-fetching.
#
# Prerequisites:
#   - .env with OPENALEX_API_KEY and UNPAYWALL_EMAIL
#   - Ollama serving at $OLLAMA_BASE  (stages 3, 7, 8)
#   - GROBID  serving at $GROBID_URL  (stage 6)
#
# Usage:
#   scripts/run-pipeline.sh [QUERY] [N]
#
#   QUERY  OpenAlex search string  (default: "Lagrangian particle tracking")
#   N      max records per stage   (default: 5)
#
# Stage gating:
#   STOP_AFTER_STAGE=N  run stages 1..N and stop (default: 8 — full pipeline).
#   Useful while stabilising upstream stages: STOP_AFTER_STAGE=3 iterates on
#   screening without paying retrieval/extraction time.
#
# Override models / endpoints via env vars:
#   OUTPUT_ROOT, OLLAMA_BASE, GROBID_URL,
#   SCREENING_MODEL, ELIGIBILITY_MODEL, EXTRACTION_MODEL,
#   LLM_CONCURRENCY (forwarded to every LLM-driven stage that accepts it),
#   RUN_ID (LLM-stage leaf under <data-dir>/<stage-subdir>/<run-id>/;
#           a fresh ISO+hex id is generated when unset).

set -euo pipefail

if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

QUERY="${1:-Lagrangian particle tracking}"
N="${2:-5}"

ROOT="${OUTPUT_ROOT:-data/run}"
OLLAMA_BASE="${OLLAMA_BASE:-http://localhost:11434}"
GROBID_URL="${GROBID_URL:-http://localhost:8070}"
SCREENING_MODEL="${SCREENING_MODEL:-gemma3:4b}"
ELIGIBILITY_MODEL="${ELIGIBILITY_MODEL:-gemma3:4b}"
EXTRACTION_MODEL="${EXTRACTION_MODEL:-llama3.1:8b}"
LLM_CONCURRENCY="${LLM_CONCURRENCY:-1}"
STOP_AFTER_STAGE="${STOP_AFTER_STAGE:-8}"

# Single run-id threaded through stages 3, 7, 8 so downstream inputs
# land at predictable <stage>/<RUN_ID>/ paths within $ROOT.
RUN_ID="${RUN_ID:-$(laglitsynth generate-run-id)}"

[[ "$STOP_AFTER_STAGE" =~ ^[0-9]+$ ]] || {
    echo "STOP_AFTER_STAGE must be a positive integer (got $STOP_AFTER_STAGE)" >&2
    exit 2
}

: "${OPENALEX_API_KEY:?OPENALEX_API_KEY required — set in .env}"
: "${UNPAYWALL_EMAIL:?UNPAYWALL_EMAIL required — set in .env}"

mkdir -p "$ROOT"

run_stage() {
    local n="$1" name="$2"
    shift 2
    [[ "$n" -gt "$STOP_AFTER_STAGE" ]] && return 0
    echo "==> [$n/8] $name"
    "$@"
}

run_stage 1 catalogue-fetch \
    laglitsynth catalogue-fetch "$QUERY" \
        --output "$ROOT/catalogue-fetch/catalogue.jsonl" \
        --max-records "$N" \
        --api-key "$OPENALEX_API_KEY"

run_stage 2 catalogue-dedup \
    laglitsynth catalogue-dedup \
        --input "$ROOT/catalogue-fetch/catalogue.jsonl" \
        --output-dir "$ROOT/catalogue-dedup"

run_stage 3 screening-abstracts \
    laglitsynth screening-abstracts \
        "$ROOT/catalogue-dedup/deduplicated.jsonl" \
        "On a scale from 0% (not relevant) to 100% (perfectly relevant), how relevant is this work to Lagrangian particle tracking in oceanography?" \
        --data-dir "$ROOT" \
        --run-id "$RUN_ID" \
        --model "$SCREENING_MODEL" \
        --base-url "$OLLAMA_BASE" \
        --concurrency "$LLM_CONCURRENCY"

run_stage 5 fulltext-retrieval \
    laglitsynth fulltext-retrieval \
        --catalogue "$ROOT/catalogue-dedup/deduplicated.jsonl" \
        --screening-verdicts "$ROOT/screening-abstracts/$RUN_ID/verdicts.jsonl" \
        --screening-threshold 50 \
        --output-dir "$ROOT/fulltext-retrieval" \
        --email "$UNPAYWALL_EMAIL" \
        --skip-existing

run_stage 6 fulltext-extraction \
    laglitsynth fulltext-extraction \
        --pdf-dir "$ROOT/fulltext-retrieval/pdfs" \
        --output-dir "$ROOT/fulltext-extraction" \
        --grobid-url "$GROBID_URL" \
        --skip-existing

# Stages 7 and 8 do not yet accept --concurrency; they run sequentially
# regardless of LLM_CONCURRENCY. See docs/llm-concurrency.md for the
# follow-up.
run_stage 7 fulltext-eligibility \
    laglitsynth fulltext-eligibility \
        --catalogue "$ROOT/screening-adjudication/included.jsonl" \
        --extractions "$ROOT/fulltext-extraction/extraction.jsonl" \
        --data-dir "$ROOT" \
        --run-id "$RUN_ID" \
        --model "$ELIGIBILITY_MODEL" \
        --base-url "$OLLAMA_BASE"

run_stage 8 extraction-codebook \
    laglitsynth extraction-codebook \
        --eligible "$ROOT/fulltext-eligibility/$RUN_ID/eligible.jsonl" \
        --extractions "$ROOT/fulltext-extraction/extraction.jsonl" \
        --data-dir "$ROOT" \
        --run-id "$RUN_ID" \
        --model "$EXTRACTION_MODEL" \
        --base-url "$OLLAMA_BASE"

echo
echo "Pipeline complete (stages 1..$STOP_AFTER_STAGE). Outputs under: $ROOT/"
echo "LLM-stage leaf for this run: $RUN_ID"
