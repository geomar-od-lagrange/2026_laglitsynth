#!/usr/bin/env bash
# Run pipeline stages 1..STOP_AFTER_STAGE end-to-end, driven by one
# per-review config YAML.
#
# Defaults to a tiny smoke-sized run; point at a real review config (or
# raise N / override QUERY) for production runs. Outputs are written
# under data/run/ so successive runs can share the catalogue / dedup
# output without re-fetching.
#
# Prerequisites:
#   - .env with OPENALEX_API_KEY and UNPAYWALL_EMAIL
#   - Ollama serving at $OLLAMA_BASE  (stages 3, 7, 8)
#   - GROBID  serving at $GROBID_URL  (stage 6)
#
# Usage:
#   scripts/run-pipeline.sh [config.yaml]
#
#   config.yaml  per-review config (default:
#                examples/reviews/lagrangian-oceanography.yaml — the smoke
#                run). Validated and emitted as CFG_* assignments by
#                `laglitsynth review-config`; see docs/configs.md.
#
# Precedence for every knob is env > config > built-in default. Each is
# resolved as VAR="${VAR:-${CFG_VAR:-default}}", so an env var (or
# `sbatch --export=`) overrides the config, which overrides the default,
# and an absent config field falls through to today's default.
#
# Env overrides (each wins over the config file):
#   QUERY                  OpenAlex search string
#   N                      max records fetched at stage 1
#   FROM_YEAR, TO_YEAR     stage-1 publication-year window (omitted if unset)
#   SCREENING_CRITERIA     stage-3 screening-criteria YAML
#   ELIGIBILITY_CRITERIA   stage-7 eligibility-criteria YAML
#   CODEBOOK               stage-8 codebook YAML
#   SCREENING_THRESHOLD    shared default for the two corpus thresholds (50)
#   RETRIEVAL_THRESHOLD    stage-5 PDF-fetch cutoff   (default SCREENING_THRESHOLD)
#   ELIGIBILITY_THRESHOLD  stage-7 LLM-pass cutoff    (default SCREENING_THRESHOLD)
#   OUTPUT_ROOT, OLLAMA_BASE, GROBID_URL,
#   SCREENING_MODEL, ELIGIBILITY_MODEL, EXTRACTION_MODEL,
#   ELIGIBILITY_NUM_CTX    (--num-ctx for stage 7; default 32768),
#   EXTRACTION_NUM_CTX     (--num-ctx for stage 8; default 32768),
#   LLM_CONCURRENCY        (--concurrency for stages 3 and 7; default 1),
#   ELIGIBILITY_CONCURRENCY (--concurrency for stage 7 only; default LLM_CONCURRENCY),
#   EXTRACTION_CONCURRENCY  (--concurrency for stage 8 only; default 1 — prefill-bound),
#   RUN_ID (LLM-stage leaf under <data-dir>/<stage-subdir>/<run-id>/;
#           a fresh ISO+hex id is generated when unset).
#   EXPORT_GAP             1 = after stage 5, export the still-missing
#                          selection as a collaborator handoff bundle into
#                          <root>/pdfs/export/ (default 0).
#
# Stage gating:
#   STOP_AFTER_STAGE=N  run stages 1..N and stop (default: 8 — full pipeline).
#   Useful while stabilising upstream stages: STOP_AFTER_STAGE=3 iterates on
#   screening without paying retrieval/extraction time.
#   STOP_AFTER_STAGE=5 EXPORT_GAP=1 is the collaborator handoff run: screen the
#   corpus, fetch what open access gives, then export the remaining gap.

set -euo pipefail

if [[ -f .env ]]; then
    set -a
    source .env
    set +a
fi

CONFIG="${1:-examples/reviews/lagrangian-oceanography.yaml}"

# Validate the per-review config and pull in its CFG_* assignments. The
# CFG_* values become the middle tier of the env > config > default
# precedence applied below.
eval "$(laglitsynth review-config "$CONFIG")"

# Query / record cap (historical env overrides preserved).
QUERY="${QUERY:-${CFG_QUERY:-Lagrangian particle tracking}}"
N="${N:-${CFG_MAX_RECORDS:-5}}"
FROM_YEAR="${FROM_YEAR:-${CFG_FROM_YEAR:-}}"
TO_YEAR="${TO_YEAR:-${CFG_TO_YEAR:-}}"

# Criteria / codebook YAMLs.
SCREENING_CRITERIA="${SCREENING_CRITERIA:-${CFG_SCREENING_CRITERIA:-examples/screening-criteria/lagrangian-oceanography.yaml}}"
ELIGIBILITY_CRITERIA="${ELIGIBILITY_CRITERIA:-${CFG_ELIGIBILITY_CRITERIA:-examples/eligibility-criteria/lagrangian-oceanography.yaml}}"
CODEBOOK="${CODEBOOK:-${CFG_CODEBOOK:-examples/codebooks/lagrangian-oceanography.yaml}}"

# Corpus thresholds: independently settable, sharing a common default.
# SCREENING_THRESHOLD is only the fallback both inherit when neither the
# specific env var nor the config pins them.
SCREENING_THRESHOLD="${SCREENING_THRESHOLD:-50}"
RETRIEVAL_THRESHOLD="${RETRIEVAL_THRESHOLD:-${CFG_RETRIEVAL_THRESHOLD:-$SCREENING_THRESHOLD}}"
ELIGIBILITY_THRESHOLD="${ELIGIBILITY_THRESHOLD:-${CFG_ELIGIBILITY_THRESHOLD:-$SCREENING_THRESHOLD}}"

# Models.
SCREENING_MODEL="${SCREENING_MODEL:-${CFG_SCREENING_MODEL:-gemma3:4b}}"
ELIGIBILITY_MODEL="${ELIGIBILITY_MODEL:-${CFG_ELIGIBILITY_MODEL:-gemma3:4b}}"
EXTRACTION_MODEL="${EXTRACTION_MODEL:-${CFG_EXTRACTION_MODEL:-llama3.1:8b}}"

# Context windows.
ELIGIBILITY_NUM_CTX="${ELIGIBILITY_NUM_CTX:-${CFG_ELIGIBILITY_NUM_CTX:-32768}}"
EXTRACTION_NUM_CTX="${EXTRACTION_NUM_CTX:-${CFG_EXTRACTION_NUM_CTX:-32768}}"

# Concurrency.
LLM_CONCURRENCY="${LLM_CONCURRENCY:-${CFG_LLM_CONCURRENCY:-1}}"
ELIGIBILITY_CONCURRENCY="${ELIGIBILITY_CONCURRENCY:-$LLM_CONCURRENCY}"
EXTRACTION_CONCURRENCY="${EXTRACTION_CONCURRENCY:-${CFG_EXTRACTION_CONCURRENCY:-1}}"

# Collaborator handoff: export the still-missing selection after stage 5.
EXPORT_GAP="${EXPORT_GAP:-${CFG_EXPORT_GAP:-0}}"

ROOT="${OUTPUT_ROOT:-data/run}"
OLLAMA_BASE="${OLLAMA_BASE:-http://localhost:11434}"
GROBID_URL="${GROBID_URL:-http://localhost:8070}"
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

# Stage 1: append --from-year / --to-year only when set.
stage1_year_flags=()
[[ -n "$FROM_YEAR" ]] && stage1_year_flags+=(--from-year "$FROM_YEAR")
[[ -n "$TO_YEAR" ]] && stage1_year_flags+=(--to-year "$TO_YEAR")

run_stage 1 catalogue-fetch \
    laglitsynth catalogue-fetch "$QUERY" \
        --output "$ROOT/catalogue-fetch/catalogue.jsonl" \
        --max-records "$N" \
        --api-key "$OPENALEX_API_KEY" \
        ${stage1_year_flags[@]+"${stage1_year_flags[@]}"}

run_stage 2 catalogue-dedup \
    laglitsynth catalogue-dedup \
        --input "$ROOT/catalogue-fetch/catalogue.jsonl" \
        --output-dir "$ROOT/catalogue-dedup"

run_stage 3 screening-abstracts \
    laglitsynth screening-abstracts \
        "$ROOT/catalogue-dedup/deduplicated.jsonl" \
        --screening-criteria "$SCREENING_CRITERIA" \
        --data-dir "$ROOT" \
        --run-id "$RUN_ID" \
        --model "$SCREENING_MODEL" \
        --base-url "$OLLAMA_BASE" \
        --concurrency "$LLM_CONCURRENCY"

run_stage 5 fulltext-retrieval \
    laglitsynth fulltext-retrieval \
        --catalogue "$ROOT/catalogue-dedup/deduplicated.jsonl" \
        --screening-verdicts "$ROOT/screening-abstracts/$RUN_ID/verdicts.jsonl" \
        --screening-threshold "$RETRIEVAL_THRESHOLD" \
        --data-dir "$ROOT" \
        --email "$UNPAYWALL_EMAIL"

if [[ "$EXPORT_GAP" == "1" ]]; then
    run_stage 5 fulltext-retrieval-export \
        laglitsynth fulltext-retrieval-export \
            --catalogue "$ROOT/catalogue-dedup/deduplicated.jsonl" \
            --screening-verdicts "$ROOT/screening-abstracts/$RUN_ID/verdicts.jsonl" \
            --screening-threshold "$RETRIEVAL_THRESHOLD" \
            --data-dir "$ROOT"
fi

run_stage 6 fulltext-extraction \
    laglitsynth fulltext-extraction \
        --pdf-dir "$ROOT/pdfs" \
        --output-dir "$ROOT/fulltext-extraction" \
        --grobid-url "$GROBID_URL" \
        --skip-existing

run_stage 7 fulltext-eligibility \
    laglitsynth fulltext-eligibility \
        --catalogue "$ROOT/catalogue-dedup/deduplicated.jsonl" \
        --screening-verdicts "$ROOT/screening-abstracts/$RUN_ID/verdicts.jsonl" \
        --screening-threshold "$ELIGIBILITY_THRESHOLD" \
        --eligibility-criteria "$ELIGIBILITY_CRITERIA" \
        --extractions "$ROOT/fulltext-extraction/extraction.jsonl" \
        --data-dir "$ROOT" \
        --run-id "$RUN_ID" \
        --model "$ELIGIBILITY_MODEL" \
        --base-url "$OLLAMA_BASE" \
        --num-ctx "$ELIGIBILITY_NUM_CTX" \
        --concurrency "$ELIGIBILITY_CONCURRENCY"

run_stage 8 extraction-codebook \
    laglitsynth extraction-codebook \
        --catalogue "$ROOT/catalogue-dedup/deduplicated.jsonl" \
        --eligibility-verdicts "$ROOT/fulltext-eligibility/$RUN_ID/verdicts.jsonl" \
        --extractions "$ROOT/fulltext-extraction/extraction.jsonl" \
        --codebook "$CODEBOOK" \
        --data-dir "$ROOT" \
        --run-id "$RUN_ID" \
        --model "$EXTRACTION_MODEL" \
        --base-url "$OLLAMA_BASE" \
        --num-ctx "$EXTRACTION_NUM_CTX" \
        --concurrency "$EXTRACTION_CONCURRENCY"

echo
echo "Pipeline complete (stages 1..$STOP_AFTER_STAGE). Outputs under: $ROOT/"
echo "LLM-stage leaf for this run: $RUN_ID"
