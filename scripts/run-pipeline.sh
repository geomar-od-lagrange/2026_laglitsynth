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
#   LLM_CONCURRENCY (forwarded to every LLM-driven stage that accepts it).

set -euo pipefail

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

[[ "$STOP_AFTER_STAGE" =~ ^[1-8]$ ]] || {
    echo "STOP_AFTER_STAGE must be 1..8 (got $STOP_AFTER_STAGE)" >&2
    exit 2
}

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
        --max-records "$N"

run_stage 2 catalogue-dedup \
    laglitsynth catalogue-dedup \
        --input "$ROOT/catalogue-fetch/catalogue.jsonl" \
        --output-dir "$ROOT/catalogue-dedup"

run_stage 3 screening-abstracts \
    laglitsynth screening-abstracts \
        "$ROOT/catalogue-dedup/deduplicated.jsonl" \
        "Is this abstract about Lagrangian particle tracking in oceanography?" \
        --output-dir "$ROOT/screening-abstracts" \
        --model "$SCREENING_MODEL" \
        --base-url "$OLLAMA_BASE" \
        --concurrency "$LLM_CONCURRENCY"

run_stage 4 screening-adjudication \
    laglitsynth screening-adjudication \
        --input "$ROOT/screening-abstracts/verdicts.jsonl" \
        --catalogue "$ROOT/catalogue-dedup/deduplicated.jsonl" \
        --output-dir "$ROOT/screening-adjudication"

run_stage 5 fulltext-retrieval \
    laglitsynth fulltext-retrieval \
        --input "$ROOT/screening-adjudication/included.jsonl" \
        --output-dir "$ROOT/fulltext-retrieval"

run_stage 6 fulltext-extraction \
    laglitsynth fulltext-extraction \
        --pdf-dir "$ROOT/fulltext-retrieval/pdfs" \
        --output-dir "$ROOT/fulltext-extraction" \
        --grobid-url "$GROBID_URL"

# Stages 7 and 8 do not yet accept --concurrency; they run sequentially
# regardless of LLM_CONCURRENCY. See docs/llm-concurrency.md for the
# follow-up.
run_stage 7 fulltext-eligibility \
    laglitsynth fulltext-eligibility \
        --catalogue "$ROOT/screening-adjudication/included.jsonl" \
        --extractions "$ROOT/fulltext-extraction/extraction.jsonl" \
        --output-dir "$ROOT/fulltext-eligibility" \
        --model "$ELIGIBILITY_MODEL" \
        --base-url "$OLLAMA_BASE"

run_stage 8 extraction-codebook \
    laglitsynth extraction-codebook \
        --eligible "$ROOT/fulltext-eligibility/eligible.jsonl" \
        --extractions "$ROOT/fulltext-extraction/extraction.jsonl" \
        --output-dir "$ROOT/extraction-codebook" \
        --model "$EXTRACTION_MODEL" \
        --base-url "$OLLAMA_BASE"

echo
echo "Pipeline complete (stages 1..$STOP_AFTER_STAGE). Outputs under: $ROOT/"
