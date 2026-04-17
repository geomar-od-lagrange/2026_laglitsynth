# Interface contracts

Cross-cutting view of the pipeline's artifacts, CLI commands, and Pydantic
models. Makes the input/output chain across all 12 stages explicit so that
each stage can be implemented independently.

## Design principle: flag, don't filter

Gate stages (3, 4, 7) write verdict sidecars but never copy or split Work
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

A shared `resolve` module joins the deduplicated catalogue against verdict
sidecars and caller-supplied thresholds to produce the active work set for
a given pipeline stage. Every stage from 5 onward uses this module. The
resolve logic lives in one place, not duplicated per stage.

Thresholds are CLI flags (e.g. `--screening-threshold 50`), passed through
to the resolve module. Each run's threshold is recorded in the stage's meta
sidecar for provenance. A pipeline-level config file may replace CLI flags
once thresholds are tuned on real data.

## Artifact map

### Stage 1 — catalogue-fetch *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/catalogue-fetch/<slug>_<ts>.jsonl` | [`Work`](../src/laglitsynth/catalogue_fetch/models.py) | Retrieved catalogue records |
| `data/catalogue-fetch/<slug>_<ts>.meta.json` | [`FetchMeta`](../src/laglitsynth/catalogue_fetch/models.py) | Per-run provenance |

Multiple search runs produce separate timestamped files. Only the JSONL
records are concatenated for stage 2 (`cat data/catalogue-fetch/*.jsonl`).
The meta files stay as per-run provenance records — they are not merged.

### Stage 2 — catalogue-dedup *(exists, pass-all MVP)*

| Path | Model | Description |
|---|---|---|
| `data/catalogue-dedup/deduplicated.jsonl` | [`Work`](../src/laglitsynth/catalogue_fetch/models.py) | Deduplicated catalogue — single source of Work records for the pipeline |
| `data/catalogue-dedup/dropped.jsonl` | Work + merge reason (TBD) | Dropped duplicates with reason |
| `data/catalogue-dedup/dedup-meta.json` | [`DeduplicationMeta`](../src/laglitsynth/catalogue_dedup/models.py) | Counts by matching rule |

### Stage 3 — screening-abstracts *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/screening-abstracts/verdicts.jsonl` | [`ScreeningVerdict`](../src/laglitsynth/screening_abstracts/models.py) | Relevance score and reason for every work |
| `data/screening-abstracts/screening-meta.json` | [`ScreeningMeta`](../src/laglitsynth/screening_abstracts/models.py) | Prompt, model, threshold, counts |

Verdicts cover all works in the deduplicated catalogue, not just accepted
ones. The accept/reject decision is derived from the relevance score and
the threshold passed to stage 4's `--screening-threshold` flag. No
`screened.jsonl` or `rejected.jsonl` — the verdict sidecar is the only
output.

### Stage 4 — screening-adjudication *(exists, pass-through MVP)*

| Path | Model | Description |
|---|---|---|
| `data/screening-adjudication/verdicts.jsonl` | [`AdjudicationVerdict`](../src/laglitsynth/screening_adjudication/models.py) | Per-work adjudication decision (accept/reject/skip) |
| `data/screening-adjudication/adjudication-meta.json` | [`AdjudicationMeta`](../src/laglitsynth/screening_adjudication/models.py) | Threshold applied, counts |
| `data/screening-adjudication/included.jsonl` | [`Work`](../src/laglitsynth/catalogue_fetch/models.py) | Work records above threshold (convenience for stage 5) |

Stage 4 reads stage 3's `verdicts.jsonl` and the deduplicated catalogue,
applies `--screening-threshold`, and emits `AdjudicationVerdict` records
plus a convenience `included.jsonl` of the accepted Work records for stage
5. The pass-through MVP sets `decision="accept"` and `reviewer="pass-through"`
for every above-threshold work.

### Stage 5 — fulltext-retrieval *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/fulltext-retrieval/retrieval.jsonl` | [`RetrievalRecord`](../src/laglitsynth/fulltext_retrieval/models.py) | Per-work retrieval outcome and PDF location |
| `data/fulltext-retrieval/retrieval-meta.json` | [`RetrievalMeta`](../src/laglitsynth/fulltext_retrieval/models.py) | Counts by source |
| `data/fulltext-retrieval/pdfs/<work_id>.pdf` | (binary) | Raw PDFs |
| `data/fulltext-retrieval/unretrieved.txt` | (plain text) | DOIs for manual download |

`RetrievalRecord` flags each work's retrieval status (success, failed,
abstract-only) and the PDF path. Already follows the flag pattern.

### Stage 6 — fulltext-extraction

| Path | Model | Description |
|---|---|---|
| `data/fulltext-extraction/extraction.jsonl` | [`ExtractedDocument`](../src/laglitsynth/fulltext_extraction/models.py) | Per-work index: `tei_path` + `content_sha256` + `extracted_at` |
| `data/fulltext-extraction/extraction-meta.json` | [`ExtractionMeta`](../src/laglitsynth/fulltext_extraction/models.py) | GROBID version, counts |
| `data/fulltext-extraction/tei/<work_id>.tei.xml` | (XML) | Canonical GROBID TEI output — read lazily via [`TeiDocument`](../src/laglitsynth/fulltext_extraction/tei.py) |

### Stage 7 — fulltext-eligibility *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/fulltext-eligibility/verdicts.jsonl` | [`EligibilityVerdict`](../src/laglitsynth/fulltext_eligibility/models.py) | Per-work eligibility decision (tri-state with sentinel reasons) |
| `data/fulltext-eligibility/eligible.jsonl` | [`Work`](../src/laglitsynth/catalogue_fetch/models.py) | Work records where `verdict.eligible is True` (derived convenience for stage 8) |
| `data/fulltext-eligibility/eligibility-meta.json` | [`EligibilityMeta`](../src/laglitsynth/fulltext_eligibility/models.py) | Counts by source basis, nested `run` + `llm` |

`verdicts.jsonl` is the source of truth; `eligible.jsonl` rebuilds each
run from the catalogue join against the verdict sidecar, mirroring
stage 4's [`included.jsonl`](screening-adjudication.md).

### Stage 8 — extraction-codebook

| Path | Model | Description |
|---|---|---|
| `data/extraction-codebook/records.jsonl` | `ExtractionRecord` (new) | One codebook record per work |
| `data/extraction-codebook/extraction-meta.json` | `DataExtractionMeta` (new) | Model, prompt version, counts |

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
laglitsynth catalogue-fetch QUERY \
    [-o OUTPUT] [--from-year YEAR] [--to-year YEAR] [--max-records N]

# Stage 2 — catalogue-dedup
laglitsynth catalogue-dedup \
    --input data/catalogue-fetch/combined.jsonl \
    --output-dir data/catalogue-dedup/

# Stage 3 — screening-abstracts
laglitsynth screening-abstracts INPUT PROMPT \
    [--output-dir DIR] [--model MODEL] [--screening-threshold N] \
    [--base-url URL] [--max-records N] [--dry-run]

# Stage 4 — screening-adjudication
laglitsynth screening-adjudication \
    --input data/screening-abstracts/verdicts.jsonl \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --screening-threshold 50 \
    --output-dir data/screening-adjudication/

# Stage 5 — fulltext-retrieval
laglitsynth fulltext-retrieval \
    --input data/screening-adjudication/included.jsonl \
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
    --catalogue data/screening-adjudication/included.jsonl \
    --extractions data/fulltext-extraction/extraction.jsonl \
    [--extraction-output-dir data/fulltext-extraction/] \
    [--output-dir data/fulltext-eligibility/] \
    [--skip-existing] [--max-records N] [--dry-run] \
    [--model MODEL] [--base-url URL]
```

Stages 1 and 3 use positional arguments. All other subcommands use
`--input` / `--output-dir` keyword flags. Stages 1 and 3 should be
harmonized to keyword flags when updated. No backwards compatibility
constraints ([AGENTS.md](../AGENTS.md)).

### Planned subcommands

```sh
# Stage 8 — extraction-codebook
laglitsynth extraction-codebook \
    --data-dir data/ \
    --extractions data/fulltext-extraction/extraction.jsonl \
    --output-dir data/extraction-codebook/ \
    [--skip-existing]

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
    -o data/catalogue-fetch/search_a.jsonl
laglitsynth catalogue-fetch "ocean tracer simulation" \
    -o data/catalogue-fetch/search_b.jsonl

# Manual: concatenate search result records (not meta files)
cat data/catalogue-fetch/search_*.jsonl > data/catalogue-fetch/combined.jsonl

# 2. Catalogue dedup
laglitsynth catalogue-dedup \
    --input data/catalogue-fetch/combined.jsonl \
    --output-dir data/catalogue-dedup/

# 3. Screening abstracts
laglitsynth screening-abstracts \
    data/catalogue-dedup/deduplicated.jsonl \
    "Is this about computational Lagrangian methods in oceanography?" \
    --screening-threshold 50 \
    --output-dir data/screening-abstracts/

# 4. Screening adjudication (pass-through in prototype)
laglitsynth screening-adjudication \
    --input data/screening-abstracts/verdicts.jsonl \
    --catalogue data/catalogue-dedup/deduplicated.jsonl \
    --screening-threshold 50 \
    --output-dir data/screening-adjudication/

# 5. Fulltext retrieval
laglitsynth fulltext-retrieval \
    --input data/screening-adjudication/included.jsonl \
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

# 7. Fulltext eligibility
laglitsynth fulltext-eligibility \
    --catalogue data/screening-adjudication/included.jsonl \
    --extractions data/fulltext-extraction/extraction.jsonl \
    --extraction-output-dir data/fulltext-extraction/ \
    --output-dir data/fulltext-eligibility/

# 8. Extraction codebook
laglitsynth extraction-codebook \
    --data-dir data/ \
    --extractions data/fulltext-extraction/extraction.jsonl \
    --output-dir data/extraction-codebook/

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

### `_RunMeta`

Run-level provenance carried by every stage meta record.

```python
class _RunMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str          # module-level TOOL_NAME constant from each stage
    tool_version: str  # "alpha" placeholder until releases
    run_at: str        # ISO-8601 UTC timestamp of run completion
    validation_skipped: int  # records dropped by read_jsonl on ValidationError
```

### `_LlmMeta`

LLM configuration carried by `ScreeningMeta` and `EligibilityMeta`. Enables reproducibility checks across runs.

```python
class _LlmMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str           # Ollama model tag
    temperature: float   # explicit value passed to the API (currently 0.8)
    prompt_sha256: str   # sha256(SYSTEM_PROMPT + "\n" + user prompt), 64 hex chars
```

## Extra policy

| Category | Policy | Models |
|---|---|---|
| OpenAlex-sourced | `extra="ignore"` — upstream may add fields | `Work`, `Author`, `Authorship`, `Institution`, `Source`, `Location`, `OpenAccess`, `Biblio`, `TopicHierarchy`, `Topic`, `Keyword` |
| Internally owned | `extra="forbid"` — unexpected fields are bugs | All `*Meta`, `_RunMeta`, `_LlmMeta`, `ScreeningVerdict`, `AdjudicationVerdict`, `RetrievalRecord`, `RetrievalStatus`, `ExtractedDocument`, `Section`, `Figure`, `Citation`, `BibReference`, `EligibilityVerdict` |

## Model dependency graph

### Existing models

| Model | Module | Used by stages |
|---|---|---|
| [`_RunMeta`](../src/laglitsynth/models.py) | `laglitsynth.models` | All `*Meta` |
| [`_LlmMeta`](../src/laglitsynth/models.py) | `laglitsynth.models` | `ScreeningMeta` |
| [`Work`](../src/laglitsynth/catalogue_fetch/models.py) | `laglitsynth.catalogue_fetch.models` | 1, 2, 3 |
| [`FetchMeta`](../src/laglitsynth/catalogue_fetch/models.py) | `laglitsynth.catalogue_fetch.models` | 1 |
| [`ScreeningVerdict`](../src/laglitsynth/screening_abstracts/models.py) | `laglitsynth.screening_abstracts.models` | 3 |
| [`ScreeningMeta`](../src/laglitsynth/screening_abstracts/models.py) | `laglitsynth.screening_abstracts.models` | 3 |
| [`DeduplicationMeta`](../src/laglitsynth/catalogue_dedup/models.py) | `laglitsynth.catalogue_dedup.models` | 2 |
| [`AdjudicationVerdict`](../src/laglitsynth/screening_adjudication/models.py) | `laglitsynth.screening_adjudication.models` | 4 |
| [`AdjudicationMeta`](../src/laglitsynth/screening_adjudication/models.py) | `laglitsynth.screening_adjudication.models` | 4 |
| [`RetrievalStatus`](../src/laglitsynth/fulltext_retrieval/models.py) | `laglitsynth.fulltext_retrieval.models` | 5 |
| [`RetrievalRecord`](../src/laglitsynth/fulltext_retrieval/models.py) | `laglitsynth.fulltext_retrieval.models` | 5 |
| [`RetrievalMeta`](../src/laglitsynth/fulltext_retrieval/models.py) | `laglitsynth.fulltext_retrieval.models` | 5 |
| [`ExtractedDocument`](../src/laglitsynth/fulltext_extraction/models.py) | `laglitsynth.fulltext_extraction.models` | 6, 7, 8 |
| [`ExtractionMeta`](../src/laglitsynth/fulltext_extraction/models.py) | `laglitsynth.fulltext_extraction.models` | 6 |
| [`Section`](../src/laglitsynth/fulltext_extraction/tei.py), [`Figure`](../src/laglitsynth/fulltext_extraction/tei.py), [`Citation`](../src/laglitsynth/fulltext_extraction/tei.py), [`BibReference`](../src/laglitsynth/fulltext_extraction/tei.py) | `laglitsynth.fulltext_extraction.tei` | 7, 8 (lazy views over TEI) |
| [`EligibilityVerdict`](../src/laglitsynth/fulltext_eligibility/models.py) | `laglitsynth.fulltext_eligibility.models` | 7 |
| [`EligibilityMeta`](../src/laglitsynth/fulltext_eligibility/models.py) | `laglitsynth.fulltext_eligibility.models` | 7 |

### Models not yet defined

| Model | Planned module | Stage |
|---|---|---|
| `ExtractionRecord` | `laglitsynth.extraction_codebook.models` | 8, 9, 10, 11 |
| `DataExtractionMeta` | `laglitsynth.extraction_codebook.models` | 8 |
| `ExtractionCorrection` | `laglitsynth.extraction_adjudication.models` | 9 |
| `ExtractionAdjudicationMeta` | `laglitsynth.extraction_adjudication.models` | 9 |
| `SynthesisStatistics` | `laglitsynth.synthesis_quantitative.models` | 10 |
| `RationaleTaxonomy` | `laglitsynth.synthesis_thematic.models` | 11 |

### Per-stage import summary

| Stage | Reads | Writes |
|---|---|---|
| 1. catalogue-fetch | — | Work, FetchMeta |
| 2. catalogue-dedup | Work | Work, DeduplicationMeta |
| 3. screening-abstracts | Work | ScreeningVerdict, ScreeningMeta |
| 4. screening-adjudication | ScreeningVerdict, Work | AdjudicationVerdict, AdjudicationMeta, Work (included.jsonl) |
| 5. fulltext-retrieval | Work (via resolve) | RetrievalRecord, RetrievalMeta |
| 6. fulltext-extraction | (PDFs) | ExtractedDocument, ExtractionMeta |
| 7. fulltext-eligibility | Work, ExtractedDocument (via resolve) | EligibilityVerdict, EligibilityMeta, Work (eligible.jsonl) |
| 8. extraction-codebook | Work, ExtractedDocument (via resolve) | ExtractionRecord, DataExtractionMeta |
| 9. extraction-adjudication | ExtractionRecord (via resolve) | ExtractionCorrection, ExtractionAdjudicationMeta |
| 10. synthesis-quantitative | ExtractionRecord (via resolve) | SynthesisStatistics |
| 11. synthesis-thematic | ExtractionRecord (via resolve) | RationaleTaxonomy |
| 12. synthesis-narrative | SynthesisStatistics, RationaleTaxonomy | (markdown) |

## Gaps

### No plan exists

- Stage 8 (data extraction) — the [codebook](codebook.md) defines the
  fields but the stage plan (CLI, LLM prompt, two-pass strategy, model
  definition) is not written.
- Stage 10 (quantitative synthesis) — aggregation logic, output schema,
  uncertainty propagation from `source_basis`.
- Stage 11 (thematic synthesis) — clustering approach, human review
  workflow, taxonomy schema.
- Stage 12 (narrative synthesis) — template structure, evidence-grounding
  mechanism, output format.

### No model definition exists

- `ExtractionRecord` — the most complex new model. Must encode all
  [codebook](codebook.md) fields (identification, numerical choices,
  reproducibility indicators, extraction metadata), each as a value +
  context-snippet pair. The codebook explicitly defers the Pydantic model
  to after phase 3 iteration.
- `ExtractionCorrection` — per-field corrections with original and
  corrected values.
- `SynthesisStatistics` — depends on which breakdowns are needed (by
  sub-discipline, by year, by source basis).
- `RationaleTaxonomy` — depends on how thematic clusters are represented
  (category label, supporting quotes, paper references).

### Resolve module

The `laglitsynth.resolve` module does not exist yet. It must join the
deduplicated catalogue against verdict sidecars and caller-supplied
thresholds. This is the single most important new piece of shared
infrastructure — every stage from 5 onward depends on it.
