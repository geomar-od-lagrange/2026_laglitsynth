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
#   - .env with OPENALEX_API_KEY, UNPAYWALL_EMAIL and LLM_API_KEY
#   - an OpenAI-compatible LLM endpoint for stages 3, 7, 8 (LLM_BASE_URL)
#   - GROBID serving at $GROBID_URL (stage 6)
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
#   SCREENING_THRESHOLD    relevance-score cutoff; no default. Required (env
#                          or config) when the run reaches stage 7, or
#                          stage 5 with EXPORT_GAP=1
#   LLM_BASE_URL           OpenAI-compatible base URL including /v1; no
#                          default. Required (env or config) from stage 3 on
#   LLM_DECISIONS_URL      decisions endpoint for stage 3, passed as
#                          --decisions-url when set (default: the CLI's
#                          <LLM_BASE_URL>/decisions)
#   SCREENING_MODEL, ELIGIBILITY_MODEL, EXTRACTION_MODEL
#                          no defaults; each is required (env or config)
#                          when the run reaches its stage
#   OUTPUT_ROOT, GROBID_URL,
#   LLM_CONCURRENCY        (--concurrency for stages 3 and 7; default 1),
#   ELIGIBILITY_CONCURRENCY (--concurrency for stage 7 only; default LLM_CONCURRENCY),
#   EXTRACTION_CONCURRENCY  (--concurrency for stage 8 only; default 1 — prefill-bound),
#   RUN_ID                 run-id leaf for stages 3, 7, 8 and the stages that
#                          read their verdicts. Unset (the default), every
#                          stage takes the run_id in <root>/manifest.json;
#                          set, it is passed as --run-id to each of them.
#   EXPORT_GAP             1 = after stage 5, export the still-missing
#                          selection as a collaborator handoff bundle into
#                          <root>/pdfs/export/ (default 0). Env only — it is
#                          a one-shot action, not a property of the review.
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

# Screening threshold: a review decision, so it has no built-in default.
# Checked below, once STOP_AFTER_STAGE says whether a stage uses it.
SCREENING_THRESHOLD="${SCREENING_THRESHOLD:-${CFG_SCREENING_THRESHOLD:-}}"

# LLM endpoint and models: no built-in defaults, the review names them.
# Checked below, once STOP_AFTER_STAGE says which stages run. The API key
# (LLM_API_KEY, env or the sourced .env) is never passed as a flag: the
# CLI reads it itself, so it stays out of the process list.
LLM_BASE_URL="${LLM_BASE_URL:-${CFG_LLM_BASE_URL:-}}"
LLM_DECISIONS_URL="${LLM_DECISIONS_URL:-${CFG_LLM_DECISIONS_URL:-}}"
SCREENING_MODEL="${SCREENING_MODEL:-${CFG_SCREENING_MODEL:-}}"
ELIGIBILITY_MODEL="${ELIGIBILITY_MODEL:-${CFG_ELIGIBILITY_MODEL:-}}"
EXTRACTION_MODEL="${EXTRACTION_MODEL:-${CFG_EXTRACTION_MODEL:-}}"

# Concurrency.
LLM_CONCURRENCY="${LLM_CONCURRENCY:-${CFG_LLM_CONCURRENCY:-1}}"
ELIGIBILITY_CONCURRENCY="${ELIGIBILITY_CONCURRENCY:-$LLM_CONCURRENCY}"
EXTRACTION_CONCURRENCY="${EXTRACTION_CONCURRENCY:-${CFG_EXTRACTION_CONCURRENCY:-1}}"

# Collaborator handoff: export the still-missing selection after stage 5.
EXPORT_GAP="${EXPORT_GAP:-0}"

ROOT="${OUTPUT_ROOT:-data/run}"
GROBID_URL="${GROBID_URL:-http://localhost:8070}"
STOP_AFTER_STAGE="${STOP_AFTER_STAGE:-8}"

[[ "$STOP_AFTER_STAGE" =~ ^[0-9]+$ ]] || {
    echo "STOP_AFTER_STAGE must be a positive integer (got $STOP_AFTER_STAGE)" >&2
    exit 2
}

# Only stage 7 and the stage-5 handoff export read the threshold, so a
# run that stops before them does not need one.
if [[ "$STOP_AFTER_STAGE" -ge 7 ]] || [[ "$EXPORT_GAP" == "1" && "$STOP_AFTER_STAGE" -ge 5 ]]; then
    : "${SCREENING_THRESHOLD:?SCREENING_THRESHOLD required — set screening_threshold in the review config or export it}"
fi

if [[ "$STOP_AFTER_STAGE" -ge 3 ]]; then
    : "${LLM_BASE_URL:?LLM_BASE_URL required — set llm_base_url in the review config or export it}"
    : "${LLM_API_KEY:?LLM_API_KEY required — set in .env or export it}"
    : "${SCREENING_MODEL:?SCREENING_MODEL required — set models.screening in the review config or export it}"
fi
if [[ "$STOP_AFTER_STAGE" -ge 7 ]]; then
    : "${ELIGIBILITY_MODEL:?ELIGIBILITY_MODEL required — set models.eligibility in the review config or export it}"
fi
if [[ "$STOP_AFTER_STAGE" -ge 8 ]]; then
    : "${EXTRACTION_MODEL:?EXTRACTION_MODEL required — set models.extraction in the review config or export it}"
fi

: "${OPENALEX_API_KEY:?OPENALEX_API_KEY required — set in .env}"
: "${UNPAYWALL_EMAIL:?UNPAYWALL_EMAIL required — set in .env}"

mkdir -p "$ROOT"

# The manifest carries the run-id every run-id-aware stage reads when
# --run-id is omitted. One per review: created on the first run, kept after.
if [[ ! -f "$ROOT/manifest.json" ]]; then
    laglitsynth manifest-init "$QUERY" --data-dir "$ROOT"
fi

# RUN_ID set: pass it explicitly to every stage that writes or reads a
# <run-id>/ leaf. Unset: each stage reads the manifest's run_id itself.
run_id_flags=()
[[ -n "${RUN_ID:-}" ]] && run_id_flags=(--run-id "$RUN_ID")

# --decisions-url only when set; the CLI otherwise derives it from --base-url.
decisions_url_flags=()
[[ -n "$LLM_DECISIONS_URL" ]] && decisions_url_flags=(--decisions-url "$LLM_DECISIONS_URL")

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
        --data-dir "$ROOT"

run_stage 3 screening-abstracts \
    laglitsynth screening-abstracts \
        --screening-criteria "$SCREENING_CRITERIA" \
        --data-dir "$ROOT" \
        ${run_id_flags[@]+"${run_id_flags[@]}"} \
        --base-url "$LLM_BASE_URL" \
        ${decisions_url_flags[@]+"${decisions_url_flags[@]}"} \
        --model "$SCREENING_MODEL" \
        --concurrency "$LLM_CONCURRENCY"

run_stage 5 fulltext-retrieval \
    laglitsynth fulltext-retrieval \
        --data-dir "$ROOT" \
        --email "$UNPAYWALL_EMAIL"

if [[ "$EXPORT_GAP" == "1" ]]; then
    run_stage 5 "fulltext-retrieval-export (handoff bundle)" \
        laglitsynth fulltext-retrieval-export \
            --screening-threshold "$SCREENING_THRESHOLD" \
            --data-dir "$ROOT" \
            ${run_id_flags[@]+"${run_id_flags[@]}"}
fi

run_stage 6 fulltext-extraction \
    laglitsynth fulltext-extraction \
        --data-dir "$ROOT" \
        --grobid-url "$GROBID_URL" \
        --skip-existing

run_stage 7 fulltext-eligibility \
    laglitsynth fulltext-eligibility \
        --screening-threshold "$SCREENING_THRESHOLD" \
        --eligibility-criteria "$ELIGIBILITY_CRITERIA" \
        --data-dir "$ROOT" \
        ${run_id_flags[@]+"${run_id_flags[@]}"} \
        --base-url "$LLM_BASE_URL" \
        --model "$ELIGIBILITY_MODEL" \
        --concurrency "$ELIGIBILITY_CONCURRENCY"

run_stage 8 extraction-codebook \
    laglitsynth extraction-codebook \
        --codebook "$CODEBOOK" \
        --data-dir "$ROOT" \
        ${run_id_flags[@]+"${run_id_flags[@]}"} \
        --base-url "$LLM_BASE_URL" \
        --model "$EXTRACTION_MODEL" \
        --concurrency "$EXTRACTION_CONCURRENCY"

echo
echo "Pipeline complete (stages 1..$STOP_AFTER_STAGE). Outputs under: $ROOT/"
echo "Run-id: ${RUN_ID:-the run_id in $ROOT/manifest.json}"
