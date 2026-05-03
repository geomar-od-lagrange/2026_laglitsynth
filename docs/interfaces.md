# Interface contracts

Cross-cutting view of the pipeline's artifacts, CLI commands, and Pydantic
models. Makes the input/output chain across all 12 stages explicit so that
each stage can be implemented independently.

## Design principle: flag, don't filter

Gate stages (3, 7) write verdict sidecars but never copy or split Work
records. The deduplicated catalogue (`data/catalogue-dedup/deduplicated.jsonl`) is
the single source of Work records for the entire pipeline. Downstream
stages determine their active work set at read time by joining the
catalogue against upstream verdicts and thresholds.

This means re-thresholding (e.g. changing the screening threshold from 50
to 40) does not require re-running the LLM or re-splitting files. The
scores already exist in the verdict sidecar. Downstream stages re-run
with `--skip-existing` and only process newly-included works.

See [retuning-propagation.md](../plans/done/retuning-propagation.md) for the
full design rationale. Multi-run consensus (running LLM stages multiple
times and combining results) is a planned optional extension; see
[multi-run-consensus.md](../plans/multi-run-consensus.md).

### Resolution

Stages 5, 7, and 8 each join the deduplicated catalogue against an upstream
verdict sidecar at a caller-supplied threshold to determine their active work
set. This logic lives in a small file-local `_active_works` helper in each
stage's runner — three copies by design. A shared `laglitsynth.resolve`
module is deferred until stage 9 adds a fourth consumer; see the
[Resolve module](#resolve-module) note under "Gaps" below.

Thresholds are CLI flags (e.g. `--screening-threshold 50`). Each run's
threshold is recorded in the stage's meta sidecar for provenance. A
pipeline-level config file may replace CLI flags once thresholds are tuned
on real data.

## Artifact map

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

### Stage 3 — screening-abstracts *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/screening-abstracts/<run-id>/verdicts.jsonl` | [`ScreeningVerdict`](../src/laglitsynth/screening_abstracts/models.py) | Relevance score and reason for every work |
| `data/screening-abstracts/<run-id>/screening-meta.json` | [`ScreeningMeta`](../src/laglitsynth/screening_abstracts/models.py) | Prompt, model, threshold, counts |

Verdicts cover all works in the deduplicated catalogue, not just accepted
ones. The accept/reject decision is derived from the relevance score and
the `--screening-threshold` flag passed to downstream stages. No
`screened.jsonl` or `rejected.jsonl` — the verdict sidecar is the only
output.

### Stage 5 — fulltext-retrieval *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/fulltext-retrieval/retrieval.jsonl` | [`RetrievalRecord`](../src/laglitsynth/fulltext_retrieval/models.py) | Per-work retrieval outcome and PDF location |
| `data/fulltext-retrieval/retrieval-meta.json` | [`RetrievalMeta`](../src/laglitsynth/fulltext_retrieval/models.py) | Counts by source |
| `data/fulltext-retrieval/pdfs/<work_id>.pdf` | (binary) | Raw PDFs |
| `data/fulltext-retrieval/unretrieved.txt` | (plain text) | DOIs for manual download |

Stage 5 joins the deduplicated catalogue against stage 3's `verdicts.jsonl`
at the `--screening-threshold` cutoff to determine the active work set.
`RetrievalRecord` flags each work's retrieval status (success, failed,
abstract-only) and the PDF path.

### Stage 6 — fulltext-extraction

| Path | Model | Description |
|---|---|---|
| `data/fulltext-extraction/extraction.jsonl` | [`ExtractedDocument`](../src/laglitsynth/fulltext_extraction/models.py) | Per-work index: `tei_path` + `content_sha256` + `extracted_at` |
| `data/fulltext-extraction/extraction-meta.json` | [`ExtractionMeta`](../src/laglitsynth/fulltext_extraction/models.py) | GROBID version, counts |
| `data/fulltext-extraction/tei/<work_id>.tei.xml` | (XML) | Canonical GROBID TEI output — read lazily via [`TeiDocument`](../src/laglitsynth/fulltext_extraction/tei.py) |

### Stage 7 — fulltext-eligibility *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/fulltext-eligibility/<run-id>/verdicts.jsonl` | [`EligibilityVerdict`](../src/laglitsynth/fulltext_eligibility/models.py) | Per-work eligibility decision (tri-state with sentinel reasons) |
| `data/fulltext-eligibility/<run-id>/eligibility-meta.json` | [`EligibilityMeta`](../src/laglitsynth/fulltext_eligibility/models.py) | Counts by source basis, nested `run` + `llm` |
| `data/fulltext-eligibility/<run-id>/config.yaml` | (YAML) | Resolved CLI+config, criteria inlined |

Stage 7 joins the deduplicated catalogue against stage 3's `verdicts.jsonl`
at the `--screening-threshold` cutoff to determine the active work set,
then runs eligibility assessment on each active work. `verdicts.jsonl` is
the source of truth and the sole output; there is no derived `eligible.jsonl`.

### Stage 8 — extraction-codebook *(exists)*

| Path | Model | Description |
|---|---|---|
| `<data-dir>/extraction-codebook/<run-id>/records.jsonl` | `ExtractionRecord` (built dynamically by [`build_record_model`](../src/laglitsynth/extraction_codebook/codebook.py)) | One codebook record per input work (successes and sentinels) |
| `<data-dir>/extraction-codebook/<run-id>/extraction-codebook-meta.json` | [`ExtractionCodebookMeta`](../src/laglitsynth/extraction_codebook/models.py) | Per-branch counts, nested `run` + `llm` |
| `<data-dir>/extraction-codebook/<run-id>/config.yaml` | resolved CLI+config (codebook inlined) | Self-contained run snapshot; see [configs.md](configs.md) |

Stage 8 joins the deduplicated catalogue against stage 7's `verdicts.jsonl`
to determine eligible works. Every input work produces exactly one record;
sentinel records carry `None` in all content fields and a `reason` from the
vocabulary in [extraction-codebook.md](extraction-codebook.md). Stage 9 and
stages 10–12 read `records.jsonl` directly.

### Stage 9 — extraction-adjudication

| Path | Model | Description |
|---|---|---|
| `data/extraction-adjudication/corrections.jsonl` | `ExtractionCorrection` (new) | Per-field corrections with original and corrected values |
| `data/extraction-adjudication/adjudication-meta.json` | `ExtractionAdjudicationMeta` (new) | Mode, counts, agreement metrics |

Corrections are stored alongside original extraction records, not as
replacements. Downstream stages apply corrections at read time.

### Stage 10 — synthesis-quantitative

| Path | Model | Description |
|---|---|---|
| `data/synthesis-quantitative/statistics.json` | `SynthesisStatistics` (new) | Counts, proportions, breakdowns |

### Stage 11 — synthesis-thematic

| Path | Model | Description |
|---|---|---|
| `data/synthesis-thematic/rationale-taxonomy.json` | `RationaleTaxonomy` (new) | Themed categories with quotations |

### Stage 12 — synthesis-narrative

| Path | Model | Description |
|---|---|---|
| `data/synthesis-narrative/synthesis-draft.md` | (markdown) | Narrative keyed to research questions |

## CLI contract

### Existing subcommands

```sh
# Stage 1 — catalogue-fetch
laglitsynth catalogue-fetch QUERY --api-key KEY \
    [-o OUTPUT] [--from-year YEAR] [--to-year YEAR] [--max-records N]

# Stage 2 — catalogue-dedup
laglitsynth catalogue-dedup \
    --input "data/catalogue-fetch/*.jsonl" \
    --output-dir data/catalogue-dedup/

# Stage 3 — screening-abstracts
laglitsynth screening-abstracts INPUT PROMPT \
    [--data-dir DIR] [--run-id ID] [--config FILE] \
    [--model MODEL] [--screening-threshold N] \
    [--base-url URL] [--max-records N] [--concurrency N] [--dry-run]

# Stage 3 — screening-abstracts-export (human review)
laglitsynth screening-abstracts-export \
    --format csv|xlsx \
    --verdicts data/screening-abstracts/<run-id>/verdicts.jsonl \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    [--output PATH] [--n-subset N] [--subset-seed N]

# Stage 5 — fulltext-retrieval
laglitsynth fulltext-retrieval \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --screening-verdicts data/screening-abstracts/<run-id>/verdicts.jsonl \
    --screening-threshold 50 \
    --output-dir data/fulltext-retrieval/ \
    --email EMAIL \
    [--manual-dir DIR] [--skip-existing] [--dry-run]

# Stage 6 — fulltext-extraction
laglitsynth fulltext-extraction \
    --pdf-dir data/fulltext-retrieval/pdfs/ \
    --output-dir data/fulltext-extraction/ \
    --grobid-url URL \
    [--skip-existing]

# Stage 7 — fulltext-eligibility
laglitsynth fulltext-eligibility \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --screening-verdicts data/screening-abstracts/<run-id>/verdicts.jsonl \
    --screening-threshold 50 \
    --extractions data/fulltext-extraction/extraction.jsonl \
    [--extraction-output-dir data/fulltext-extraction/] \
    [--data-dir DIR] [--run-id ID] \
    [--eligibility-criteria FILE] [--config FILE] \
    [--skip-existing] [--max-records N] [--dry-run] \
    [--model MODEL] [--base-url URL]

# Stage 8 — extraction-codebook
laglitsynth extraction-codebook \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --eligibility-verdicts data/fulltext-eligibility/<run-id>/verdicts.jsonl \
    --extractions data/fulltext-extraction/extraction.jsonl \
    [--extraction-output-dir data/fulltext-extraction/] \
    [--data-dir DIR] [--run-id ID] \
    [--codebook FILE] [--config FILE] \
    [--skip-existing] [--max-records N] [--dry-run] \
    [--model MODEL] [--base-url URL]
```

Stages 3, 7 and 8 use the run-id directory model: outputs land at
`<data-dir>/<stage-subdir>/<run-id>/`. See [configs.md](configs.md).

Stages 1 and 3 use positional arguments. All other subcommands use
`--input` / `--output-dir` keyword flags. Stages 1 and 3 should be
harmonized to keyword flags when updated. No backwards compatibility
constraints ([AGENTS.md](../AGENTS.md)).

### Configuration: flags first, `.env` fallback for credentials

Every parameter is a CLI flag on the stage tool. For credential flags
(`--api-key`, `--email`) the tool also reads `.env` in the working directory
when the flag is omitted, and emits a one-line stderr notice
(`Loaded <KEY> from .env`). The explicit flag always wins; the `.env` read is
never silent. All other run-affecting parameters (`--base-url`, `--grobid-url`,
model names, thresholds) are CLI-only. Driver scripts
([scripts/run-pipeline.sh](../scripts/run-pipeline.sh),
[scripts/nesh-pipeline.sbatch](../scripts/nesh-pipeline.sbatch)) source `.env`
(`set -a; source .env; set +a`) and pass the values as `--flag "$VAR"` to each
tool — this is still the recommended pattern for wrapper invocations.

### Planned subcommands

```sh
# Stage 9 — extraction-adjudication
laglitsynth extraction-adjudication \
    --data-dir data/ \
    --output-dir data/extraction-adjudication/

# Stage 10 — synthesis-quantitative
laglitsynth synthesis-quantitative \
    --data-dir data/ \
    --output-dir data/synthesis-quantitative/

# Stage 11 — synthesis-thematic
laglitsynth synthesis-thematic \
    --data-dir data/ \
    --output-dir data/synthesis-thematic/

# Stage 12 — synthesis-narrative
laglitsynth synthesis-narrative \
    --statistics data/synthesis-quantitative/statistics.json \
    --taxonomy data/synthesis-thematic/rationale-taxonomy.json \
    --output-dir data/synthesis-narrative/
```

### End-to-end sequence

A complete pipeline run with manual steps noted.

```sh
# 1. Catalogue fetch (repeat for different keyword sets)
laglitsynth catalogue-fetch "lagrangian particle tracking" \
    --api-key "$OPENALEX_API_KEY" \
    -o data/catalogue-fetch/search_a.jsonl
laglitsynth catalogue-fetch "ocean tracer simulation" \
    --api-key "$OPENALEX_API_KEY" \
    -o data/catalogue-fetch/search_b.jsonl

# 2. Catalogue dedup (--input accepts multiple paths or globs)
laglitsynth catalogue-dedup \
    --input "data/catalogue-fetch/*.jsonl" \
    --output-dir data/catalogue-dedup/

# 3. Screening abstracts (writes to data/screening-abstracts/<run-id>/)
# Note the run-id printed at the end — you need it for stages 5, 7, 8.
RUN_ID="$(laglitsynth generate-run-id)"
laglitsynth screening-abstracts \
    data/catalogue-dedup/deduplicated.jsonl \
    "Is this about computational Lagrangian methods in oceanography?" \
    --run-id "$RUN_ID"

# 5. Fulltext retrieval (inline-joins catalogue + stage 3 verdicts)
laglitsynth fulltext-retrieval \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --screening-verdicts "data/screening-abstracts/$RUN_ID/verdicts.jsonl" \
    --screening-threshold 50 \
    --output-dir data/fulltext-retrieval/ \
    --email user@example.com \
    --skip-existing

# Manual: download unretrieved PDFs from data/fulltext-retrieval/unretrieved.txt
# Place them in data/fulltext-retrieval/manual/ named by OpenAlex work ID
# Then re-run retrieval to pick up manual PDFs

# 6. Fulltext extraction
# Manual: start GROBID container first
laglitsynth fulltext-extraction \
    --pdf-dir data/fulltext-retrieval/pdfs/ \
    --output-dir data/fulltext-extraction/ \
    --grobid-url http://localhost:8070

# 7. Fulltext eligibility (writes to data/fulltext-eligibility/<run-id>/)
laglitsynth fulltext-eligibility \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --screening-verdicts "data/screening-abstracts/$RUN_ID/verdicts.jsonl" \
    --screening-threshold 50 \
    --extractions data/fulltext-extraction/extraction.jsonl \
    --run-id "$RUN_ID"

# 8. Extraction codebook (writes to data/extraction-codebook/<run-id>/)
laglitsynth extraction-codebook \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --eligibility-verdicts "data/fulltext-eligibility/$RUN_ID/verdicts.jsonl" \
    --extractions data/fulltext-extraction/extraction.jsonl \
    --run-id "$RUN_ID"

# Stages 9–12 are not yet implemented. Stop here for now.

# 9. Extraction adjudication (pass-through in prototype)
laglitsynth extraction-adjudication \
    --data-dir data/ \
    --output-dir data/extraction-adjudication/

# 10. Synthesis: quantitative
laglitsynth synthesis-quantitative \
    --data-dir data/ \
    --output-dir data/synthesis-quantitative/

# 11. Synthesis: thematic
laglitsynth synthesis-thematic \
    --data-dir data/ \
    --output-dir data/synthesis-thematic/

# 12. Synthesis: narrative
laglitsynth synthesis-narrative \
    --statistics data/synthesis-quantitative/statistics.json \
    --taxonomy data/synthesis-thematic/rationale-taxonomy.json \
    --output-dir data/synthesis-narrative/
```

## Shared meta shapes

Two shared Pydantic models live in [`src/laglitsynth/models.py`](../src/laglitsynth/models.py) and are nested inside every `*Meta` class.

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
`ExtractionCodebookMeta`. Enables reproducibility checks across runs.

```python
class LlmMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str           # Ollama model tag
    temperature: float   # explicit value passed to the API (currently 0.8)
    prompt_sha256: str   # 64 hex chars
```

Per stage, the hash input is:

| Stage | `prompt_sha256` covers |
|---|---|
| 3 — screening-abstracts | `SYSTEM_PROMPT + "\n" + user_prompt` (user prompt is a CLI arg) |
| 7 — fulltext-eligibility | `SYSTEM_PROMPT + "\n" + USER_TEMPLATE + "\n" + num_ctx` |
| 8 — extraction-codebook | `SYSTEM_PROMPT + "\n" + USER_TEMPLATE + "\n" + num_ctx + "\n" + CHAR_BUDGET` |

Stages 7 and 8 fold their Ollama `num_ctx` setting into the hash so a
context-window change produces a different digest. Stage 8 also folds
`CHAR_BUDGET`, its prompt-truncation placeholder.

## Extra policy

| Category | Policy | Models |
|---|---|---|
| OpenAlex-sourced | `extra="ignore"` — upstream may add fields | `Work`, `Author`, `Authorship`, `Institution`, `Source`, `Location`, `OpenAccess`, `Biblio`, `TopicHierarchy`, `Topic`, `Keyword` |
| Internally owned | `extra="forbid"` — unexpected fields are bugs | All `*Meta`, `RunMeta`, `LlmMeta`, `ScreeningVerdict`, `DroppedRecord`, `RetrievalRecord`, `RetrievalStatus`, `ExtractedDocument`, `Section`, `Figure`, `Citation`, `BibReference`, `EligibilityVerdict`, `ExtractionRecord` |

## Model dependency graph

### Existing models

| Model | Module | Used by stages |
|---|---|---|
| [`RunMeta`](../src/laglitsynth/models.py) | `laglitsynth.models` | All `*Meta` |
| [`LlmMeta`](../src/laglitsynth/models.py) | `laglitsynth.models` | `ScreeningMeta`, `EligibilityMeta`, `ExtractionCodebookMeta` |
| [`Work`](../src/laglitsynth/catalogue_fetch/models.py) | `laglitsynth.catalogue_fetch.models` | 1, 2, 3 |
| [`FetchMeta`](../src/laglitsynth/catalogue_fetch/models.py) | `laglitsynth.catalogue_fetch.models` | 1 |
| [`ScreeningVerdict`](../src/laglitsynth/screening_abstracts/models.py) | `laglitsynth.screening_abstracts.models` | 3 |
| [`ScreeningMeta`](../src/laglitsynth/screening_abstracts/models.py) | `laglitsynth.screening_abstracts.models` | 3 |
| [`DroppedRecord`](../src/laglitsynth/catalogue_dedup/models.py) | `laglitsynth.catalogue_dedup.models` | 2 |
| [`DeduplicationMeta`](../src/laglitsynth/catalogue_dedup/models.py) | `laglitsynth.catalogue_dedup.models` | 2 |
| [`RetrievalStatus`](../src/laglitsynth/fulltext_retrieval/models.py) | `laglitsynth.fulltext_retrieval.models` | 5 |
| [`RetrievalRecord`](../src/laglitsynth/fulltext_retrieval/models.py) | `laglitsynth.fulltext_retrieval.models` | 5 |
| [`RetrievalMeta`](../src/laglitsynth/fulltext_retrieval/models.py) | `laglitsynth.fulltext_retrieval.models` | 5 |
| [`ExtractedDocument`](../src/laglitsynth/fulltext_extraction/models.py) | `laglitsynth.fulltext_extraction.models` | 6, 7, 8 |
| [`ExtractionMeta`](../src/laglitsynth/fulltext_extraction/models.py) | `laglitsynth.fulltext_extraction.models` | 6 |
| [`Section`](../src/laglitsynth/fulltext_extraction/tei.py), [`Figure`](../src/laglitsynth/fulltext_extraction/tei.py), [`Citation`](../src/laglitsynth/fulltext_extraction/tei.py), [`BibReference`](../src/laglitsynth/fulltext_extraction/tei.py) | `laglitsynth.fulltext_extraction.tei` | 7, 8 (lazy views over TEI) |
| [`EligibilityVerdict`](../src/laglitsynth/fulltext_eligibility/models.py) | `laglitsynth.fulltext_eligibility.models` | 7 |
| [`EligibilityMeta`](../src/laglitsynth/fulltext_eligibility/models.py) | `laglitsynth.fulltext_eligibility.models` | 7 |
| `ExtractionRecord` (dynamic; [`build_record_model`](../src/laglitsynth/extraction_codebook/codebook.py)) | `laglitsynth.extraction_codebook.codebook` | 8, 9, 10, 11 |
| [`ExtractionCodebookMeta`](../src/laglitsynth/extraction_codebook/models.py) | `laglitsynth.extraction_codebook.models` | 8 |
| [`CodebookSpec`](../src/laglitsynth/extraction_codebook/codebook.py) | `laglitsynth.extraction_codebook.codebook` | 8 (codebook YAML schema) |

### Models not yet defined

| Model | Planned module | Stage |
|---|---|---|
| `ExtractionCorrection` | `laglitsynth.extraction_adjudication.models` | 9 |
| `ExtractionAdjudicationMeta` | `laglitsynth.extraction_adjudication.models` | 9 |
| `SynthesisStatistics` | `laglitsynth.synthesis_quantitative.models` | 10 |
| `RationaleTaxonomy` | `laglitsynth.synthesis_thematic.models` | 11 |

### Per-stage import summary

| Stage | Reads | Writes |
|---|---|---|
| 1. catalogue-fetch | — | Work, FetchMeta |
| 2. catalogue-dedup | Work | Work, DroppedRecord, DeduplicationMeta |
| 3. screening-abstracts | Work | ScreeningVerdict, ScreeningMeta |
| 5. fulltext-retrieval | Work + ScreeningVerdict (inline join) | RetrievalRecord, RetrievalMeta |
| 6. fulltext-extraction | (PDFs) | ExtractedDocument, ExtractionMeta |
| 7. fulltext-eligibility | Work + ScreeningVerdict (inline join), ExtractedDocument | EligibilityVerdict, EligibilityMeta |
| 8. extraction-codebook | Work + EligibilityVerdict (inline join), ExtractedDocument | ExtractionRecord, ExtractionCodebookMeta |
| 9. extraction-adjudication | ExtractionRecord (via resolve) | ExtractionCorrection, ExtractionAdjudicationMeta |
| 10. synthesis-quantitative | ExtractionRecord (via resolve) | SynthesisStatistics |
| 11. synthesis-thematic | ExtractionRecord (via resolve) | RationaleTaxonomy |
| 12. synthesis-narrative | SynthesisStatistics, RationaleTaxonomy | (markdown) |

## Gaps

### No plan exists

- Stage 10 (quantitative synthesis) — aggregation logic, output schema,
  uncertainty propagation from `source_basis`.
- Stage 11 (thematic synthesis) — clustering approach, human review
  workflow, taxonomy schema.
- Stage 12 (narrative synthesis) — template structure, evidence-grounding
  mechanism, output format.

### No model definition exists

- `ExtractionCorrection` — per-field corrections with original and
  corrected values.
- `SynthesisStatistics` — depends on which breakdowns are needed (by
  sub-discipline, by year, by source basis).
- `RationaleTaxonomy` — depends on how thematic clusters are represented
  (category label, supporting quotes, paper references).

### Resolve module

Stages 5, 7, and 8 each perform an inline join of the deduplicated catalogue
against upstream verdict sidecars at a caller-supplied threshold. This logic
is currently duplicated per stage. A shared `laglitsynth.resolve` module is
planned to consolidate it once a fourth consumer would otherwise add another
copy (expected when stage 9 lands).
