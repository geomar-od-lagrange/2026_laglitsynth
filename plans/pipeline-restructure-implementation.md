# Pipeline restructure: implementation

## Goal

Implement [pipeline-restructure.md](pipeline-restructure.md): shrink the
surface, put every LLM stage on one OpenAI-compatible endpoint, add
`corpus-assemble`, and rewire stages 6–8 to read only `data/corpus/`. The
order puts a full run of the analysis half first. The export/import changes
of mission step 4 come last.

## Non-goals

This plan does not implement synthesis: it only replaces stages 10–12 with
one planned `synthesis` step in the docs. Retrieval keeps its current two
routes: trying every OpenAlex location plus Semantic Scholar and arXiv
(mission step 2) is a follow-up, and the store already holds those PDFs from
one-off scripts. Title-based import matching stays queued in the
[roadmap](roadmap.md). The Ollama explorations under
[docs/explorations/](../docs/explorations/) are left as history.

## Target state

### Deleted

- Stage 2b: [`abstract_lookup/`](../src/laglitsynth/abstract_lookup/),
  [docs/abstract-lookup.md](../docs/abstract-lookup.md), and the
  `ABSTRACT_LOOKUP_EMAIL` and `SEMANTIC_SCHOLAR_API_KEY` keys in
  `.env.example`.
- Stage 9: [docs/adjudication-extraction.md](../docs/adjudication-extraction.md),
  [plans/multi-run-consensus.md](multi-run-consensus.md), and the
  `reviewer_correction` column of `extraction-codebook-export` (the per-work
  tab becomes `field | value | context`).
- XLSX review exports for screening and eligibility:
  [`screening_abstracts/export.py`](../src/laglitsynth/screening_abstracts/export.py)
  and [`fulltext_eligibility/export.py`](../src/laglitsynth/fulltext_eligibility/export.py).
  `extraction-codebook-export` stays, so `openpyxl` stays.
- `--pass-all` and the `unscreened` sentinel. `ScreeningMeta.llm` is no
  longer optional.
- Every threshold outside `corpus-assemble`. Screening loses
  `--screening-threshold`, its above/below console split, and
  `ScreeningMeta.threshold`, `above_threshold_count` and
  `below_threshold_count`; the scored, skipped and sentinel counts stay.
  `ScreeningMeta.criterion` goes with the screening `system_prompt`.
- The screening join in `fulltext-retrieval`: its `_active_works`,
  `--screening-verdicts`, `--run-id` and `--screening-threshold` go.
  Retrieval runs over the whole `--catalogue`. The export keeps the join
  (moved into [`export.py`](../src/laglitsynth/fulltext_retrieval/export.py))
  until commit 8.
- Ollama: [`ollama.py`](../src/laglitsynth/ollama.py),
  [`bake_model.py`](../src/laglitsynth/bake_model.py) and
  [docs/bake-model.md](../docs/bake-model.md). Also `--num-ctx`, the
  `extra_body` `num_ctx` hint, `CHAR_BUDGET` and its block truncation in
  [`extraction_codebook/prompts.py`](../src/laglitsynth/extraction_codebook/prompts.py),
  the `truncated` record field, the explicit `temperature` and per-call
  `seed`, the `seed` field on every verdict and record, `--backend`, and
  the chat path of screening. In
  [nesh-pipeline.sbatch](../scripts/nesh-pipeline.sbatch) the Ollama install
  check, `ollama serve`, its wait loop, the pulls and the Modelfile tags go,
  and the GPU request (`--partition=gpu`, `--constraint`, `--gpus-per-node`)
  becomes `--partition=base`. GROBID and the proxy exports stay; the hosted
  endpoint is reached through the proxy.
- [`ReviewConfig`](../src/laglitsynth/review.py) loses `backends`, `num_ctx`
  and `thresholds`. The runner loses `RETRIEVAL_THRESHOLD`,
  `ELIGIBILITY_THRESHOLD`, `OLLAMA_BASE`, `SCREENING_BACKEND` and
  `*_NUM_CTX`.

### LLM backend: `src/laglitsynth/llm.py`

One module replaces `ollama.py` and
[`screening_abstracts/decisions.py`](../src/laglitsynth/screening_abstracts/decisions.py).
The three LLM stages register `--base-url`, `--model`, `--api-key` and
`--concurrency` with `add_endpoint_args(parser, decisions=...)`; stages 3
and 7 pass `decisions=True`, which adds `--decisions-url`.
`resolve_endpoint(args)` returns a frozen `LlmEndpoint {base_url, model,
api_key, decisions_url}`. `base_url` is the OpenAI-compatible base
including `/v1`, exactly what the `openai` SDK takes. `decisions_url`
defaults to `base_url.rstrip("/") + "/decisions"`, and `--decisions-url`
overrides it for a provider that serves the endpoint elsewhere (OpenRouter
serves it only under `/api/alpha/decisions`). `--base-url` and `--model`
have no default: argparse `required=True` would ignore `--config`
defaults, so `resolve_endpoint` exits naming the missing flag instead. The
key comes from `--api-key`, else `os.environ["LLM_API_KEY"]`, else
`LLM_API_KEY` in `.env` through [`load_env_var`](../src/laglitsynth/dotenv.py);
with none, it exits naming `LLM_API_KEY`.

`chat_client(endpoint, timeout)` returns an `openai.OpenAI`. Stage 8 calls
`chat.completions.create` with `model`, `messages` and
`response_format={"type": "json_object"}` only. `decisions_client(endpoint)`
returns an `httpx.Client` with the bearer header, and `decide(client,
endpoint, *, state, key, question) -> Decision` POSTs to
`endpoint.decisions_url`. `Decision` is `{probability: float | None,
reason: str | None, raw_response: str | None}`. The request body keeps the
shape PR #27 shipped: `{model, state, questions: {<key>: {type: "noul",
instructions, criteria: {true, false}}}}`. The probability is
`answers.<key>.noul` in [0, 1]. Screening uses key `relevant`, eligibility
`eligible`.

Errors fall in three classes. Transient errors give `llm-timeout` after
retries: on decisions, transport errors, 5xx and 429 (retried as in
`classify_decision`); on chat, `APITimeoutError`, `APIConnectionError`,
`RateLimitError` and `InternalServerError` after the SDK's retries.
Errors that fail every work the same way exit naming the URL: decisions
401, 402, 403 and 404; chat `AuthenticationError`,
`PermissionDeniedError`, `NotFoundError` and a 402 `APIStatusError`.
Any other decisions 4xx, and chat `BadRequestError`, give a new
`llm-rejected` sentinel with the error body. This is where an over-long
full text lands now that nothing truncates it. An unparseable 2xx gives
`llm-parse-failure` with the body attached.

[`LlmMeta`](../src/laglitsynth/models.py) becomes `{model, base_url,
decisions_url: str | None, prompt_sha256}`, with `decisions_url` `None`
for stage 8. [`save_resolved_config`](../src/laglitsynth/config.py) adds
`api_key` to `_DROP_KEYS`. Today a key passed on the command line ends up
in the run's `config.yaml`.

### Verdicts and records

`ScreeningVerdict` becomes `{work_id, relevance_score: int | None, reason:
str | None, raw_response}`, with `relevance_score = round(100 * p)` and
`reason` set only for sentinels. The screening-criteria YAML keeps only its
`decision` block; `system_prompt` goes.

`EligibilityVerdict` becomes `{work_id, probability: float | None,
eligible: bool | None, reason: str | None, raw_response}`, with `eligible =
probability >= 0.5` and `reason` set only for sentinels. Any other cut is
made ad hoc on `verdicts.jsonl`, the same way as for screening. The state
sent is the rendered full text
([`render_fulltext`](../src/laglitsynth/fulltext_eligibility/prompts.py)).
The eligibility-criteria YAML becomes a `decision` block covering criteria
1 and 2 only (ocean particle/tracer/object tracking; primary research).
`_EligibilityPayload` and
[`prompts.load_system_prompt`](../src/laglitsynth/prompts.py) go.

`ExtractionRecord` keeps `{work_id, reason, raw_response}` plus the
codebook payload. The example codebook
([lagrangian-oceanography.yaml](../examples/codebooks/lagrangian-oceanography.yaml))
gains a `rationale` field (`context: true`) for the stated reasons behind
numerical choices (RQ1.3). It needs no code change: fields are driven by
the YAML.

The prompt hash for a decisions stage is
`sha256(question.canonical_json() + "\n" + USER_TEMPLATE)`.
`criteria_sha256(spec)` in
[`fulltext_eligibility/eligibility.py`](../src/laglitsynth/fulltext_eligibility/eligibility.py)
is the one implementation, and stage 8 imports it.

### Corpus: `src/laglitsynth/corpus/`

`laglitsynth corpus-assemble [--catalogue] [--screening-verdicts]
[--screening-run-id] --screening-threshold FLOAT [--allow-unscreened]
[--data-dir]` resolves the catalogue and the verdicts with the existing
[`resolve_screening_inputs`](../src/laglitsynth/layout.py); the screening
run-id defaults to the manifest's. The command selects every catalogue
work whose verdict scores `None` or `>= threshold`. The threshold is
required: it is a review decision, and the runner passes it from the review
config. The command exits, writing nothing, when any verdict row fails
validation (`JsonlReadStats.skipped > 0`), when `screening-meta.json` beside
the verdicts is missing or does not validate, or when catalogue works have
no verdict row. `--allow-unscreened` turns the last case into inclusion,
which is how an unscreened or partly screened catalogue is assembled.

The command removes any leftover `<data-dir>/corpus.tmp/`, writes into a
fresh one, and then replaces `<data-dir>/corpus/` with it, so the corpus is
always rewritten whole:

- `works.jsonl`: the selected `Work` records.
- `verdicts.jsonl`: their `ScreeningVerdict`s.
- `pdfs/<stem>.pdf`: copies from the store, for works whose store record is
  not `missing`. A record whose PDF file is absent makes the command exit
  naming the file. A file without the `%PDF` magic (an unfetched Git LFS
  pointer) makes it exit and say to run `git lfs pull` in `data/`. The magic
  check moves from `retrieve._validate_pdf` to `store.py`.
- `provenance.jsonl`: one `CorpusPdf {work_id, pdf_path: str | None,
  content_sha256: str | None, store_record: PdfProvenanceRecord | None}` per
  selected work, works without a PDF included. `content_sha256` is computed
  from the copy.
- `corpus-meta.json`: `CorpusMeta {run, corpus_id, analysis_run_id,
  catalogue, screening_verdicts, screening_run_id: str | None,
  screening_llm: LlmMeta, screening_threshold, catalogue_count,
  selected_count, unscored_count, below_threshold_count, no_verdict_count,
  with_pdf_count}`.

[`corpus/load.py`](../src/laglitsynth/corpus/) holds the readers stages 6–8
share: `load_corpus_meta`, `read_corpus_works`, `read_corpus_pdfs`, and
`require_corpus(recorded, current, source)`, which exits naming the stale
file and the stage to re-run.

### Corpus identity and run-ids

`corpus_id` is the sha256 of the lines `f"{work_id}\t{content_sha256 or
''}"`, sorted by work id and joined with `\n`. Two assemblies with the same
members and the same PDF bytes therefore share an id. The id ignores
catalogue order, the threshold and the time of assembly; a change in
membership or in any PDF's bytes yields a new id. Analysis outputs record it
and check it:

- Stage 6: `ExtractionMeta.corpus_id`, and
  `ExtractedDocument.pdf_sha256` per record. `--skip-existing` skips a work
  only when its latest record's `pdf_sha256` matches the corpus copy.
- Stage 7: refuses to run when `extraction-meta.json` names a different
  `corpus_id`. It uses an extraction only when its `pdf_sha256` matches the
  corpus. It records `EligibilityMeta.corpus_id`.
- Stage 8: refuses eligibility verdicts whose `eligibility-meta.json` (next
  to the verdicts file; missing means exit) names a different `corpus_id`,
  or a `prompt_sha256` different from `criteria_sha256` of its own
  `--eligibility-criteria`. It applies the extraction checks as stage 7
  does and records `corpus_id` and `eligibility_prompt_sha256`.
- On stages 7 and 8, `--skip-existing` also refuses when the stage's own
  meta has a different `corpus_id`, in addition to the existing prompt-hash
  guard.

The halves get separate run-ids.
[`data/manifest.json`](../src/laglitsynth/manifest.py) keeps the screening
run-id. Screening writes under it, and `corpus-assemble` and the export read
verdicts from it unless `--screening-run-id` or `--screening-verdicts` names
another run. `corpus-assemble` keeps the previous
`CorpusMeta.analysis_run_id` when the `corpus_id` is unchanged and mints a
fresh one (`generate_run_id`) otherwise. Stages 7 and 8 default `--run-id`
to it (`layout.resolve_analysis_run_id`): a changed corpus gets new analysis
directories and earlier output is never rewritten, while an identical
re-assembly lets `--skip-existing` resume a crashed run. `--run-id` still
names a variant run on the same corpus, for example with other criteria. In
the runner, `RUN_ID` names the screening run (stage 3, `corpus-assemble`,
export) and `ANALYSIS_RUN_ID`, unset by default, is passed to stages 7 and 8.

### Existing screening output

The Jev screening run in `data/screening-abstracts/` predates these models:
its rows carry `seed` and its meta carries `llm.temperature`, `threshold`,
the above/below counts and `criterion`, and no `base_url`. The manifest
names the `--pass-all` sample run, not the Jev run. Before the first
analysis run the operator rewrites that run's `verdicts.jsonl` (drop
`seed`) and `screening-meta.json` (to the new `ScreeningMeta` and
`LlmMeta`) by hand, outside the CLI, and passes its run-id as
`--screening-run-id`. The scores are unchanged by the rewrite; re-screening
is the paid alternative. Implementers do not write `data/`, and the code
carries no reader for the old shape; `corpus-assemble` exits on it.

### Stage inputs after the rewire

- `fulltext-extraction [--corpus-dir data/corpus] [--output-dir] [--data-dir]
  [--grobid-url] [--timeout] [--skip-existing] [--consolidate-citations]`.
  It iterates `CorpusPdf` rows with a `pdf_path`. `--pdf-dir` and
  `ExtractionMeta.invalid_stem_count` go.
- `fulltext-eligibility [--corpus-dir] [--extractions] [--extraction-output-dir]
  [--eligibility-criteria] [--run-id]` plus the endpoint flags,
  `--max-records`, `--skip-existing`, `--dry-run` and `--config`.
  `--catalogue`, `--screening-verdicts` and `--screening-threshold` go.
  `EligibilityMeta` drops `input_catalogue` and `input_screening_verdicts`.
- `extraction-codebook [--corpus-dir] [--eligibility-verdicts]
  [--eligibility-criteria] [--extractions] [--codebook] [--run-id]` plus the
  same common flags. `--catalogue` goes: corpus works replace it in the
  verdict-consistency check. It passes `inlines=["codebook",
  "eligibility_criteria"]` to `save_resolved_config`.
  `ExtractionCodebookMeta` drops `input_catalogue` and `truncated_count`.
- `extraction-codebook-export --catalogue` defaults to
  `data/corpus/works.jsonl`.

### Review config and runner

`ReviewConfig` gains `llm_base_url: str | None`, `llm_decisions_url: str |
None` and `screening_threshold: float | None`. `models.*` stays and has no
defaults in the runner. The runner requires `LLM_BASE_URL` (env or config),
the three models, `SCREENING_THRESHOLD` (env or config) and `LLM_API_KEY`
(env or the sourced `.env`), and it exits naming whichever is missing.
`LLM_DECISIONS_URL` is passed as `--decisions-url` when set. The runner
never passes `--api-key`, so the key stays out of the process list; the CLI
reads it itself. The key never sits in a committed YAML; this is how the
mission's "key taken from the review config" is met. The
[example review](../examples/reviews/lagrangian-oceanography.yaml) leaves
base URL, decisions URL, models and threshold as commented placeholders and
names no provider. The run order is: 1 fetch → 2 dedup → 3 screening → 5
retrieval → `corpus-assemble` (gated with stage 5) → 6 → 7 → 8.

## Design decisions

The decisions URL is derived from `base_url` by default, because the mission
asks for one API per review and `/v1/decisions` is its stated path. The
override exists because the only provider used so far serves the endpoint
off that path; it changes the URL, not the key or the model namespace. A
wrong URL is found out on the first request, as a 404 exit that names it,
not as a run full of parse failures. The decisions client stays on `httpx`,
since the `openai` SDK has no decisions resource; the two clients share only
the endpoint.

`eligible` is cut at a fixed 0.5 instead of a new flag. The probability is
stored, so any other cut needs no re-run, and the pipeline gains no second
threshold knob.

`corpus_id` hashes content, not the assembly event. An identical
re-assembly then leaves stage 6 output valid and keeps the analysis run-id,
so stages 7 and 8 resume. A changed corpus gets a new analysis run-id, so a
re-assembly never overwrites stage 7 or 8 output in place.

The mission includes works without a score; a work with no verdict row at
all is a different case, usually a screening run that died or a
`--max-records` smoke run. `corpus-assemble` therefore exits on it unless
`--allow-unscreened` is passed, which includes such works and replaces
`--pass-all` as the way to assemble an unscreened catalogue.

## Implementation sequence

`pixi run typecheck` and `pixi run test` pass after every commit. Each
deletion commit also removes or redirects every link to the docs it deletes
(`grep -rn` across `README.md`, `docs/`, `plans/*.md`, and help strings in
`src/`), and leaves `plans/done/` and `docs/explorations/` alone. The
broader doc rewrite is commit 7. Slices: s1 is commits 1–2, s2 commits 3–4,
s3 commit 5, s4 commit 6, s5 commit 7 and s6 commit 8.

1. Drop abstract lookup, adjudication and the screening/eligibility XLSX
   exports. Delete [`abstract_lookup/`](../src/laglitsynth/abstract_lookup/),
   the two export modules, their builders in [`cli.py`](../src/laglitsynth/cli.py),
   [docs/abstract-lookup.md](../docs/abstract-lookup.md),
   [docs/adjudication-extraction.md](../docs/adjudication-extraction.md),
   [multi-run-consensus.md](multi-run-consensus.md), and the two
   `.env.example` keys. Drop `reviewer_correction` and its placeholder from
   [`extraction_codebook/export.py`](../src/laglitsynth/extraction_codebook/export.py).
   Tests: delete `tests/test_abstract_lookup.py`,
   `tests/test_screening_abstracts_export.py` and
   `tests/test_fulltext_eligibility_export.py`; in
   `tests/test_extraction_codebook_export.py`, assert that the per-work tab
   header is `field | value | context`.

2. Drop `--pass-all`, the thresholds outside the corpus, and the
   Ollama-specific code. Remove `pass_all_works`, `_unscreened_verdict`,
   `--pass-all` and `--screening-threshold` from
   [`screen.py`](../src/laglitsynth/screening_abstracts/screen.py), drop the
   three `ScreeningMeta` threshold fields, and make `ScreeningMeta.llm`
   required. Retrieval runs over the whole catalogue, and `_active_works`
   moves into `export.py`. Delete `ollama.py`, `bake_model.py` and
   docs/bake-model.md. Remove every preflight call, `--num-ctx`, the
   `extra_body` hint, `CHAR_BUDGET` with `_truncate_blocks`, and the
   `truncated` record field with its proto attribute, `truncated_count` and
   the `truncated` row of the codebook export. In
   [`render_fulltext`](../src/laglitsynth/extraction_codebook/prompts.py),
   return the full text. `ReviewConfig.thresholds` becomes
   `screening_threshold`, emitted as `CFG_SCREENING_THRESHOLD`; the runner
   requires `SCREENING_THRESHOLD` and passes it to stage 7's
   `--screening-threshold` until commit 6. Update `emit_cfg_lines`,
   [run-pipeline.sh](../scripts/run-pipeline.sh) and the sbatch as listed
   under Deleted, and drop the threshold text from
   [screening-abstracts.md](../docs/screening-abstracts.md). Tests: delete
   `tests/test_bake_model.py` and `tests/test_preflight.py`; remove the
   pass-all and threshold-split tests from `tests/test_screening_abstracts.py`,
   the truncation tests from `tests/test_extraction_codebook_prompts.py`,
   the `truncated` row from `tests/test_extraction_codebook_export.py`, and
   the `num_ctx` and `extra_body` assertions and every `patch(...preflight)`
   from `tests/test_fulltext_eligibility.py`,
   `tests/test_extraction_codebook.py`, `tests/test_screening_abstracts.py`
   and `tests/test_config.py`. In `tests/test_fulltext_retrieval.py` the
   threshold tests become `test_retrieval_covers_whole_catalogue` (no
   verdicts file); `tests/test_fulltext_retrieval_export.py` keeps its join
   tests against the moved helper; `tests/test_review.py` drops the removed
   `CFG_*` names and asserts `CFG_SCREENING_THRESHOLD`.

3. One OpenAI-compatible endpoint. Add `llm.py` as specified, moving
   `classify_decision` in as `decide`, and delete
   `screening_abstracts/decisions.py`. Screening becomes decisions-only
   (`--backend`, `classify_abstract`, `screen_works` and
   `ScreeningMeta.criterion` go), and the screening YAML loses
   `system_prompt`. Stages 7 and 8 build their client with `chat_client`,
   without temperature or seed, and map chat errors as specified; stage 7
   stays on chat until commit 4. Drop `seed` from all three verdict/record
   models, change `LlmMeta`, add `api_key` to `_DROP_KEYS`, and add
   `llm-rejected` with its meta counts. The codebook export drops its `seed`
   and `llm_temperature` rows and gains `llm_base_url`. `ReviewConfig` gains
   `llm_base_url` and `llm_decisions_url`; the runner gets `LLM_BASE_URL`,
   `LLM_DECISIONS_URL` and the `LLM_API_KEY` check, and stops passing
   `--api-key`. `.env.example` gets `LLM_API_KEY` in place of
   `OPENROUTER_API_KEY`. Tests: `tests/test_screening_decisions.py` becomes
   `tests/test_llm.py` (`httpx.MockTransport`), covering the default URL
   `base_url + "/decisions"` and the body shape, the `--decisions-url`
   override, exits on 401, 402 and 404 naming the URL, retry then
   `llm-timeout` on 5xx and 429, `llm-rejected` on 400, `llm-parse-failure`
   on a malformed body, `resolve_endpoint` exits naming `--base-url`,
   `--model` or `LLM_API_KEY`, and the key read from the environment before
   `.env`. `tests/test_extraction_codebook.py`: `create` receives no
   `temperature` or `seed`, `BadRequestError` gives `llm-rejected`,
   `RateLimitError` gives `llm-timeout`, and `AuthenticationError` exits.
   `tests/test_extraction_codebook_export.py` drops the `seed` and
   `llm_temperature` rows; `tests/test_config.py` asserts `api_key` is absent
   from the snapshot; `tests/test_screening_abstracts.py` is trimmed to the
   decisions path; `tests/test_review.py` asserts `CFG_LLM_BASE_URL` and
   `CFG_LLM_DECISIONS_URL`.

4. Eligibility on the decisions endpoint. Stage 7 calls `decide` with the
   rendered full text as `state` and key `eligible`. Rewrite the
   eligibility criteria YAML as a `decision` block without the methods
   criterion. Change `EligibilityVerdict` as specified, add
   `criteria_sha256`, and delete `_EligibilityPayload` and
   `load_system_prompt`. Tests: `tests/test_fulltext_eligibility.py` is
   rewritten for decisions, with `test_probability_cut_at_half` (0.49, 0.5
   and 1.0), `probability=None` on each of the `tei-parse-failure`,
   `llm-timeout`, `llm-parse-failure` and `llm-rejected` sentinels, and the
   prompt hash equal to `criteria_sha256`.
   `tests/test_fulltext_eligibility_prompts.py` loads the example criteria
   as a `DecisionQuestion`, and its `load_system_prompt` tests go.

5. `corpus-assemble`. Add the `corpus/` package (`models.py`,
   `assemble.py`, `load.py`), the CLI builder, `layout.corpus_dir`, and the
   PDF magic check in `store.py`. Tests: new `tests/test_corpus_assemble.py`
   with `test_selection` (above the threshold in, below out, a `None` score
   in), `test_missing_verdict_exits`, `test_allow_unscreened_includes`,
   `test_invalid_verdict_row_exits`, `test_invalid_screening_meta_exits`,
   `test_pdf_copies_match_store`, `test_work_without_pdf_has_null_path`,
   `test_absent_store_file_exits`, `test_lfs_pointer_exits`,
   `test_reassembly_removes_stale_files`, `test_leftover_tmp_removed`,
   `test_corpus_id_stable_across_catalogue_order`,
   `test_corpus_id_changes_with_pdf_bytes`, and
   `test_analysis_run_id_kept_when_corpus_unchanged` with its converse.

6. Stages 6–8 read the corpus. Rewire the three stages as specified under
   Stage inputs after the rewire and Corpus identity and run-ids. Add
   `layout.resolve_analysis_run_id`. Delete the stage-7 `_active_works` and
   stage 8's catalogue read. Add `rationale` to the example codebook. The
   runner runs `corpus-assemble` with `SCREENING_THRESHOLD`, stops passing a
   threshold to stage 7, passes `ANALYSIS_RUN_ID` to stages 7 and 8, and
   passes `--eligibility-criteria` to stage 8. Tests: `conftest.py` gains a
   `_write_corpus` helper. `tests/test_fulltext_extraction.py`: only corpus
   PDFs are sent, a changed corpus PDF is re-extracted under
   `--skip-existing`, and the meta carries `corpus_id`.
   `tests/test_fulltext_eligibility.py`: works come from
   `corpus/works.jsonl`, an extraction-meta corpus mismatch exits, a stale
   `pdf_sha256` extraction is ignored, and `--run-id` defaults to
   `CorpusMeta.analysis_run_id`. `tests/test_extraction_codebook.py`: exits
   on an eligibility-meta corpus mismatch, on a criteria-hash mismatch and on
   a missing eligibility meta, and the config snapshot inlines the
   eligibility criteria. `tests/test_extraction_codebook_loader.py`: the
   example codebook yields `rationale` and `rationale_context`.

7. Docs and roadmap, in present tense, describing only what stays true.
   [pipeline.md](../docs/pipeline.md) shows the two halves meeting at the
   corpus and names the one-branch-per-paper principle; stage 2b, stage 9,
   the feedback loops, citation-network and author review go; stages 10–12
   become one planned `synthesis`; the flowchart and human spot-checking
   are redrawn. [interfaces.md](../docs/interfaces.md) gets the artifact
   map, CLI contract, model tables, `LlmMeta`, prompt-hash table and
   `--concurrency`, and loses the resolve-module gap.
   [data-model.md](../docs/data-model.md) follows the new models.
   [glossary.md](../docs/glossary.md) redefines *corpus* and drops
   adjudication, included catalogue, eligible corpus, source basis and the
   PRISMA framing, each checked against the code.
   [vocabulary.md](../docs/vocabulary.md) loses the adjudication
   dependence. [codebook.md](../docs/codebook.md) gains the `rationale`
   field, reversing the "replaces rationale sections" passages. Rewrite the
   stage docs ([screening-abstracts.md](../docs/screening-abstracts.md),
   [eligibility.md](../docs/eligibility.md),
   [extraction-codebook.md](../docs/extraction-codebook.md),
   [fulltext-extraction.md](../docs/fulltext-extraction.md),
   [fulltext-retrieval.md](../docs/fulltext-retrieval.md)) and the setup
   and run docs ([configs.md](../docs/configs.md),
   [run-manifest.md](../docs/run-manifest.md),
   [cross-machine.md](../docs/cross-machine.md), and
   [two-pass-extraction.md](../docs/two-pass-extraction.md) for
   `CHAR_BUDGET`). In [external-services.md](../docs/external-services.md)
   the Ollama sections become an endpoint section that names no provider.
   Delete [llm-concurrency.md](../docs/llm-concurrency.md) and point the
   `--concurrency` help strings at interfaces.md. Update
   [README.md](../README.md) and add [docs/corpus.md](../docs/corpus.md).
   In the [roadmap](roadmap.md), multi-run consensus, stages 9–12, the
   shared export helper and the open abstract-lookup question become
   Withdrawn entries or the synthesis entry, retrieval over every OpenAlex
   location plus Semantic Scholar and arXiv gets a Queued entry, and the
   Ollama parts of [usability-docs.md](usability-docs.md) are marked
   obsolete. Tests: none new.

8. Export/import for the collaborator procedure. In
   [`export.py`](../src/laglitsynth/fulltext_retrieval/export.py) the
   screening filter becomes optional: with no `--screening-threshold` the
   bundle lists every catalogue work without a held PDF, and the
   `ExportMeta` screening fields become `| None`. The RIS record gets
   `AN  - <stem>`, `UR  - https://doi.org/<doi>` (falling back to
   `primary_location.landing_page_url`) and `T2` in place of `JO`.
   [`import_.py`](../src/laglitsynth/fulltext_retrieval/import_.py) matches
   DOIs and stems against the whole `--catalogue` (default: the
   deduplicated catalogue) instead of `--pdf-manifest`, which goes.
   `--source` takes `zotero-desktop | browser | institutional | manual`,
   and `PdfSource.zotero_import` goes; the store holds no such record.
   [pdf-store.md](../docs/pdf-store.md),
   [fulltext-retrieval.md](../docs/fulltext-retrieval.md) and
   interfaces.md drop the "does not support this procedure yet" text. This
   plan and the mission statement move to `plans/done/` and to Implemented
   in the roadmap, and links to them (pdf-store.md among them) are
   redirected. Tests: `tests/test_fulltext_retrieval_export.py` covers the
   AN, UR (DOI and landing page) and T2 lines and the unfiltered gap;
   `tests/test_fulltext_retrieval_import.py` covers a PDF for a work in no
   export matched by its embedded DOI, a bare `<stem>.pdf` landing, each new
   `--source` recorded, and `zotero-import` rejected by argparse.

## Follow-ups

- Retrieval over every OpenAlex location plus Semantic Scholar and arXiv
  (mission step 2): the store already holds these PDFs, so it does not
  block the analysis run.
- Per-work reuse of stage 7 and 8 output across re-assemblies that change
  the corpus (verdicts keyed by PDF sha): wait until a re-assembly makes the
  full re-run cost matter.
- Synthesis design, including whether RQ1.3 rationale grouping keeps human
  labelling: decided when synthesis is planned.
- Refining [vocabulary.md](../docs/vocabulary.md): waits for the first
  codebook results.

## Risks

- Full texts exceed the provider's context. Nothing truncates them any
  more. Such works get `llm-rejected` with the error body, and the meta
  count shows whether two-pass extraction is needed.
- The store checkout holds LFS pointers instead of PDFs.
  `corpus-assemble` checks the magic bytes and exits before writing a
  corpus.
- Matching against the whole catalogue mis-files a PDF whose first page
  cites another catalogue work. The existing rule (page text counts only
  when it names exactly one work) still applies, and metadata DOIs win over
  page text.

## Critical files

- [plans/pipeline-restructure.md](pipeline-restructure.md),
  [plans/roadmap.md](roadmap.md)
- [src/laglitsynth/cli.py](../src/laglitsynth/cli.py),
  [layout.py](../src/laglitsynth/layout.py),
  [manifest.py](../src/laglitsynth/manifest.py),
  [models.py](../src/laglitsynth/models.py),
  [config.py](../src/laglitsynth/config.py),
  [review.py](../src/laglitsynth/review.py),
  [prompts.py](../src/laglitsynth/prompts.py),
  [dotenv.py](../src/laglitsynth/dotenv.py),
  [io.py](../src/laglitsynth/io.py),
  [ollama.py](../src/laglitsynth/ollama.py),
  [bake_model.py](../src/laglitsynth/bake_model.py)
- [screening_abstracts/screen.py](../src/laglitsynth/screening_abstracts/screen.py),
  [decisions.py](../src/laglitsynth/screening_abstracts/decisions.py),
  [models.py](../src/laglitsynth/screening_abstracts/models.py)
- [fulltext_retrieval/retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py),
  [export.py](../src/laglitsynth/fulltext_retrieval/export.py),
  [import_.py](../src/laglitsynth/fulltext_retrieval/import_.py),
  [store.py](../src/laglitsynth/fulltext_retrieval/store.py),
  [models.py](../src/laglitsynth/fulltext_retrieval/models.py)
- [fulltext_extraction/extract.py](../src/laglitsynth/fulltext_extraction/extract.py),
  [models.py](../src/laglitsynth/fulltext_extraction/models.py)
- [fulltext_eligibility/eligibility.py](../src/laglitsynth/fulltext_eligibility/eligibility.py),
  [models.py](../src/laglitsynth/fulltext_eligibility/models.py),
  [prompts.py](../src/laglitsynth/fulltext_eligibility/prompts.py)
- [extraction_codebook/extract.py](../src/laglitsynth/extraction_codebook/extract.py),
  [codebook.py](../src/laglitsynth/extraction_codebook/codebook.py),
  [prompts.py](../src/laglitsynth/extraction_codebook/prompts.py),
  [export.py](../src/laglitsynth/extraction_codebook/export.py)
- [examples/](../examples/),
  [scripts/run-pipeline.sh](../scripts/run-pipeline.sh),
  [scripts/nesh-pipeline.sbatch](../scripts/nesh-pipeline.sbatch),
  [.env.example](../.env.example)
- [docs/pipeline.md](../docs/pipeline.md),
  [docs/interfaces.md](../docs/interfaces.md),
  [docs/data-model.md](../docs/data-model.md),
  [docs/pdf-store.md](../docs/pdf-store.md),
  [docs/glossary.md](../docs/glossary.md)
