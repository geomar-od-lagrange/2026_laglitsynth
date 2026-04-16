# Interface contracts

Cross-cutting view of the pipeline's artifacts, CLI commands, and Pydantic
models. Makes the input/output chain across all 12 stages explicit so that
each stage can be implemented independently.

## Design principle: flag, don't filter

Gate stages (3, 4, 7) write verdict sidecars but never copy or split Work
records. The deduplicated catalogue (`data/dedup/deduplicated.jsonl`) is
the single source of Work records for the entire pipeline. Downstream
stages determine their active work set at read time by joining the
catalogue against upstream verdicts and thresholds.

This means re-thresholding (e.g. changing the screening threshold from 50
to 40) does not require re-running the LLM or re-splitting files. The
scores already exist in the verdict sidecar. Downstream stages re-run
with `--skip-existing` and only process newly-included works.

See [retuning-propagation.md](../plans/retuning-propagation.md) for the
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

### Stage 1 — search *(exists)*

| Path | Model | Description |
|---|---|---|
| `data/openalex/<slug>_<ts>.jsonl` | [`Work`](../src/laglitsynth/openalex/models.py) | Retrieved catalogue records |
| `data/openalex/<slug>_<ts>.meta.json` | [`FetchMeta`](../src/laglitsynth/openalex/models.py) | Per-run provenance |

Multiple search runs produce separate timestamped files. Only the JSONL
records are concatenated for stage 2 (`cat data/openalex/*.jsonl`). The
meta files stay as per-run provenance records — they are not merged.

### Stage 2 — deduplication

| Path | Model | Description |
|---|---|---|
| `data/dedup/deduplicated.jsonl` | [`Work`](../src/laglitsynth/openalex/models.py) | Deduplicated catalogue — single source of Work records for the pipeline |
| `data/dedup/dropped.jsonl` | Work + merge reason (TBD) | Dropped duplicates with reason |
| `data/dedup/dedup-meta.json` | `DeduplicationMeta` (new) | Counts by matching rule |

### Stage 3 — screen-abstracts *(exists, needs update)*

| Path | Model | Description |
|---|---|---|
| `data/screening/verdicts.jsonl` | [`FilterVerdict`](../src/laglitsynth/llmfilter/models.py) | Relevance score and reason for every work |
| `data/screening/screening-meta.json` | [`FilterMeta`](../src/laglitsynth/llmfilter/models.py) | Prompt, model, counts |

Verdicts cover all works in the deduplicated catalogue, not just accepted
ones. The accept/reject decision is derived from the relevance score and
the threshold passed to the resolve module at read time. No `screened.jsonl` or
`rejected.jsonl` — the verdict sidecar is the only output.

The existing code writes to `data/filtered/` with timestamped filenames
and splits accepted/rejected Work records into separate files. Both must
change: rename the directory to `data/screening/`, write only verdicts,
drop the Work-record split.

### Stage 4 — adjudication (screening)

| Path | Model | Description |
|---|---|---|
| `data/adjudication/verdicts.jsonl` | `AdjudicationVerdict` (new) | Human overrides (accept/reject/skip per work) |
| `data/adjudication/adjudication-meta.json` | `AdjudicationMeta` (new) | Mode, counts |

The adjudication tool resolves the current accepted set (screening scores
above threshold) and presents unreviewed works for human judgment. Human
decisions are recorded as verdicts, not as copies of Work records.

### Stage 5 — full-text-retrieval

| Path | Model | Description |
|---|---|---|
| `data/fulltext/retrieval.jsonl` | `RetrievalRecord` (new) | Per-work retrieval outcome and PDF location |
| `data/fulltext/retrieval-meta.json` | `RetrievalMeta` (new) | Counts by source |
| `data/fulltext/pdfs/<work_id>.pdf` | (binary) | Raw PDFs |
| `data/fulltext/unretrieved.txt` | (plain text) | DOIs for manual download |

`RetrievalRecord` flags each work's retrieval status (success, failed,
abstract-only) and the PDF path. Already follows the flag pattern.

### Stage 6 — full-text-extraction

| Path | Model | Description |
|---|---|---|
| `data/fulltext/extraction.jsonl` | `ExtractedDocument` (new) | Structured sections per work |
| `data/fulltext/extraction-meta.json` | `ExtractionMeta` (new) | GROBID version, counts |
| `data/fulltext/tei/<work_id>.tei.xml` | (XML) | Raw GROBID output |

### Stage 7 — eligibility

| Path | Model | Description |
|---|---|---|
| `data/eligibility/verdicts.jsonl` | `EligibilityVerdict` (new) | Per-work eligibility decision |
| `data/eligibility/eligibility-meta.json` | `EligibilityMeta` (new) | Counts by source basis |

Verdicts cover all works that the resolve module passes to this stage.
No `eligible.jsonl` — downstream stages resolve the eligible set from
the verdict sidecar.

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
| `data/adjudication-extraction/corrections.jsonl` | `ExtractionCorrection` (new) | Per-field corrections with original and corrected values |
| `data/adjudication-extraction/adjudication-meta.json` | `ExtractionAdjudicationMeta` (new) | Mode, counts, agreement metrics |

Corrections are stored alongside original extraction records, not as
replacements. Downstream stages apply corrections at read time.

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

### Stage 3 implementation (code change needed)

The existing code writes to `data/filtered/`, uses timestamped filenames,
and splits Work records into accepted/rejected files. All three must
change: rename directory to `data/screening/`, write only
`verdicts.jsonl` (scores for all works), drop the Work-record split.

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
harmonized to keyword flags when stage 3 is updated. No backwards
compatibility constraints ([AGENTS.md](../AGENTS.md)).

### Planned subcommands

```sh
# Stage 2 — deduplication
laglitsynth deduplicate \
    --input data/openalex/combined.jsonl \
    --output-dir data/dedup/

# Stage 4 — adjudication (screening)
laglitsynth adjudicate-screening \
    --data-dir data/ \
    --output-dir data/adjudication/

# Stage 5 — full-text retrieval
laglitsynth retrieve \
    --data-dir data/ \
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
    --data-dir data/ \
    --extractions data/fulltext/extraction.jsonl \
    --output-dir data/eligibility/ \
    [--skip-existing]

# Stage 8 — data extraction
laglitsynth extract-data \
    --data-dir data/ \
    --extractions data/fulltext/extraction.jsonl \
    --output-dir data/extraction/ \
    [--skip-existing]

# Stage 9 — adjudication (extraction)
laglitsynth adjudicate-extraction \
    --data-dir data/ \
    --output-dir data/adjudication-extraction/

# Stage 10 — quantitative synthesis
laglitsynth synthesize-quantitative \
    --data-dir data/ \
    --output-dir data/synthesis/

# Stage 11 — thematic synthesis
laglitsynth synthesize-thematic \
    --data-dir data/ \
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

# Manual: concatenate search result records (not meta files)
cat data/openalex/search_*.jsonl > data/openalex/combined.jsonl

# 2. Deduplicate
laglitsynth deduplicate \
    --input data/openalex/combined.jsonl \
    --output-dir data/dedup/

# 3. Screen abstracts (scores all works, no split)
laglitsynth filter-abstracts \
    --input data/dedup/deduplicated.jsonl \
    --prompt "Is this about computational Lagrangian methods in oceanography?" \
    --output-dir data/screening/

# 4. Adjudicate screening (pass-through in prototype)
laglitsynth adjudicate-screening \
    --data-dir data/ \
    --output-dir data/adjudication/

# 5. Retrieve full texts (resolve determines which works to retrieve)
laglitsynth retrieve \
    --data-dir data/ \
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
    --data-dir data/ \
    --extractions data/fulltext/extraction.jsonl \
    --output-dir data/eligibility/

# 8. Extract data against codebook
laglitsynth extract-data \
    --data-dir data/ \
    --extractions data/fulltext/extraction.jsonl \
    --output-dir data/extraction/

# 9. Adjudicate extraction (pass-through in prototype)
laglitsynth adjudicate-extraction \
    --data-dir data/ \
    --output-dir data/adjudication-extraction/

# 10. Quantitative synthesis
laglitsynth synthesize-quantitative \
    --data-dir data/ \
    --output-dir data/synthesis/

# 11. Thematic synthesis
laglitsynth synthesize-thematic \
    --data-dir data/ \
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
| [`Work`](../src/laglitsynth/openalex/models.py) | `laglitsynth.openalex.models` | 1, 2, 3 |
| [`FetchMeta`](../src/laglitsynth/openalex/models.py) | `laglitsynth.openalex.models` | 1 |
| [`FilterVerdict`](../src/laglitsynth/llmfilter/models.py) | `laglitsynth.llmfilter.models` | 3 |
| [`FilterMeta`](../src/laglitsynth/llmfilter/models.py) | `laglitsynth.llmfilter.models` | 3 |

### New models needed

| Model | Planned module | Stage | Defined in |
|---|---|---|---|
| `DeduplicationMeta` | `laglitsynth.dedup.models` | 2 | [deduplication.md](deduplication.md) |
| `AdjudicationVerdict` | `laglitsynth.adjudication.models` | 4 | — |
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
| `ExtractionCorrection` | `laglitsynth.adjudication.models` | 9 | — |
| `ExtractionAdjudicationMeta` | `laglitsynth.adjudication.models` | 9 | [adjudication-extraction.md](adjudication-extraction.md) |
| `SynthesisStatistics` | `laglitsynth.synthesis.models` | 10 | Not yet planned |
| `RationaleTaxonomy` | `laglitsynth.synthesis.models` | 11 | Not yet planned |

### Per-stage import summary

| Stage | Reads | Writes |
|---|---|---|
| 1. search | — | Work, FetchMeta |
| 2. deduplication | Work | Work, DeduplicationMeta |
| 3. screen-abstracts | Work | FilterVerdict, FilterMeta |
| 4. adjudication | FilterVerdict (via resolve) | AdjudicationVerdict, AdjudicationMeta |
| 5. retrieval | Work (via resolve) | RetrievalRecord, RetrievalMeta |
| 6. extraction | (PDFs) | ExtractedDocument, ExtractionMeta |
| 7. eligibility | Work, ExtractedDocument (via resolve) | EligibilityVerdict, EligibilityMeta |
| 8. data-extraction | Work, ExtractedDocument (via resolve) | ExtractionRecord, DataExtractionMeta |
| 9. adjudication (extr.) | ExtractionRecord (via resolve) | ExtractionCorrection, ExtractionAdjudicationMeta |
| 10. quant-synthesis | ExtractionRecord (via resolve) | SynthesisStatistics |
| 11. thematic-synthesis | ExtractionRecord (via resolve) | RationaleTaxonomy |
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
- `AdjudicationVerdict` — human accept/reject/skip per work with reason.
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
