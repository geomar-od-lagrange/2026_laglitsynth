# Codebook development

The codebook defines what to extract from each paper and how. It is the
schema for stage 8 (data extraction) and the input to stages 10–12
(synthesis). The codebook does not exist yet — it must be built
iteratively from the actual corpus.

## Approach: LLM-first pilot, human-tuned later

The codebook is developed in three phases. The first two phases happen
during end-to-end prototyping — they produce a working but unvalidated
codebook that lets the full pipeline run. The third phase happens when
humans review the pipeline's output and tune the codebook based on what
they find.

### Phase 1 — Seed codebook

Write an initial codebook based on domain knowledge. This is a best guess,
not a literature survey. The goal is a schema that an LLM can fill, not a
schema that is correct.

The seed codebook defines fields, types, and rough decision rules. Free-text
fields are preferred over enums at this stage — premature enumeration is the
main risk. Let the LLM use the vocabulary it finds in the papers; collapse
into categories later.

### Phase 2 — LLM pilot extraction

Run the LLM against 15–20 papers spanning the three sub-disciplines (water
parcels, tracers, objects) and varying in documentation quality. The LLM
fills all codebook fields. Output is a JSONL of extraction records — one
per paper.

This is not a one-off experiment. It is the first real run of stage 8. The
extraction records flow into stages 10–12 and produce (likely poor) synthesis
output. The value is that the full pipeline stands end to end, even if the
results are unvalidated.

No human review happens in this phase. The extraction records are artifacts
to be reviewed later, not ground truth.

### Phase 3 — Human review and codebook tuning

A human reviews the pilot extraction records against the source papers.
The review answers three questions per paper:

1. **Correctness.** Did the LLM extract the right values?
2. **Completeness.** Did the LLM miss information that was in the paper?
3. **Schema fitness.** Does the codebook have the right fields, at the right
   granularity, with clear enough decision rules?

Question 3 is the important one. The corrections from questions 1 and 2
improve the extraction records, but the schema-fitness observations improve
the codebook itself. Common findings:

- A field is too broad (e.g. `integration_scheme` conflates the algorithm
  and its order).
- A field is missing (e.g. papers mention boundary conditions but the
  codebook has no field for it).
- A decision rule is ambiguous (e.g. when does `methods_detail` count as
  `sufficient` vs. `partial`?).
- An enum is premature (the free-text values cluster into categories the
  seed codebook didn't anticipate).

After review, revise the codebook and re-run extraction. The
extract–review–revise loop converges when the human reviewer stops finding
schema problems (correctness and completeness errors are expected and
handled by adjudication, stage 9).

## Seed codebook fields

Derived from the research questions and domain terms in the
[glossary](../docs/glossary.md).

### Identification

| Field | Type | Notes |
|---|---|---|
| work_id | str | OpenAlex ID |
| source_basis | enum | `full_text`, `abstract_only` |
| sub_discipline | free text → enum | `water_parcels`, `tracers`, `objects`, `multiple`. Free text in pilot. |

### RQ1.2 — Numerical choices

| Field | Type | Notes |
|---|---|---|
| integration_scheme | free text | e.g. "Euler forward", "RK4", "RK45 adaptive". Do not force categories yet. |
| time_step_strategy | free text | e.g. "fixed", "adaptive CFL-based", "sub-stepped within ocean model step". |
| time_step_value | str or None | e.g. "300s", "1h", "variable". Separate from strategy — some papers state only the value. |
| interpolation_spatial | free text | e.g. "trilinear", "bicubic + linear vertical". |
| interpolation_temporal | free text | e.g. "linear between snapshots", "none". Often unstated. |
| diffusion_scheme | free text or None | e.g. "random walk", "Markov-1", "none". May not apply to all sub-disciplines. |
| software | free text or None | e.g. "OceanParcels v2.4", "OpenDrift", "custom Fortran". |
| ocean_model | free text or None | e.g. "NEMO ORCA025", "MITgcm". Context for interpreting numerical choices. |

### RQ1.1 — Reproducibility

| Field | Type | Notes |
|---|---|---|
| methods_detail | free text → enum | How thoroughly numerical methods are described. Decision rules TBD in phase 3. |
| code_available | free text → enum | e.g. "yes, GitHub link", "named but not linked", "not mentioned". |
| config_available | free text → enum | Whether runtime parameters (domain, resolution, forcing) are given. |

### RQ1.3 — Rationale

| Field | Type | Notes |
|---|---|---|
| rationale_integration | free text or None | Why this integration scheme? Verbatim quote or close paraphrase. |
| rationale_timestep | free text or None | Why this time step? |
| rationale_interpolation | free text or None | Why this interpolation? |
| rationale_other | free text or None | Rationale that does not map to a specific numerical choice. |

### Extraction metadata

| Field | Type | Notes |
|---|---|---|
| passage_locations | free text | Where in the paper the information was found. Becomes ground truth for the two-pass extraction pattern. |
| extraction_notes | free text | What was ambiguous, surprising, or hard to classify. Human-written in phase 3; LLM can attempt in phase 2 but this field is primarily for human review. |

## Design decisions

### Free text over enums in the pilot

Most fields are free text during phases 1 and 2. Enums are introduced in
phase 3 when the actual vocabulary is known. This avoids the main
codebook-design failure mode: categories that do not match how papers
actually describe their methods.

### One rationale field per numerical choice

The glossary defines rationale as "the reason a paper gives for a
particular numerical choice." Separating by choice type makes thematic
synthesis (stage 11) cleaner — integration rationales cluster separately
from time-step rationales.

### passage_locations is load-bearing

This field records where in the paper each piece of information was found.
It serves two purposes:

- **Validation.** A human reviewer can check the LLM's extraction against
  the source text without re-reading the whole paper.
- **Two-pass calibration.** The passage locations from the pilot become the
  ground truth for evaluating whether pass 1 (locate relevant passages)
  of the [two-pass extraction](two-pass-extraction.md) pattern is finding
  the right text.

### The codebook is a living document

The seed codebook will be wrong. Fields will be added, removed, split, or
merged during phase 3. The Pydantic model for the extraction record must
be updated to match. This is fine — the project has no backwards
compatibility constraints.

## What this plan does not cover

- The Pydantic model for the extraction record. That is defined when the
  codebook stabilises after phase 3.
- The LLM prompt for extraction. That is part of the stage 8 plan, not the
  codebook plan.
- Adjudication workflow (stage 9). Adjudication consumes the codebook but
  does not change it.
- Enumeration of allowed values for enum fields. That is the output of
  phase 3, not an input.
