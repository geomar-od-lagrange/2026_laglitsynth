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
  `seed`, the `seed` field on every verdict and record, `--backend`,
  `--decisions-url`, and the chat path of screening. In
  [nesh-pipeline.sbatch](../scripts/nesh-pipeline.sbatch) the Ollama install
  check, `ollama serve`, its wait loop, the pulls, the Modelfile tags and the
  GPU request go; GROBID stays.
- [`ReviewConfig`](../src/laglitsynth/review.py) loses `backends`, `num_ctx`
  and `thresholds`. The runner loses `RETRIEVAL_THRESHOLD`,
  `ELIGIBILITY_THRESHOLD`, `OLLAMA_BASE`, `SCREENING_BACKEND` and
  `*_NUM_CTX`.

### LLM backend: `src/laglitsynth/llm.py`

One module replaces `ollama.py` and
[`screening_abstracts/decisions.py`](../src/laglitsynth/screening_abstracts/decisions.py).
The three LLM stages register the same flags with `add_endpoint_args(parser)`:
`--base-url`, `--model`, `--api-key` and `--concurrency`. `resolve_endpoint(args)`
returns a frozen `LlmEndpoint {base_url, model, api_key}`. `--base-url` and
`--model` have no default: argparse `required=True` would ignore
`--config` defaults, so `resolve_endpoint` exits naming the missing flag
instead. The key comes from `--api-key`, or else from `LLM_API_KEY` in `.env`
through [`load_env_var`](../src/laglitsynth/dotenv.py).
`base_url` is the OpenAI-compatible base including `/v1`, exactly what the
`openai` SDK takes.

- `chat_client(endpoint, timeout)` returns an `openai.OpenAI`. Stage 8 calls
  `chat.completions.create` with `model`, `messages` and
  `response_format={"type": "json_object"}` only.
- `decisions_client(endpoint)` returns an `httpx.Client` with the bearer
  header. `decide(client, endpoint, *, state, key, question) -> Decision`
  POSTs to `decisions_url(base_url) = base_url.rstrip("/") + "/decisions"`.
  `Decision` is `{probability: float | None, reason: str | None,
  raw_response: str | None}`.
- The request body keeps the shape PR #27 shipped: `{model, state,
  questions: {<key>: {type: "noul", instructions, criteria: {true,
  false}}}}`. The probability is `answers.<key>.noul` in [0, 1]. Screening
  uses key `relevant`, eligibility `eligible`.
- Error handling stays as in `classify_decision`: transport errors, 5xx and
  429 are retried, then give `llm-timeout`, and an unparseable 2xx gives
  `llm-parse-failure` with the body attached. 401, 403 and 404 exit naming
  the URL, because they fail every work the same way and a 404 means the
  provider does not serve the path. Any other 4xx, and `openai.BadRequestError`
  on the chat side, gives a new `llm-rejected` sentinel with the error body.
  This is where an over-long full text lands now that nothing truncates it.
- [`LlmMeta`](../src/laglitsynth/models.py) becomes `{model, base_url,
  prompt_sha256}`. [`save_resolved_config`](../src/laglitsynth/config.py)
  adds `api_key` to `_DROP_KEYS`. Today a key passed on the command line
  ends up in the run's `config.yaml`.

### Verdicts and records

- `ScreeningVerdict` becomes `{work_id, relevance_score: int | None, reason:
  str | None, raw_response}`, with `relevance_score = round(100 * p)` and
  `reason` set only for sentinels. The screening-criteria YAML keeps only its
  `decision` block; `system_prompt` goes.
- `EligibilityVerdict` becomes `{work_id, probability: float | None,
  eligible: bool | None, reason: str | None, raw_response}`, with `eligible =
  probability >= 0.5` and `reason` set only for sentinels. Any other cut is
  made ad hoc on `verdicts.jsonl`, the same way as for screening. The state
  sent is the rendered full text
  ([`render_fulltext`](../src/laglitsynth/fulltext_eligibility/prompts.py)).
  The eligibility-criteria YAML becomes a `decision` block covering
  criteria 1 and 2 only (ocean particle/tracer/object tracking; primary
  research). `_EligibilityPayload` and
  [`prompts.load_system_prompt`](../src/laglitsynth/prompts.py) go.
- `ExtractionRecord` keeps `{work_id, reason, raw_response}` plus the
  codebook payload. The example codebook
  ([lagrangian-oceanography.yaml](../examples/codebooks/lagrangian-oceanography.yaml))
  gains a `rationale` field (`context: true`) for the stated reasons behind
  numerical choices (RQ1.3). It needs no code change: fields are driven by
  the YAML.
- The prompt hash for a decisions stage is
  `sha256(question.canonical_json() + "\n" + USER_TEMPLATE)`.
  `criteria_sha256(spec)` in
  [`fulltext_eligibility/eligibility.py`](../src/laglitsynth/fulltext_eligibility/eligibility.py)
  is the one implementation, and stage 8 imports it.

### Corpus: `src/laglitsynth/corpus/`

`laglitsynth corpus-assemble [--catalogue] [--screening-verdicts] [--run-id]
--screening-threshold FLOAT [--data-dir]` resolves the catalogue and the
verdicts with the existing
[`resolve_screening_inputs`](../src/laglitsynth/layout.py). The run-id comes
from the manifest. The command selects every catalogue work whose verdict
scores `None` or `>= threshold`. Works without a verdict are left out,
counted and warned about. The threshold is required: it is a review
decision, and the runner passes it from the review config.

The command writes into `<data-dir>/corpus.tmp/` and then replaces
`<data-dir>/corpus/` with it, so the corpus is always rewritten whole:

- `works.jsonl`: the selected `Work` records.
- `verdicts.jsonl`: their `ScreeningVerdict`s.
- `pdfs/<stem>.pdf`: copies from the store, for works whose store record is
  not `missing`. A file without the `%PDF` magic (an unfetched Git LFS
  pointer) makes the command exit and say to run `git lfs pull` in `data/`.
  The magic check moves from `retrieve._validate_pdf` to `store.py`.
- `provenance.jsonl`: one `CorpusPdf {work_id, pdf_path: str | None,
  content_sha256: str | None, store_record: PdfProvenanceRecord | None}` per
  selected work, works without a PDF included. `content_sha256` is computed
  from the copy.
- `corpus-meta.json`: `CorpusMeta {run, corpus_id, run_id, catalogue,
  screening_verdicts, screening_run_id: str | None, screening_llm: LlmMeta |
  None (from screening-meta.json beside the verdicts), screening_threshold,
  catalogue_count, selected_count, unscored_count, below_threshold_count,
  no_verdict_count, with_pdf_count}`.

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
[`data/manifest.json`](../src/laglitsynth/manifest.py) keeps the run-id of
the first half. Screening writes under it, and `corpus-assemble` and the
export read the screening verdicts from it. `corpus-assemble` mints a fresh
`CorpusMeta.run_id` (`generate_run_id`) on every assembly. Stages 7 and 8
default `--run-id` to that id (`layout.resolve_analysis_run_id`), so each
assembly gets new analysis directories and earlier analysis output is never
rewritten. `--run-id` still names a variant run on the same corpus, for
example with other criteria.

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
  verdict-consistency check. `ExtractionCodebookMeta` drops
  `input_catalogue` and `truncated_count`.
- `extraction-codebook-export --catalogue` defaults to
  `data/corpus/works.jsonl`.

### Review config and runner

`ReviewConfig` gains `llm_base_url: str | None` and `screening_threshold:
float | None`. `models.*` stays and has no defaults in the runner. The
runner requires `LLM_BASE_URL` (env or config), the three models and
`LLM_API_KEY` in `.env`, and it exits naming whichever is missing. The key
never sits in a committed YAML; this is how the mission's "key taken from
the review config" is met. The [example review](../examples/reviews/lagrangian-oceanography.yaml)
leaves base URL and models as commented placeholders and names no provider.
The run order is: 1 fetch → 2 dedup → 3 screening → 5 retrieval →
`corpus-assemble` (gated with stage 5) → 6 → 7 → 8.

## Design decisions

The decisions URL is derived from `base_url` rather than set by its own
flag, because the mission asks for one API per review. Whether a provider
serves `/v1/decisions` is found out on the first request, as a 404 exit
that names the URL; it does not turn into a run full of parse failures.
The decisions client stays on `httpx`, since the `openai` SDK has no
decisions resource; the two clients share only the endpoint.

`eligible` is cut at a fixed 0.5 instead of a new flag. The probability is
stored, so any other cut needs no re-run, and the pipeline gains no second
threshold knob.

`corpus_id` hashes content, not the assembly event. An identical
re-assembly then leaves stage 6 output valid. The analysis run-id
deliberately tracks the event instead, so a re-assembly never overwrites
stage 7 or 8 output in place.

Works without a screening verdict are excluded from the corpus with a
count instead of failing the command. `--max-records` smoke runs leave such
partial verdict files, and the count in `corpus-meta.json` makes the gap
visible.

## Implementation sequence

`pixi run typecheck` and `pixi run test` pass after every commit. Each
deletion commit also removes or redirects every link to the docs it deletes
(`grep -rn` across `README.md`, `docs/`, `plans/*.md`), and leaves
`plans/done/` and `docs/explorations/` alone. The broader doc rewrite is
commit 7.

1. Drop abstract lookup, adjudication and the screening/eligibility XLSX
   exports. Delete
   [`abstract_lookup/`](../src/laglitsynth/abstract_lookup/), the two export
   modules, their builders in [`cli.py`](../src/laglitsynth/cli.py),
   [docs/abstract-lookup.md](../docs/abstract-lookup.md),
   [docs/adjudication-extraction.md](../docs/adjudication-extraction.md),
   [multi-run-consensus.md](multi-run-consensus.md), and the two
   `.env.example` keys. Drop `reviewer_correction` and its placeholder from
   [`extraction_codebook/export.py`](../src/laglitsynth/extraction_codebook/export.py).
   Tests: delete `tests/test_abstract_lookup.py`,
   `tests/test_screening_abstracts_export.py` and
   `tests/test_fulltext_eligibility_export.py`. In
   `tests/test_extraction_codebook_export.py`, assert that the per-work tab
   header is `field | value | context`.

2. Drop `--pass-all`, the retrieval threshold and the Ollama-specific
   code. Remove `pass_all_works`, `_unscreened_verdict` and the flag from
   [`screen.py`](../src/laglitsynth/screening_abstracts/screen.py), and make
   `ScreeningMeta.llm` required. Retrieval runs over the whole catalogue, and
   `_active_works` moves into `export.py`. Delete `ollama.py`,
   `bake_model.py` and docs/bake-model.md. Remove every preflight call,
   `--num-ctx`, the `extra_body` hint, `CHAR_BUDGET` with `_truncate_blocks`,
   and the `truncated` record field with its proto attribute and
   `truncated_count`. In [`render_fulltext`](../src/laglitsynth/extraction_codebook/prompts.py),
   return the full text. Update `ReviewConfig`, `emit_cfg_lines`,
   [run-pipeline.sh](../scripts/run-pipeline.sh) and the sbatch as listed
   under Deleted. Tests: delete `tests/test_bake_model.py` and
   `tests/test_preflight.py`, plus the pass-all tests in
   `tests/test_screening_abstracts.py`, the truncation tests in
   `tests/test_extraction_codebook_prompts.py`, and the `num_ctx` and
   `extra_body` assertions in `tests/test_fulltext_eligibility.py` and
   `tests/test_extraction_codebook.py`. In
   `tests/test_fulltext_retrieval.py`, replace the threshold tests with
   `test_retrieval_covers_whole_catalogue` (no verdicts file needed).
   `tests/test_fulltext_retrieval_export.py` keeps its join tests against
   the moved helper, and `tests/test_review.py` drops the removed `CFG_*`
   names.

3. One OpenAI-compatible endpoint. Add `llm.py` as specified, moving
   `classify_decision` in as `decide`, and delete
   `screening_abstracts/decisions.py`. Screening becomes decisions-only
   (`--backend`, `--decisions-url`, `classify_abstract` and `screen_works`
   go), and the screening YAML loses `system_prompt`. Stages 7 and 8 build
   their client with `chat_client`, without temperature or seed. Stage 7
   stays on chat until commit 4. Drop `seed` from all three
   verdict/record models, change `LlmMeta`, add `api_key` to `_DROP_KEYS`,
   and add `llm-rejected` with its meta counts. `ReviewConfig` gains
   `llm_base_url`, and the runner gets `LLM_BASE_URL` and `LLM_API_KEY`.
   `.env.example` gets `LLM_API_KEY` in place of `OPENROUTER_API_KEY`.
   Tests: `tests/test_screening_decisions.py` becomes `tests/test_llm.py`
   (`httpx.MockTransport`) with these tests:
   - The URL is `base_url + "/decisions"` and the body has the documented
     shape.
   - 404 and 401 exit naming the URL; 5xx and 429 retry, then give
     `llm-timeout`; 400 gives `llm-rejected`; a malformed body gives
     `llm-parse-failure`.
   - `resolve_endpoint` exits naming `--base-url` or `--model`, and reads
     `LLM_API_KEY` from `.env`.

   Other tests:
   - `tests/test_extraction_codebook.py`: `create` receives no
     `temperature` or `seed`, and `BadRequestError` gives `llm-rejected`.
   - `tests/test_config.py`: `api_key` is absent from the snapshot.
   - `tests/test_screening_abstracts.py`: trimmed to the decisions path.
   - `tests/test_review.py`: emits `CFG_LLM_BASE_URL`.

4. Eligibility on the decisions endpoint. Stage 7 calls `decide` with
   the rendered full text as `state` and key `eligible`. Rewrite the
   eligibility criteria YAML as a `decision` block without the methods
   criterion. Change `EligibilityVerdict` as specified, add
   `criteria_sha256`, and delete `_EligibilityPayload` and
   `load_system_prompt`. Tests: `tests/test_fulltext_eligibility.py` is
   rewritten for decisions:
   - `test_probability_cut_at_half` covers 0.49, 0.5 and 1.0.
   - The `tei-parse-failure`, `llm-timeout`, `llm-parse-failure` and
     `llm-rejected` sentinels each carry `probability=None`.
   - The prompt hash equals `criteria_sha256`.

   `tests/test_fulltext_eligibility_prompts.py` loads the example criteria
   as a `DecisionQuestion`, and its `load_system_prompt` tests go.

5. `corpus-assemble`. Add the `corpus/` package (`models.py`,
   `assemble.py`, `load.py`), the CLI builder, `layout.corpus_dir`, and the
   PDF magic check in `store.py`. Tests: new `tests/test_corpus_assemble.py`
   with:
   - `test_selection`: above the threshold is in, below is out, a `None`
     score is in, and a work without a verdict is out and counted.
   - `test_pdf_copies_match_store`: bytes and sha are equal.
   - `test_work_without_pdf_has_null_path`.
   - `test_lfs_pointer_exits`.
   - `test_reassembly_removes_stale_files`.
   - `test_corpus_id_stable_across_catalogue_order`.
   - `test_corpus_id_changes_with_pdf_bytes`.
   - `test_run_id_fresh_per_assembly`.

6. Stages 6–8 read the corpus. Rewire the three stages as specified
   under Stage inputs after the rewire and Corpus identity and run-ids. Add
   `layout.resolve_analysis_run_id`. Delete the stage-7 `_active_works` and
   stage 8's catalogue read. Add `rationale` to the example codebook, and
   have the runner pass `--eligibility-criteria` to stage 8 and run
   `corpus-assemble`. Tests:
   - `tests/test_fulltext_extraction.py`: only corpus PDFs are sent, a
     changed corpus PDF is re-extracted under `--skip-existing`, and the
     meta carries `corpus_id`.
   - `tests/test_fulltext_eligibility.py`: works come from
     `corpus/works.jsonl`, an extraction-meta corpus mismatch exits, a
     stale `pdf_sha256` extraction is ignored, and `--run-id` defaults to
     `CorpusMeta.run_id`.
   - `tests/test_extraction_codebook.py`: exits on an eligibility-meta
     corpus mismatch, on a criteria-hash mismatch and on a missing
     eligibility meta.
   - `tests/test_extraction_codebook_loader.py`: the example codebook
     yields `rationale` and `rationale_context`.
   - `conftest.py` gains a `_write_corpus` helper.

7. Docs and roadmap. Rewrite these in present tense, describing only
   what stays true:
   - [pipeline.md](../docs/pipeline.md): the two halves meeting at the
     corpus; stage 2b, stage 9, the feedback loops, citation-network and
     author review removed; stages 10–12 as one planned `synthesis`;
     flowchart and human spot-checking redrawn.
   - [interfaces.md](../docs/interfaces.md): artifact map, CLI contract,
     model tables, `LlmMeta`, prompt-hash table; the resolve-module gap
     goes.
   - [glossary.md](../docs/glossary.md): *corpus* redefined; adjudication,
     included catalogue, eligible corpus, source basis and the PRISMA
     framing dropped, each checked against the code.
   - [vocabulary.md](../docs/vocabulary.md): the adjudication dependence
     goes.
   - [codebook.md](../docs/codebook.md): the `rationale` field, reversing
     the "replaces rationale sections" passages.
   - Stage docs: [screening-abstracts.md](../docs/screening-abstracts.md),
     [eligibility.md](../docs/eligibility.md),
     [extraction-codebook.md](../docs/extraction-codebook.md),
     [fulltext-extraction.md](../docs/fulltext-extraction.md),
     [fulltext-retrieval.md](../docs/fulltext-retrieval.md).
   - Setup and run docs: [configs.md](../docs/configs.md),
     [run-manifest.md](../docs/run-manifest.md),
     [cross-machine.md](../docs/cross-machine.md),
     [two-pass-extraction.md](../docs/two-pass-extraction.md) (the
     `CHAR_BUDGET` references).
   - [external-services.md](../docs/external-services.md): the Ollama
     sections are replaced by an endpoint section that names no provider.
   - [llm-concurrency.md](../docs/llm-concurrency.md): deleted, with
     `--concurrency` described in interfaces.md.
   - [README.md](../README.md).
   - New [docs/corpus.md](../docs/corpus.md).

   In the [roadmap](roadmap.md), this plan and the mission statement move
   to Implemented and `plans/done/`. Multi-run consensus, stages 9–12, the
   shared export helper and the open abstract-lookup question are replaced
   by Withdrawn entries or the synthesis entry, and the Ollama parts of
   [usability-docs.md](usability-docs.md) are marked obsolete. Tests: none
   new.

8. Export/import for the collaborator procedure.
   - [`export.py`](../src/laglitsynth/fulltext_retrieval/export.py): the
     screening filter becomes optional. With no `--screening-threshold` the
     bundle lists every catalogue work without a held PDF, and the
     `ExportMeta` screening fields become `| None`.
   - The RIS record gets `AN  - <stem>`, `UR  - https://doi.org/<doi>`
     (falling back to `primary_location.landing_page_url`) and `T2` in
     place of `JO`.
   - [`import_.py`](../src/laglitsynth/fulltext_retrieval/import_.py)
     matches DOIs and stems against the whole `--catalogue` (default: the
     deduplicated catalogue) instead of `--pdf-manifest`, which goes.
   - `--source` takes `zotero-desktop | browser | institutional | manual`,
     and `PdfSource.zotero_import` goes. The store holds no such record.
   - [pdf-store.md](../docs/pdf-store.md),
     [fulltext-retrieval.md](../docs/fulltext-retrieval.md) and
     interfaces.md drop the "does not support this procedure yet" text.

   Tests:
   - `tests/test_fulltext_retrieval_export.py`: AN, UR (DOI and landing
     page) and T2 lines, plus the unfiltered gap.
   - `tests/test_fulltext_retrieval_import.py`: a PDF for a work in no
     export is matched by its embedded DOI, a bare `<stem>.pdf` lands, each
     new `--source` is recorded, and `zotero-import` is rejected by
     argparse.

### Slices

- s1: commits 1–2
- s2: commits 3–4
- s3: commit 5
- s4: commit 6
- s5: commit 7
- s6: commit 8

## Follow-ups

- Retrieval over every OpenAlex location plus Semantic Scholar and arXiv
  (mission step 2): the store already holds these PDFs, so it does not
  block the analysis run.
- Per-work reuse of stage 7 and 8 output across re-assemblies (verdicts
  keyed by PDF sha): wait until a re-assembly makes the full re-run cost
  matter.
- Synthesis design, including whether RQ1.3 rationale grouping keeps human
  labelling: decided when synthesis is planned.
- Refining [vocabulary.md](../docs/vocabulary.md): waits for the first
  codebook results.

## Risks

- The provider serves the decisions endpoint only on the alpha path.
  The first request exits with a 404 naming the derived URL, before any
  verdict is written. The fix is then a provider choice, not a code path.
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
  [docs/pdf-store.md](../docs/pdf-store.md),
  [docs/glossary.md](../docs/glossary.md)
