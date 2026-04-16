# Interface contracts

Cross-cutting view of the pipeline's artifacts, CLI commands, and Pydantic
models. Makes the input/output chain across all 12 stages explicit so that
each stage can be implemented independently.

## Artifact map

### Stage 1 — search *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/openalex/<slug>_<ts>.jsonl` | [`Work`](../src/laglitsynth/openalex/models.py) | Retrieved catalogue records |
| `data/openalex/<slug>_<ts>.meta.json` | [`FetchMeta`](../src/laglitsynth/openalex/models.py) | Query, timestamp, counts |

Multiple runs produce separate timestamped files; concatenation for stage 2
is a manual `cat` step.

### Stage 2 — deduplication

| Path | Model | Description |
|---|---|---|
| `data/dedup/deduplicated.jsonl` | [`Work`](../src/laglitsynth/openalex/models.py) | Deduplicated catalogue |
| `data/dedup/dropped.jsonl` | Work + merge reason (TBD) | Dropped duplicates with reason |
| `data/dedup/dedup-meta.json` | `DeduplicationMeta` (new) | Counts by matching rule |

### Stage 3 — screen-abstracts *(exists, needs path change)*

| Path | Model | Description |
|---|---|---|
| `data/screening/screened.jsonl` | [`Work`](../src/laglitsynth/openalex/models.py) | Accepted work records |
| `data/screening/rejected.jsonl` | [`Work`](../src/laglitsynth/openalex/models.py) | Rejected work records |
| `data/screening/verdicts.jsonl` | [`FilterVerdict`](../src/laglitsynth/llmfilter/models.py) | Per-work relevance scores |
| `data/screening/screening-meta.json` | [`FilterMeta`](../src/laglitsynth/llmfilter/models.py) | Prompt, model, threshold, counts |

The existing code writes to `data/filtered/` with timestamped filenames.
This must change to `data/screening/` with fixed filenames. The timestamp
moves into the meta sidecar. See [Inconsistencies to
resolve](#inconsistencies-to-resolve).

### Stage 4 — adjudication (screening)

| Path | Model | Description |
|---|---|---|
| `data/adjudication/included.jsonl` | [`Work`](../src/laglitsynth/openalex/models.py) | Included catalogue |
| `data/adjudication/adjudication-meta.json` | `AdjudicationMeta` (new) | Mode, counts |

### Stage 5 — full-text-retrieval

| Path | Model | Description |
|---|---|---|
| `data/fulltext/retrieval.jsonl` | `RetrievalRecord` (new) | Per-work retrieval outcome |
| `data/fulltext/retrieval-meta.json` | `RetrievalMeta` (new) | Counts by source |
| `data/fulltext/pdfs/<work_id>.pdf` | (binary) | Raw PDFs |
| `data/fulltext/unretrieved.txt` | (plain text) | DOIs for manual download |

### Stage 6 — full-text-extraction

| Path | Model | Description |
|---|---|---|
| `data/fulltext/extraction.jsonl` | `ExtractedDocument` (new) | Structured sections per work |
| `data/fulltext/extraction-meta.json` | `ExtractionMeta` (new) | GROBID version, counts |
| `data/fulltext/tei/<work_id>.tei.xml` | (XML) | Raw GROBID output |

### Stage 7 — eligibility

| Path | Model | Description |
|---|---|---|
| `data/eligibility/eligible.jsonl` | [`Work`](../src/laglitsynth/openalex/models.py) | Eligible corpus |
| `data/eligibility/verdicts.jsonl` | `EligibilityVerdict` (new) | Per-work eligibility decision |
| `data/eligibility/eligibility-meta.json` | `EligibilityMeta` (new) | Counts by source basis |

### Stage 8 — data-extraction

| Path | Model | Description |
|---|---|---|
| `data/extraction/records.jsonl` | `ExtractionRecord` (new) | One codebook record per work |
| `data/extraction/data-extraction-meta.json` | `DataExtractionMeta` (new) | Model, prompt version, counts |

The meta file is named `data-extraction-meta.json` (not
`extraction-meta.json`) to avoid ambiguity with stage 6's
`data/fulltext/extraction-meta.json`. The Python class is
`DataExtractionMeta` for the same reason.

### Stage 9 — adjudication (extraction)

| Path | Model | Description |
|---|---|---|
| `data/adjudication-extraction/validated.jsonl` | `ExtractionRecord` | Corrected extraction records |
| `data/adjudication-extraction/adjudication-meta.json` | `ExtractionAdjudicationMeta` (new) | Mode, counts, agreement metrics |

### Stage 10 — quantitative-synthesis

| Path | Model | Description |
|---|---|---|
| `data/synthesis/statistics.json` | `SynthesisStatistics` (new) | Counts, proportions, breakdowns |

### Stage 11 — thematic-synthesis

| Path | Model | Description |
|---|---|---|
| `data/synthesis/rationale-taxonomy.json` | `RationaleTaxonomy` (new) | Themed categories with quotations |

### Stage 12 — narrative-synthesis

| Path | Model | Description |
|---|---|---|
| `data/synthesis/synthesis-draft.md` | (markdown) | Narrative keyed to research questions |

## Inconsistencies to resolve

### Stage 3 output directory (code change needed)

The implemented code writes to `data/filtered/` with timestamped filenames.
Downstream plans expect `data/screening/screened.jsonl`. Two changes:
(a) rename the output directory from `filtered` to `screening`, and
(b) switch from timestamped filenames to fixed names. The timestamp moves
into the meta sidecar.

### Stage 5 input path (fixed)

The [retrieval spec](full-text-retrieval.md) CLI example previously said
`--input data/screening/included.jsonl`. Already corrected to
`--input data/adjudication/included.jsonl`.

## CLI contract

### Existing subcommands

```sh
# Stage 1 — search
laglitsynth fetch-publications QUERY \
    [-o OUTPUT] [--from-year YEAR] [--to-year YEAR] [--max-records N]

# Stage 3 — screen-abstracts
laglitsynth filter-abstracts INPUT PROMPT \
    [-o OUTPUT] [--model MODEL] [--threshold N] [--base-url URL] \
    [--reject-file PATH] [--max-records N] [--dry-run]
```

These use positional arguments. All new subcommands use `--input` /
`--output-dir` keyword flags instead. The existing commands should be
harmonized to keyword flags when stage 3 is updated for the path change.
No backwards compatibility constraints
([AGENTS.md](../AGENTS.md)).

### Planned subcommands

```sh
# Stage 2 — deduplication
laglitsynth deduplicate \
    --input data/openalex/combined.jsonl \
    --output-dir data/dedup/

# Stage 4 — adjudication (screening)
laglitsynth adjudicate-screening \
    --input data/screening/screened.jsonl \
    --output-dir data/adjudication/

# Stage 5 — full-text retrieval
laglitsynth retrieve \
    --input data/adjudication/included.jsonl \
    --output-dir data/fulltext/ \
    --email EMAIL \
    [--manual-dir DIR] [--skip-existing] [--dry-run]

# Stage 6 — full-text extraction
laglitsynth extract \
    --pdf-dir data/fulltext/pdfs/ \
    --output-dir data/fulltext/ \
    --grobid-url URL \
    [--skip-existing]

# Stage 7 — eligibility
laglitsynth assess-eligibility \
    --catalogue data/adjudication/included.jsonl \
    --extractions data/fulltext/extraction.jsonl \
    --output-dir data/eligibility/ \
    [--skip-existing]

# Stage 8 — data extraction
laglitsynth extract-data \
    --catalogue data/eligibility/eligible.jsonl \
    --extractions data/fulltext/extraction.jsonl \
    --output-dir data/extraction/ \
    [--skip-existing]

# Stage 9 — adjudication (extraction)
laglitsynth adjudicate-extraction \
    --input data/extraction/records.jsonl \
    --output-dir data/adjudication-extraction/

# Stage 10 — quantitative synthesis
laglitsynth synthesize-quantitative \
    --input data/adjudication-extraction/validated.jsonl \
    --output-dir data/synthesis/

# Stage 11 — thematic synthesis
laglitsynth synthesize-thematic \
    --input data/adjudication-extraction/validated.jsonl \
    --output-dir data/synthesis/

# Stage 12 — narrative synthesis
laglitsynth synthesize-narrative \
    --statistics data/synthesis/statistics.json \
    --taxonomy data/synthesis/rationale-taxonomy.json \
    --output-dir data/synthesis/
```

### End-to-end sequence

A complete pipeline run with manual steps noted.

```sh
# 1. Search (repeat for different keyword sets)
laglitsynth fetch-publications "lagrangian particle tracking" \
    -o data/openalex/search_a.jsonl
laglitsynth fetch-publications "ocean tracer simulation" \
    -o data/openalex/search_b.jsonl

# Manual: concatenate search results
cat data/openalex/search_*.jsonl > data/openalex/combined.jsonl

# 2. Deduplicate
laglitsynth deduplicate \
    --input data/openalex/combined.jsonl \
    --output-dir data/dedup/

# 3. Screen abstracts
laglitsynth filter-abstracts data/dedup/deduplicated.jsonl \
    "Is this about computational Lagrangian methods in oceanography?" \
    -o data/screening/screened.jsonl \
    --reject-file data/screening/rejected.jsonl

# 4. Adjudicate screening (pass-through in prototype)
laglitsynth adjudicate-screening \
    --input data/screening/screened.jsonl \
    --output-dir data/adjudication/

# 5. Retrieve full texts
laglitsynth retrieve \
    --input data/adjudication/included.jsonl \
    --output-dir data/fulltext/ \
    --email user@example.com \
    --skip-existing

# Manual: download unretrieved PDFs from data/fulltext/unretrieved.txt
# Place them in data/fulltext/manual/ named by OpenAlex work ID
# Then re-run retrieval to pick up manual PDFs

# 6. Extract full text via GROBID
# Manual: start GROBID container first
laglitsynth extract \
    --pdf-dir data/fulltext/pdfs/ \
    --output-dir data/fulltext/ \
    --grobid-url http://localhost:8070

# 7. Assess eligibility
laglitsynth assess-eligibility \
    --catalogue data/adjudication/included.jsonl \
    --extractions data/fulltext/extraction.jsonl \
    --output-dir data/eligibility/

# 8. Extract data against codebook
laglitsynth extract-data \
    --catalogue data/eligibility/eligible.jsonl \
    --extractions data/fulltext/extraction.jsonl \
    --output-dir data/extraction/

# 9. Adjudicate extraction (pass-through in prototype)
laglitsynth adjudicate-extraction \
    --input data/extraction/records.jsonl \
    --output-dir data/adjudication-extraction/

# 10. Quantitative synthesis
laglitsynth synthesize-quantitative \
    --input data/adjudication-extraction/validated.jsonl \
    --output-dir data/synthesis/

# 11. Thematic synthesis
laglitsynth synthesize-thematic \
    --input data/adjudication-extraction/validated.jsonl \
    --output-dir data/synthesis/

# 12. Narrative synthesis
laglitsynth synthesize-narrative \
    --statistics data/synthesis/statistics.json \
    --taxonomy data/synthesis/rationale-taxonomy.json \
    --output-dir data/synthesis/
```

## Model dependency graph

### Existing models

| Model | Module | Used by stages |
|---|---|---|
| [`_Base`](../src/laglitsynth/models.py) | `laglitsynth.models` | All (base class) |
| [`Work`](../src/laglitsynth/openalex/models.py) | `laglitsynth.openalex.models` | 1, 2, 3, 4, 5, 7, 8 |
| [`FetchMeta`](../src/laglitsynth/openalex/models.py) | `laglitsynth.openalex.models` | 1 |
| [`FilterVerdict`](../src/laglitsynth/llmfilter/models.py) | `laglitsynth.llmfilter.models` | 3 |
| [`FilterMeta`](../src/laglitsynth/llmfilter/models.py) | `laglitsynth.llmfilter.models` | 3 |

### New models needed

| Model | Planned module | Stage | Defined in |
|---|---|---|---|
| `DeduplicationMeta` | `laglitsynth.dedup.models` | 2 | [deduplication.md](deduplication.md) |
| `AdjudicationMeta` | `laglitsynth.adjudication.models` | 4 | [adjudication-screening.md](adjudication-screening.md) |
| `RetrievalStatus` (enum) | `laglitsynth.fulltext.models` | 5 | [full-text-retrieval.md](full-text-retrieval.md) |
| `RetrievalRecord` | `laglitsynth.fulltext.models` | 5 | [full-text-retrieval.md](full-text-retrieval.md) |
| `RetrievalMeta` | `laglitsynth.fulltext.models` | 5 | [full-text-retrieval.md](full-text-retrieval.md) |
| `TextSection` | `laglitsynth.fulltext.models` | 6 | [full-text-extraction.md](full-text-extraction.md) |
| `ExtractedDocument` | `laglitsynth.fulltext.models` | 6, 7, 8 | [full-text-extraction.md](full-text-extraction.md) |
| `ExtractionMeta` | `laglitsynth.fulltext.models` | 6 | [full-text-extraction.md](full-text-extraction.md) |
| `EligibilityVerdict` | `laglitsynth.eligibility.models` | 7 | [eligibility.md](eligibility.md) |
| `EligibilityMeta` | `laglitsynth.eligibility.models` | 7 | [eligibility.md](eligibility.md) |
| `ExtractionRecord` | `laglitsynth.extraction.models` | 8, 9, 10, 11 | Not yet defined. Must encode the [codebook](codebook.md) fields. |
| `DataExtractionMeta` | `laglitsynth.extraction.models` | 8 | Not yet defined |
| `ExtractionAdjudicationMeta` | `laglitsynth.adjudication.models` | 9 | [adjudication-extraction.md](adjudication-extraction.md) |
| `SynthesisStatistics` | `laglitsynth.synthesis.models` | 10 | Not yet planned |
| `RationaleTaxonomy` | `laglitsynth.synthesis.models` | 11 | Not yet planned |

### Per-stage import summary

| Stage | Reads | Writes |
|---|---|---|
| 1. search | — | Work, FetchMeta |
| 2. deduplication | Work | Work, DeduplicationMeta |
| 3. screen-abstracts | Work | Work, FilterVerdict, FilterMeta |
| 4. adjudication | Work | Work, AdjudicationMeta |
| 5. retrieval | Work | RetrievalRecord, RetrievalMeta |
| 6. extraction | (PDFs) | ExtractedDocument, ExtractionMeta |
| 7. eligibility | Work, ExtractedDocument | Work, EligibilityVerdict, EligibilityMeta |
| 8. data-extraction | Work, ExtractedDocument | ExtractionRecord, DataExtractionMeta |
| 9. adjudication (extr.) | ExtractionRecord | ExtractionRecord, ExtractionAdjudicationMeta |
| 10. quant-synthesis | ExtractionRecord | SynthesisStatistics |
| 11. thematic-synthesis | ExtractionRecord | RationaleTaxonomy |
| 12. narrative-synthesis | SynthesisStatistics, RationaleTaxonomy | (markdown) |

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
- `SynthesisStatistics` — depends on which breakdowns are needed (by
  sub-discipline, by year, by source basis).
- `RationaleTaxonomy` — depends on how thematic clusters are represented
  (category label, supporting quotes, paper references).
