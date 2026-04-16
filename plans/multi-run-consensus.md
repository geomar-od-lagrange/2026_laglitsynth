# Plan: Multi-run consensus for LLM-driven stages

## Problem

Stages 3 (screen-abstracts), 7 (eligibility), and 8 (data-extraction) use
LLMs that are non-deterministic. Running a stage multiple times on the same
input and combining results improves robustness: agreement signals
confidence, disagreement surfaces cases for human review.

The current data model writes one verdict or record per work. Multiple runs
produce multiple verdicts per work, which need storage, identification, and
a consensus mechanism before downstream stages can consume them.

## Run identity

Each invocation of an LLM-driven stage is a **run**, identified by a
`run_id` (UUID4 prefix or timestamp slug). The run's configuration (model,
prompt, seed) is recorded in the meta sidecar. Verdict and record models
gain a `run_id` field:

```python
class FilterVerdict(_Base):
    work_id: str
    run_id: str                        # new
    relevance_score: int | None = None
    accepted: bool | None = None
    reason: str | None = None
```

The same pattern applies to `EligibilityVerdict` and `ExtractionRecord`.

## Storage layout

Multiple runs append to the same `verdicts.jsonl`. After three screening
runs, `data/screening-abstracts/verdicts.jsonl` contains three `FilterVerdict` lines
per work, distinguished by `run_id`. The meta sidecar becomes a list of
per-run metadata entries.

This keeps storage flat and avoids per-run directory proliferation.
Downstream consumers read a single file; `run_id` is the discriminator.

## Consensus mechanisms

### Stages 3 and 7 — scoring and classification

These stages produce a numeric score and a boolean accept/reject. Consensus
is a majority-vote verdict per work:

```python
class ConsensusVerdict(_Base):
    work_id: str
    run_ids: list[str]
    mean_score: float
    accept_count: int
    reject_count: int
    consensus_accepted: bool           # majority vote
    unanimous: bool                    # all runs agree
```

- Ties (even run count, equal accept/reject) are flagged for adjudication.
- Non-unanimous works are priority candidates for human review (stages 4/9).
- `mean_score` supports threshold sensitivity analysis and borderline
  ranking.

### Stage 8 — data extraction

Free-text fields make automatic merging unreliable. The strategy is
**field-level agreement checking**:

```python
class FieldAgreement(_Base):
    field_name: str
    values: list[str | None]           # one value per run
    agreed: bool                       # identical after normalization

class ExtractionConsensus(_Base):
    work_id: str
    run_ids: list[str]
    field_agreements: list[FieldAgreement]
    all_agreed: bool
```

- `agreed` if all non-None values match after whitespace normalization and
  case folding.
- Disagreeing fields are preserved for human adjudication (stage 9). No
  automatic "best of N" selection for free text.

## Downstream interface

Downstream stages consume consensus output, not individual runs. The
consensus step emits one resolved record per work:

- Stages 3/7: one `ConsensusVerdict` per work. Individual run verdicts
  stay in `verdicts.jsonl` for audit.
- Stage 8: one `ExtractionRecord` per work, using agreed values where runs
  converge. Disagreeing fields are populated from the first run and flagged
  via a companion `ExtractionConsensus` record for stage 9.

This preserves the pipeline's contract: downstream stages still read one
record per work.

## Interaction with --skip-existing

- **Within a run:** `--skip-existing` skips works with a verdict matching
  the current `run_id`. This supports resuming an interrupted run.
- **Across runs:** a new `run_id` has no existing matches, so all works
  are processed.
- **Consensus:** always recomputed from all verdicts in the file. Not
  subject to `--skip-existing` (deterministic aggregation, no LLM call).

## CLI sketch

```sh
# Three screening runs
laglitsynth screening-abstracts --input data/catalogue-dedup/deduplicated.jsonl \
    --prompt "..." --output-dir data/screening-abstracts/ --run-id run_a
laglitsynth screening-abstracts --input ... --run-id run_b
laglitsynth screening-abstracts --input ... --run-id run_c

# Compute consensus (stage-aware: majority-vote vs. field-agreement)
laglitsynth consensus --input data/screening-abstracts/verdicts.jsonl \
    --output-dir data/screening-abstracts/ --stage screen-abstracts
```

`--run-id` is optional; if omitted, a UUID4 prefix is generated. The
`consensus` subcommand selects the appropriate mechanism via `--stage`.

The multi-run model also supports ensemble approaches (different models or
prompts per run). The consensus mechanism is the same — it requires only
that runs produce the same output schema, not that they share configuration.

## Open questions

- **Run count.** Three is natural for majority vote, but the right number
  depends on inter-run variance. Must be determined empirically.
- **Weighted consensus.** Treating all runs equally is the starting point.
  Weighting by model quality requires calibration data that does not exist.
- **Free-text near-misses.** Exact match after normalization will flag
  "RK4" vs. "4th-order Runge-Kutta" as disagreement. A similarity check
  could help but adds complexity. Defer until empirical data shows
  frequency.
- **Context snippets.** Different runs may select different verbatim
  passages. Unclear whether to treat this as disagreement or complementary
  evidence. Likely: keep all distinct snippets for adjudication.
