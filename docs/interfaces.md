# Interface contracts

Cross-cutting view of the pipeline's artifacts, CLI commands, and Pydantic
models: the input/output chain from catalogue fetch through the
[corpus](corpus.md) to codebook extraction.

## Design principle: flag, don't filter

Gate stages (3, 7) write verdict sidecars but never copy or split Work
records. Before the corpus, the deduplicated catalogue
(`data/catalogue-dedup/deduplicated.jsonl`) is the single source of Work
records; `corpus-assemble` copies the selected ones into
`data/corpus/works.jsonl`, which is the single source for stages 6–8.
Stages determine their active work set at read time by joining those
works against upstream verdicts.

Changing the screening threshold therefore needs no LLM re-run: the
scores are already in the verdict sidecar, and re-assembling the corpus
applies the new cut. A corpus whose membership changes gets a new
`corpus_id` and a new analysis run-id, so stages 7 and 8 start fresh
directories instead of mixing results from two corpora.

### Resolution

`corpus-assemble`, and `fulltext-retrieval-export` when given
`--screening-threshold`, join the deduplicated catalogue against the
screening verdicts. Stages 6 and 7 read the corpus
works (stage 6 only those with a PDF); stage 8 joins the corpus works
against stage 7's verdicts and keeps only `eligible=True` ones. Stage 5
(`fulltext-retrieval`) joins nothing: it runs over the whole catalogue.

The export's join (only with `--screening-threshold`) walks the catalogue
and **silently drops** any work with no screening verdict (`_active_works` does `if sv is None: continue`).
`corpus-assemble` exits instead, unless `--allow-unscreened` includes such
works. Stage 8 exits on a verdict whose `work_id` is not in the corpus.

Analysis outputs record the `corpus_id` they ran on and check it:
`extraction-meta.json`, `eligibility-meta.json` and
`extraction-codebook-meta.json` each carry it. Stages 7 and 8 exit when
`extraction-meta.json` names another corpus and use an extraction only
when its `pdf_sha256` matches the corpus copy. Stage 8 also exits when
`eligibility-meta.json` is missing, names another corpus, or records
another `prompt_sha256` than its own `--eligibility-criteria`. With
`--skip-existing`, stages 7 and 8 refuse to resume a run whose own meta
names another corpus, and stage 6 re-extracts a work whose corpus PDF
changed.

The screening threshold is a CLI flag (`--screening-threshold`, a `float`)
on `fulltext-retrieval-export`, where it is optional and narrows the list
handed to collaborators, and on `corpus-assemble`, where it is required;
the export and the corpus record it in their metas (`None` for an
unfiltered export). Screening itself applies no
threshold and records none.

## Artifact map

Every path below is where a stage reads or writes by default; each can be
passed explicitly to override. The screening run-id leaf comes from
`data/manifest.json`, the analysis run-id leaf of stages 7 and 8 from
`data/corpus/corpus-meta.json` — see [run-manifest.md](run-manifest.md).

| Path | Model | Description |
|---|---|---|
| `data/manifest.json` | [`RunManifest`](../src/laglitsynth/manifest.py) | The review's queries and screening run-id; written once by `manifest-init` |

### Stage 1 — catalogue-fetch *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/catalogue-fetch/<slug>_<ts>.jsonl` | [`Work`](../src/laglitsynth/catalogue_fetch/models.py) | Retrieved catalogue records |
| `data/catalogue-fetch/<slug>_<ts>.meta.json` | [`FetchMeta`](../src/laglitsynth/catalogue_fetch/models.py) | Per-run provenance |

Multiple search runs produce separate timestamped files. Only the JSONL
records are concatenated for stage 2 (`cat data/catalogue-fetch/*.jsonl`).
The meta files stay as per-run provenance records — they are not merged.

### Stage 2 — catalogue-dedup *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/catalogue-dedup/deduplicated.jsonl` | [`Work`](../src/laglitsynth/catalogue_fetch/models.py) | Deduplicated catalogue — single source of Work records for the pipeline |
| `data/catalogue-dedup/dropped.jsonl` | [`DroppedRecord`](../src/laglitsynth/catalogue_dedup/models.py) | Dropped duplicates with the matching rule and the surviving work's ID |
| `data/catalogue-dedup/dedup-meta.json` | [`DeduplicationMeta`](../src/laglitsynth/catalogue_dedup/models.py) | Counts by matching rule |
| `data/catalogue-dedup/sample-<n>-seed<seed>.jsonl` | [`Work`](../src/laglitsynth/catalogue_fetch/models.py) | Optional seeded subset written by `catalogue-sample`, passed downstream as `--catalogue`; `.meta.json` sidecar is a [`SampleMeta`](../src/laglitsynth/catalogue_dedup/models.py) |

### Stage 3 — screening-abstracts *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/screening-abstracts/<run-id>/verdicts.jsonl` | [`ScreeningVerdict`](../src/laglitsynth/screening_abstracts/models.py) | Relevance score (`round(100 * p)`) or sentinel reason for every work |
| `data/screening-abstracts/<run-id>/screening-meta.json` | [`ScreeningMeta`](../src/laglitsynth/screening_abstracts/models.py) | LLM fingerprint (`LlmMeta`), counts |

Verdicts cover all works in the deduplicated catalogue, not just accepted
ones. The accept/reject decision is made by `corpus-assemble` from the
relevance score and its `--screening-threshold` flag. No
`screened.jsonl` or `rejected.jsonl` — the verdict sidecar is the only
output.

### Stage 5 — fulltext-retrieval *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/pdfs/<stem>.pdf` | (binary) | The persistent, work-keyed PDF store: one PDF per work, shared across searches |
| `data/pdfs/provenance.jsonl` | [`PdfProvenanceRecord`](../src/laglitsynth/fulltext_retrieval/models.py) | Per-work store state: where its one PDF came from, or that it is still `missing` (whole-file, last-write-wins, single writer) |
| `data/fulltext-retrieval/retrieval-meta.json` | [`RetrievalMeta`](../src/laglitsynth/fulltext_retrieval/models.py) | Per-invocation counts: `total_works`, `retrieved_count`, `missing_count`, and `by_source` (a `dict[str, int]` keyed by `PdfSource`) |
| `data/pdfs/export/{dois.txt,missing.ris,no-doi.csv,pdf-manifest.csv,README.md}` | (plain text / CSV) | Export bundle for a collaborator: still-missing works as DOI links, RIS, the no-DOI worklist, the full list for people (title, year, expected filename), and the instructions; `pack-N/` subdirs hold the same for each slice when `--packs` is given |
| `data/pdfs/export/export-meta.json` | [`ExportMeta`](../src/laglitsynth/fulltext_retrieval/models.py) | How the bundle was made: git commit, run-id, inputs, threshold (`None` when unfiltered), counts, pack sizes |

Stage 5 runs over every work in the `--catalogue`; it reads no screening
verdicts. `fulltext-retrieval` fills the store automatically from OA URLs and Unpaywall,
recording a [`PdfProvenanceRecord`](../src/laglitsynth/fulltext_retrieval/models.py)
per work (`source` one of `oa` / `unpaywall` / `missing`).
`fulltext-retrieval-export` lists the catalogue works still without a PDF
for a collaborator, optionally narrowed by `--screening-threshold`;
`fulltext-retrieval-import` ingests their returned PDF folder (`source`
`zotero-desktop` / `browser` / `institutional` / `manual`), matched back to
works through the whole deduplicated catalogue, so no prior export is
needed.

### corpus-assemble *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/corpus/works.jsonl` | [`Work`](../src/laglitsynth/catalogue_fetch/models.py) | The selected catalogue works |
| `data/corpus/verdicts.jsonl` | [`ScreeningVerdict`](../src/laglitsynth/screening_abstracts/models.py) | Their screening verdicts (none for works included by `--allow-unscreened`) |
| `data/corpus/pdfs/<stem>.pdf` | (binary) | Copies of the store PDFs of the selected works |
| `data/corpus/provenance.jsonl` | [`CorpusPdf`](../src/laglitsynth/corpus/models.py) | One row per selected work: `pdf_path` (relative to `data/corpus/`, `None` without a PDF), `content_sha256` of the copy, and the store's `PdfProvenanceRecord` |
| `data/corpus/corpus-meta.json` | [`CorpusMeta`](../src/laglitsynth/corpus/models.py) | Inputs, screening LLM and threshold, counts, `corpus_id` and `analysis_run_id` |

`corpus-assemble` selects every catalogue work whose screening verdict
scores `None` or at least `--screening-threshold`. It exits without
writing when a verdict row or `screening-meta.json` fails validation, or
when catalogue works have no verdict row and `--allow-unscreened` is not
passed. It rewrites `data/corpus/` whole, through `data/corpus.tmp/`.
`corpus_id` is the sha256 of the lines `work_id\tcontent_sha256` (empty
without a PDF), sorted by work id and joined with newlines, so it changes
only with membership or PDF bytes. `analysis_run_id` is kept from the
previous `corpus-meta.json` while `corpus_id` is unchanged and minted
afresh otherwise. The readers for stages 6–8 are in
[`corpus/load.py`](../src/laglitsynth/corpus/load.py). See
[corpus.md](corpus.md).

### Stage 6 — fulltext-extraction

| Path | Model | Description |
|---|---|---|
| `data/fulltext-extraction/extraction.jsonl` | [`ExtractedDocument`](../src/laglitsynth/fulltext_extraction/models.py) | Per-work index: `tei_path` + `content_sha256` + `pdf_sha256` of the corpus PDF + `extracted_at`; appended, the last record per work counts |
| `data/fulltext-extraction/extraction-meta.json` | [`ExtractionMeta`](../src/laglitsynth/fulltext_extraction/models.py) | `corpus_id`, GROBID version, counts |
| `data/fulltext-extraction/tei/<work_id>.tei.xml` | (XML) | Canonical GROBID TEI output — read lazily via [`TeiDocument`](../src/laglitsynth/fulltext_extraction/tei.py) |

### Stage 7 — fulltext-eligibility *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/fulltext-eligibility/<run-id>/verdicts.jsonl` | [`EligibilityVerdict`](../src/laglitsynth/fulltext_eligibility/models.py) | Per-work decisions-endpoint probability and `eligible = probability >= 0.5` (tri-state with sentinel reasons; no `source_basis` — full-text-only) |
| `data/fulltext-eligibility/<run-id>/eligibility-meta.json` | [`EligibilityMeta`](../src/laglitsynth/fulltext_eligibility/models.py) | `corpus_id`, sentinel counts + the predicate instructions as `criterion`, nested `run` + `llm` |
| `data/fulltext-eligibility/<run-id>/config.yaml` | (YAML) | Resolved CLI+config, criteria inlined |

Stage 7 assesses the corpus works that have a current extraction — it is
full-text-only, so works without one are skipped (no verdict row, no
count), not flagged.
`verdicts.jsonl` is the source of truth and the sole machine output; there
is no derived `eligible.jsonl`.

### Stage 8 — extraction-codebook *(exists)*

| Path | Model | Description |
|---|---|---|
| `<data-dir>/extraction-codebook/<run-id>/records.jsonl` | `ExtractionRecord` (built dynamically by [`build_record_model`](../src/laglitsynth/extraction_codebook/codebook.py); no `source_basis` — full-text-only) | One codebook record per processed work (successes and sentinels) |
| `<data-dir>/extraction-codebook/<run-id>/extraction-codebook-meta.json` | [`ExtractionCodebookMeta`](../src/laglitsynth/extraction_codebook/models.py) | `corpus_id`, `eligibility_prompt_sha256`, sentinel counts, nested `run` + `llm` |
| `<data-dir>/extraction-codebook/<run-id>/config.yaml` | resolved CLI+config (codebook and eligibility criteria inlined) | Self-contained run snapshot; see [configs.md](configs.md) |
| `<data-dir>/extraction-codebook/<run-id>/review.xlsx` | (XLSX, on demand) | `extraction-codebook-export` review workbook (Index + per-work tabs) |

Stage 8 joins the corpus works against stage 7's `verdicts.jsonl`
to determine eligible works, then gates that set on extraction presence —
it is full-text-only, so eligible works without an `ExtractedDocument` are
skipped (no record row, no count). Every processed work produces exactly
one record; sentinel records carry `None` in all content fields and a
`reason` from the vocabulary in
[extraction-codebook.md](extraction-codebook.md). The planned synthesis
step reads `records.jsonl` directly. The optional `review.xlsx` is a human
spot-check artifact written by `extraction-codebook-export`.

### Synthesis *(planned)*

One step aggregates the extraction records into answers to the research
questions. Its outputs, models and CLI are not designed yet; see
[pipeline.md](pipeline.md#synthesis).

## CLI contract

### Existing subcommands

Input and output paths are shown where they default under `--data-dir`
(itself defaulting to `data/`); every one can be overridden with the flag
named. `--run-id` on stage 3 and the export, and `--screening-run-id` on
`corpus-assemble`, default to the `run_id` in `<data-dir>/manifest.json`;
`--run-id` on stages 7 and 8 defaults to `analysis_run_id` in
`<data-dir>/corpus/corpus-meta.json` — see [run-manifest.md](run-manifest.md).

```sh
# Review setup — once per data dir
laglitsynth manifest-init QUERY [QUERY ...] [--data-dir DIR]

# Stage 1 — catalogue-fetch
laglitsynth catalogue-fetch QUERY --api-key KEY \
    [-o data/catalogue-fetch/<slug>_<timestamp>.jsonl] \
    [--from-year YEAR] [--to-year YEAR] [--max-records N]

# Stage 2 — catalogue-dedup
laglitsynth catalogue-dedup \
    [--input "data/catalogue-fetch/*.jsonl"] \
    [--output-dir data/catalogue-dedup/]

# Stage 2 — catalogue-sample (optional: a seeded subset as its own catalogue)
laglitsynth catalogue-sample --n N --seed N \
    [--input data/catalogue-dedup/deduplicated.jsonl] \
    [--output data/catalogue-dedup/sample-<n>-seed<seed>.jsonl]

# Stage 3 — screening-abstracts
laglitsynth screening-abstracts [data/catalogue-dedup/deduplicated.jsonl] \
    [--screening-criteria FILE] \
    [--data-dir DIR] [--run-id ID] [--config FILE] \
    --base-url URL --model MODEL [--api-key KEY] [--decisions-url URL] \
    [--max-records N] [--concurrency N] [--dry-run]

# Stage 5 — fulltext-retrieval (automatic OA + Unpaywall into data/pdfs/)
laglitsynth fulltext-retrieval \
    [--catalogue data/catalogue-dedup/deduplicated.jsonl] \
    [--data-dir DIR] \
    --email EMAIL \
    [--refetch] [--dry-run]

# Stage 5 — fulltext-retrieval-export (handoff bundle for a collaborator)
laglitsynth fulltext-retrieval-export \
    [--catalogue data/catalogue-dedup/deduplicated.jsonl] \
    [--screening-verdicts data/screening-abstracts/<run-id>/verdicts.jsonl] \
    [--run-id ID] [--screening-threshold FLOAT] [--data-dir DIR] \
    [--export-dir data/pdfs/export/] [--packs 1]

# Stage 5 — fulltext-retrieval-import (ingest a returned PDF folder)
laglitsynth fulltext-retrieval-import \
    --import-dir PATH \
    --source zotero-desktop|browser|institutional|manual \
    [--catalogue data/catalogue-dedup/deduplicated.jsonl] \
    [--data-dir DIR] [--overwrite]

# corpus-assemble (selected works + PDF copies into data/corpus/)
laglitsynth corpus-assemble \
    [--catalogue data/catalogue-dedup/deduplicated.jsonl] \
    [--screening-verdicts data/screening-abstracts/<run-id>/verdicts.jsonl] \
    [--screening-run-id ID] --screening-threshold FLOAT \
    [--allow-unscreened] [--data-dir DIR]

# Stage 6 — fulltext-extraction
laglitsynth fulltext-extraction \
    [--corpus-dir data/corpus/] \
    [--output-dir data/fulltext-extraction/] \
    [--data-dir DIR] [--grobid-url URL] [--skip-existing] \
    [--concurrency N]

# Stage 7 — fulltext-eligibility
laglitsynth fulltext-eligibility \
    [--corpus-dir data/corpus/] \
    [--extractions data/fulltext-extraction/extraction.jsonl] \
    [--extraction-output-dir data/fulltext-extraction/] \
    [--data-dir DIR] [--run-id ID] \
    [--eligibility-criteria FILE] [--config FILE] \
    [--skip-existing] [--max-records N] [--dry-run] \
    --base-url URL --model MODEL [--api-key KEY] [--decisions-url URL] \
    [--concurrency N]

# Stage 8 — extraction-codebook
laglitsynth extraction-codebook \
    [--corpus-dir data/corpus/] \
    [--eligibility-verdicts data/fulltext-eligibility/<run-id>/verdicts.jsonl] \
    [--eligibility-criteria FILE] \
    [--extractions data/fulltext-extraction/extraction.jsonl] \
    [--extraction-output-dir data/fulltext-extraction/] \
    [--data-dir DIR] [--run-id ID] \
    [--codebook FILE] [--config FILE] \
    [--skip-existing] [--max-records N] [--dry-run] \
    --base-url URL --model MODEL [--api-key KEY] \
    [--reasoning-effort low|medium|high] [--concurrency N]

# Stage 8 — extraction-codebook-export (human review, XLSX only)
laglitsynth extraction-codebook-export \
    --records data/extraction-codebook/<run-id>/records.jsonl \
    [--catalogue data/corpus/works.jsonl] \
    [--codebook FILE] [--meta PATH] [--output PATH] \
    [--n-subset N] [--subset-seed N]
```

Stages 3, 7 and 8 use the run-id directory model: outputs land at
`<data-dir>/<stage-subdir>/<run-id>/`. See [configs.md](configs.md).

Stages 3, 7 and 8 share the endpoint flags: `--base-url` (the
OpenAI-compatible base, including `/v1`) and `--model` are required (the
stage exits naming the missing flag), `--api-key` falls back to
`LLM_API_KEY` in the environment, then in `.env`, and is never written to
`config.yaml`; stages 3 and 7 also take `--decisions-url` (default
`<base-url>/decisions`), stage 8 `--reasoning-effort` (`low`, `medium`,
`high`; unset sends none). `--concurrency` (default `1`) sets the number of
in-flight LLM requests; see [LLM concurrency](#llm-concurrency).

Stages 1 and 3 take their main input (the query, the catalogue) as a
positional argument; the rest use keyword flags.

### Configuration: flags first, `.env` fallback for credentials

Every parameter is a CLI flag on the stage tool. For credential flags
(`--api-key`, `--email`) the tool also reads `.env` in the working directory
when the flag is omitted, and emits a one-line stderr notice
(`Loaded <KEY> from .env`). The explicit flag always wins; the `.env` read is
never silent. The LLM stages' `--api-key` falls back to `LLM_API_KEY` in
the environment first, then in `.env`. All other run-affecting parameters
(`--base-url`, `--grobid-url`, model names, thresholds) are CLI-only. The
driver script [scripts/run-pipeline.sh](../scripts/run-pipeline.sh) sources
`.env` (`set -a; source .env; set +a`) and passes the OpenAlex key and the
Unpaywall email as `--flag "$VAR"`; it never passes the LLM key as a flag,
so it stays out of the process list.

### LLM concurrency

Stages 3, 7 and 8 share one helper,
[`map_concurrent`](../src/laglitsynth/concurrency.py): a
`ThreadPoolExecutor` of `--concurrency` workers, each making one LLM
request per work. Only the main thread appends to the output JSONL, so
there is no lock or shared file handle. At `--concurrency 1` no pool is
created and works run one after another. Works are handed to the pool
lazily, at most two per worker ahead of the results, so Ctrl-C or an
error in the main thread cancels the queued ones and only the requests
already running finish. `--concurrency` must be at least `1`. The
useful value depends on the provider's rate limits; a 429 is retried
and then recorded as `llm-timeout`.

Rows land in this order: sentinels that need no request (stage 3's
`no-abstract`) in input order, then the LLM-backed rows in completion
order. Every consumer joins on `work_id`, so the order carries no
meaning.

Stage 3 truncates its `verdicts.jsonl` at the start of a run and has no
resume mode: a killed run is re-run from scratch, and the partial file
is still valid JSONL. Stages 7 and 8 resume with `--skip-existing`:
within the run-id directory they load the existing sidecar, skip the
`work_id`s already in it and append the rest, refusing when the recorded
`prompt_sha256` or `corpus_id` differs from the current run. Without
`--skip-existing` they truncate as stage 3 does.

The runner passes `LLM_CONCURRENCY` (default `1`) to stage 3,
`ELIGIBILITY_CONCURRENCY` (default `LLM_CONCURRENCY`) to stage 7 and
`EXTRACTION_CONCURRENCY` (default `1`) to stage 8; the review config
sets the first and last as `concurrency.llm` and
`concurrency.extraction`.

### GROBID concurrency

Stage 6 uses the same helper for its GROBID requests: `--concurrency`
(default `1`) workers each POST one corpus PDF to
`/api/processFulltextDocument` through one shared `httpx.Client`, whose
connection pool holds one connection per worker. Only the main thread
writes the TEI files and appends to `extraction.jsonl`. A request that
fails is counted in `failed_count` and written nowhere; the other
workers carry on. At `--concurrency 1` PDFs go one after another in
corpus order.

Records land in completion order. Each work is sent at most once per
run, and consumers join on `work_id` and take a work's last record, so
the order carries no meaning. A killed run leaves valid JSONL and a TEI
file for every record in it; `--skip-existing` resumes it, sending only
the works without a record for their current corpus PDF. The useful
value depends on the CPU and memory given to GROBID.

GROBID caps the requests it processes at once on its own side: the
`concurrency` setting in its `grobid.yaml` (`10` in
`grobid/grobid:0.9.0-crf`). A request beyond that cap is answered with
HTTP 503 rather than queued. Stage 6 retries a 503 a few times with a
growing pause and then counts the PDF as failed; no other error status
is retried. A `--concurrency` above GROBID's cap therefore buys nothing
but retries.

The runner passes `GROBID_CONCURRENCY` (default `3`) to stage 6; the
review config sets it as `concurrency.grobid`.

### End-to-end sequence

A complete pipeline run with manual steps noted.

```sh
# 0. Initialise the review: mints the run-id every later stage reads
laglitsynth manifest-init "lagrangian particle tracking" "ocean tracer simulation"

# 1. Catalogue fetch (repeat for different keyword sets; each lands as its
#    own timestamped file under data/catalogue-fetch/)
laglitsynth catalogue-fetch "lagrangian particle tracking" --api-key "$OPENALEX_API_KEY"
laglitsynth catalogue-fetch "ocean tracer simulation" --api-key "$OPENALEX_API_KEY"

# 2. Catalogue dedup (reads data/catalogue-fetch/*.jsonl)
laglitsynth catalogue-dedup

# 3. Screening abstracts (writes to data/screening-abstracts/<run-id>/)
# The relevance question lives in the screening-criteria YAML's
# decision block (default: the Lagrangian-oceanography example).
laglitsynth screening-abstracts \
    --screening-criteria examples/screening-criteria/lagrangian-oceanography.yaml \
    --base-url https://llm.example.org/v1 --model <decisions-model>

# 5. Fulltext retrieval (every work in the deduplicated catalogue).
#    Sticky by default: re-runs skip works that already have a PDF and
#    attempt only missing/unseen ones.
laglitsynth fulltext-retrieval --email user@example.com

# Diversify the still-missing margin via collaborators with library access:
# export the gap, hand the bundle to a collaborator, import what they return.
laglitsynth fulltext-retrieval-export --screening-threshold 50
# ... collaborator runs data/pdfs/export/missing.ris through Zotero desktop ...
laglitsynth fulltext-retrieval-import \
    --import-dir ~/Downloads/returned-pdfs/ \
    --source zotero-desktop

# Assemble the corpus: the one place the screening threshold is applied.
laglitsynth corpus-assemble --screening-threshold 50

# 6. Fulltext extraction
# Manual: start GROBID container first
laglitsynth fulltext-extraction --grobid-url http://localhost:8070

# 7. Fulltext eligibility (writes to data/fulltext-eligibility/<run-id>/)
laglitsynth fulltext-eligibility

# 8. Extraction codebook (writes to data/extraction-codebook/<run-id>/)
laglitsynth extraction-codebook
```

The sequence ends at stage 8: synthesis is planned but not built.

## Shared meta shapes

Two shared Pydantic models live in [`src/laglitsynth/models.py`](../src/laglitsynth/models.py): `RunMeta` is nested in every `*Meta` class, `LlmMeta` in those of the LLM stages.

### `RunMeta`

Run-level provenance carried by every stage meta record.

```python
class RunMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str          # module-level TOOL_NAME constant from each stage
    tool_version: str  # "alpha" placeholder until releases
    run_at: str        # ISO-8601 UTC timestamp of run completion
    validation_skipped: int  # records dropped by read_jsonl on ValidationError
```

### `LlmMeta`

LLM configuration carried by `ScreeningMeta`, `EligibilityMeta`, and
`ExtractionCodebookMeta`, and copied into `CorpusMeta.screening_llm`.
Enables reproducibility checks across runs.

```python
class LlmMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str                 # model name sent to the endpoint
    base_url: str              # OpenAI-compatible base, including /v1
    decisions_url: str | None  # decisions endpoint; None for chat stages
    reasoning_effort: str | None  # stage 8's --reasoning-effort; None when none was sent
    prompt_sha256: str         # 64 hex chars
```

No temperature or seed is sent, so none is recorded; sampling is the
provider's default. The API key is never recorded.

Per stage, the hash input is:

| Stage | `prompt_sha256` covers |
|---|---|
| 3 — screening-abstracts | `question.canonical_json() + "\n" + USER_TEMPLATE` (the screening-criteria YAML's `decision` block) |
| 7 — fulltext-eligibility | `question.canonical_json() + "\n" + USER_TEMPLATE` (the eligibility-criteria YAML's `decision` block; `criteria_sha256` in [eligibility.py](../src/laglitsynth/fulltext_eligibility/eligibility.py)) |
| 8 — extraction-codebook | rendered codebook system prompt `+ "\n" + USER_TEMPLATE` |

## Extra policy

| Category | Policy | Models |
|---|---|---|
| OpenAlex-sourced | `extra="ignore"` — upstream may add fields | `Work`, `Author`, `Authorship`, `Institution`, `Source`, `Location`, `OpenAccess`, `Biblio`, `TopicHierarchy`, `Topic`, `Keyword` |
| Internally owned | `extra="forbid"` — unexpected fields are bugs | All `*Meta`, `RunMeta`, `LlmMeta`, `RunManifest`, `ScreeningVerdict`, `DroppedRecord`, `PdfProvenanceRecord`, `CorpusPdf`, `ExtractedDocument`, `Section`, `Figure`, `Citation`, `BibReference`, `EligibilityVerdict`, `ExtractionRecord` |

## Model dependency graph

### Models

| Model | Module | Used by stages |
|---|---|---|
| [`RunMeta`](../src/laglitsynth/models.py) | `laglitsynth.models` | All `*Meta`, `RunManifest` |
| [`LlmMeta`](../src/laglitsynth/models.py) | `laglitsynth.models` | `ScreeningMeta`, `EligibilityMeta`, `ExtractionCodebookMeta`, `CorpusMeta` (as `screening_llm`) |
| [`RunManifest`](../src/laglitsynth/manifest.py) | `laglitsynth.manifest` | `manifest-init`; read by 3, the export and `corpus-assemble` |
| [`Work`](../src/laglitsynth/catalogue_fetch/models.py) | `laglitsynth.catalogue_fetch.models` | 1, 2, 3, 5, `corpus-assemble`, 7, 8 |
| [`FetchMeta`](../src/laglitsynth/catalogue_fetch/models.py) | `laglitsynth.catalogue_fetch.models` | 1 |
| [`DroppedRecord`](../src/laglitsynth/catalogue_dedup/models.py) | `laglitsynth.catalogue_dedup.models` | 2 |
| [`DeduplicationMeta`](../src/laglitsynth/catalogue_dedup/models.py), [`SampleMeta`](../src/laglitsynth/catalogue_dedup/models.py) | `laglitsynth.catalogue_dedup.models` | 2 |
| [`ScreeningVerdict`](../src/laglitsynth/screening_abstracts/models.py) | `laglitsynth.screening_abstracts.models` | 3, the export, `corpus-assemble` |
| [`ScreeningMeta`](../src/laglitsynth/screening_abstracts/models.py) | `laglitsynth.screening_abstracts.models` | 3, `corpus-assemble` |
| [`PdfSource`](../src/laglitsynth/fulltext_retrieval/models.py) (enum), [`PdfProvenanceRecord`](../src/laglitsynth/fulltext_retrieval/models.py) | `laglitsynth.fulltext_retrieval.models` | 5, `corpus-assemble` |
| [`RetrievalMeta`](../src/laglitsynth/fulltext_retrieval/models.py), [`ExportMeta`](../src/laglitsynth/fulltext_retrieval/models.py) | `laglitsynth.fulltext_retrieval.models` | 5 |
| [`CorpusPdf`](../src/laglitsynth/corpus/models.py), [`CorpusMeta`](../src/laglitsynth/corpus/models.py) | `laglitsynth.corpus.models` | `corpus-assemble`, 6, 7, 8 |
| [`ExtractedDocument`](../src/laglitsynth/fulltext_extraction/models.py), [`ExtractionMeta`](../src/laglitsynth/fulltext_extraction/models.py) | `laglitsynth.fulltext_extraction.models` | 6, 7, 8 |
| [`Section`](../src/laglitsynth/fulltext_extraction/tei.py), [`Figure`](../src/laglitsynth/fulltext_extraction/tei.py), [`Citation`](../src/laglitsynth/fulltext_extraction/tei.py), [`BibReference`](../src/laglitsynth/fulltext_extraction/tei.py) | `laglitsynth.fulltext_extraction.tei` | 7, 8 (lazy views over TEI) |
| [`DecisionQuestion`](../src/laglitsynth/prompts.py) | `laglitsynth.prompts` | 3, 7 (the criteria YAML's `decision` block) |
| [`EligibilityVerdict`](../src/laglitsynth/fulltext_eligibility/models.py), [`EligibilityMeta`](../src/laglitsynth/fulltext_eligibility/models.py) | `laglitsynth.fulltext_eligibility.models` | 7, 8 |
| `ExtractionRecord` (dynamic; [`build_record_model`](../src/laglitsynth/extraction_codebook/codebook.py)) | `laglitsynth.extraction_codebook.codebook` | 8, the codebook export |
| [`ExtractionCodebookMeta`](../src/laglitsynth/extraction_codebook/models.py) | `laglitsynth.extraction_codebook.models` | 8 |
| [`CodebookSpec`](../src/laglitsynth/extraction_codebook/codebook.py) | `laglitsynth.extraction_codebook.codebook` | 8 (codebook YAML schema) |

### Per-stage import summary

| Stage | Reads | Writes |
|---|---|---|
| 1. catalogue-fetch | — | Work, FetchMeta |
| 2. catalogue-dedup | Work | Work, DroppedRecord, DeduplicationMeta |
| 3. screening-abstracts | Work | ScreeningVerdict, ScreeningMeta |
| 5. fulltext-retrieval | Work | PdfProvenanceRecord, RetrievalMeta |
| corpus-assemble | Work, ScreeningVerdict, ScreeningMeta, PdfProvenanceRecord | Work, ScreeningVerdict, CorpusPdf, CorpusMeta |
| 6. fulltext-extraction | CorpusPdf, CorpusMeta | ExtractedDocument, ExtractionMeta |
| 7. fulltext-eligibility | Work, CorpusPdf, CorpusMeta, ExtractedDocument, ExtractionMeta | EligibilityVerdict, EligibilityMeta |
| 8. extraction-codebook | Work, CorpusPdf, CorpusMeta, EligibilityVerdict, EligibilityMeta, ExtractedDocument, ExtractionMeta | ExtractionRecord, ExtractionCodebookMeta |

## Gaps

Synthesis has no plan, no models and no CLI yet. Its design decides the
aggregations for RQ1.1 and RQ1.2 and whether the RQ1.3 rationale grouping
keeps human labelling.
