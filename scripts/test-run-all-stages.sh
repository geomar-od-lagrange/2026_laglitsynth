#!/usr/bin/env bash
# Smoke test: run all 8 implemented pipeline stages end-to-end on a tiny sample.
#
# Outputs are written under data/test-run/ so they don't collide with real runs.
#
# Prerequisites:
#   - .env with OPENALEX_API_KEY and UNPAYWALL_EMAIL
#   - Ollama serving at $OLLAMA_BASE  (stages 3, 7, 8)
#   - GROBID  serving at $GROBID_URL  (stage 6)
#
# Usage:
#   scripts/test-run-all-stages.sh [QUERY] [N]
#
#   QUERY  OpenAlex search string  (default: "Lagrangian particle tracking")
#   N      max records per stage   (default: 5)
#
# Override models / endpoints via env vars:
#   OLLAMA_BASE, GROBID_URL,
#   SCREENING_MODEL, ELIGIBILITY_MODEL, EXTRACTION_MODEL.

set -euo pipefail

QUERY="${1:-Lagrangian particle tracking}"
N="${2:-5}"

ROOT="data/test-run"
OLLAMA_BASE="${OLLAMA_BASE:-http://localhost:11434}"
GROBID_URL="${GROBID_URL:-http://localhost:8070}"
SCREENING_MODEL="${SCREENING_MODEL:-gemma3:4b}"
ELIGIBILITY_MODEL="${ELIGIBILITY_MODEL:-gemma3:4b}"
EXTRACTION_MODEL="${EXTRACTION_MODEL:-llama3.1:8b}"

mkdir -p "$ROOT"

echo "==> [1/8] catalogue-fetch"
laglitsynth catalogue-fetch "$QUERY" \
    --output "$ROOT/catalogue-fetch/catalogue.jsonl" \
    --max-records "$N"

echo "==> [2/8] catalogue-dedup"
laglitsynth catalogue-dedup \
    --input "$ROOT/catalogue-fetch/catalogue.jsonl" \
    --output-dir "$ROOT/catalogue-dedup"

echo "==> [3/8] screening-abstracts"
laglitsynth screening-abstracts \
    "$ROOT/catalogue-dedup/deduplicated.jsonl" \
    "Is this abstract about Lagrangian particle tracking in oceanography?" \
    --output-dir "$ROOT/screening-abstracts" \
    --model "$SCREENING_MODEL" \
    --base-url "$OLLAMA_BASE"

echo "==> [4/8] screening-adjudication"
laglitsynth screening-adjudication \
    --input "$ROOT/screening-abstracts/verdicts.jsonl" \
    --catalogue "$ROOT/catalogue-dedup/deduplicated.jsonl" \
    --output-dir "$ROOT/screening-adjudication"

echo "==> [5/8] fulltext-retrieval"
laglitsynth fulltext-retrieval \
    --input "$ROOT/screening-adjudication/included.jsonl" \
    --output-dir "$ROOT/fulltext-retrieval"

echo "==> [6/8] fulltext-extraction"
laglitsynth fulltext-extraction \
    --pdf-dir "$ROOT/fulltext-retrieval/pdfs" \
    --output-dir "$ROOT/fulltext-extraction" \
    --grobid-url "$GROBID_URL"

echo "==> [7/8] fulltext-eligibility"
laglitsynth fulltext-eligibility \
    --catalogue "$ROOT/screening-adjudication/included.jsonl" \
    --extractions "$ROOT/fulltext-extraction/extraction.jsonl" \
    --output-dir "$ROOT/fulltext-eligibility" \
    --model "$ELIGIBILITY_MODEL" \
    --base-url "$OLLAMA_BASE"

echo "==> [8/8] extraction-codebook"
laglitsynth extraction-codebook \
    --eligible "$ROOT/fulltext-eligibility/eligible.jsonl" \
    --extractions "$ROOT/fulltext-extraction/extraction.jsonl" \
    --output-dir "$ROOT/extraction-codebook" \
    --model "$EXTRACTION_MODEL" \
    --base-url "$OLLAMA_BASE"

echo
echo "Test run complete. Outputs under: $ROOT/"
