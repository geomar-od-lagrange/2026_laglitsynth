# Stages 7–8: full-text-only basis + human-review exports

## Goal

Stages 7 ([fulltext-eligibility](../../src/laglitsynth/fulltext_eligibility/eligibility.py))
and 8 ([extraction-codebook](../../src/laglitsynth/extraction_codebook/extract.py))
currently fall back to a work's abstract when no full text is available.
That fallback makes no sense for these stages: their whole point is to
assess and extract details — eligibility against the full criteria, then
numerical-method specifics — that abstracts do not carry. This plan removes
the abstract basis from both stages (works without full text are simply not
processed, not flagged with a sentinel), tears out the now-dead machinery
that the basis distinction threaded through both stages, and adds
`fulltext-eligibility-export` / `extraction-codebook-export` subcommands —
XLSX review workbooks that put a random sampled subset of works each on its
own tab, for human spot-checking the two LLM stages and tuning their
prompts. CSV output is dropped throughout: the JSONL sidecars are the
machine-readable form, and stage 3's CSV — whose only rationale was a
stage-4 ingestor the verdicts-only cutover deleted — is retired here too.

## Non-goals

Stage 3's screening *logic* is unchanged — abstracts are legitimate input
there (only its export's dead CSV path is removed). We do not add a shared
export helper across stages (stage 3's verdict-shaped export and stage 8's
field+context-shaped export diverge enough that sharing buys nothing); each
stage gets its own self-contained `export.py`. The workbooks are explicitly
*not* a wide all-works scan surface: the workflow is a reviewer digging into
a random subset (drawn by `--n-subset` / `--subset-seed`) and generalizing
from those spot-checks, so the per-work tab is the working surface and the
overview sheet is a light navigation index, not a triage table. We do not
change retrieval, extraction (stage 6), or how the "what is still missing"
gap is surfaced — that already lives in stage 5's `missing` provenance
records and the export gap bundle.

## Target state

### Stage 7: full text or nothing

[`_assess_one`](../../src/laglitsynth/fulltext_eligibility/eligibility.py) drops
its abstract fallback (step 2) and the empty-TEI fall-through. A work is
assessed only when an `ExtractedDocument` exists for it and its TEI renders
non-empty; the active set fed to the LLM is gated on extraction presence, so
works without full text never enter the loop and produce **no verdict row,
and are not counted** (per "don't flag them explicitly" — what reaches the
stage is the only number that matters; the gap is already visible upstream).
Malformed TEI on a work that *does* have an extraction still records a
`tei-parse-failure` sentinel: that signals broken extraction on a paper we
do have a PDF for, which is worth surfacing, unlike a plain absence. The
`llm-parse-failure` / `llm-timeout` sentinels are unchanged.

With every remaining verdict now full-text-based, `SourceBasis` and the
`source_basis` field disappear from `EligibilityVerdict`
([models.py](../../src/laglitsynth/fulltext_eligibility/models.py)); the
`no-source` reason and its `no_source_count` / `by_source_basis` meta
counters go with them. No skipped-work counter replaces them. The
`source_basis` framing tag in
[prompts.py](../../src/laglitsynth/fulltext_eligibility/prompts.py) collapses:
`USER_TEMPLATE` and `build_user_message` no longer take a basis argument.

### Stage 8: full text or nothing

The same surgery on
[`_extract_one`](../../src/laglitsynth/extraction_codebook/extract.py): drop the
abstract fallback and empty-TEI fall-through; gate the eligible set on
extraction presence; keep `tei-parse-failure` / `llm-parse-failure` /
`llm-timeout`, drop `no-source`. `SourceBasis` and the record's
`source_basis` field go from
[models.py](../../src/laglitsynth/extraction_codebook/models.py) and the
dynamically-built record model
([codebook.py](../../src/laglitsynth/extraction_codebook/codebook.py)); the
`abstract_only_count` / `by_source_basis` meta counters go, `skipped_count`
narrows to `tei-parse-failure` only. `_sentinel_record` loses its
`source_basis` parameter; `build_user_message` /`USER_TEMPLATE` in
[prompts.py](../../src/laglitsynth/extraction_codebook/prompts.py) drop the
basis tag (`CHAR_BUDGET` and `render_fulltext` are unaffected). The
`--catalogue` help text that justifies itself by the abstract fallback is
rewritten — the catalogue is retained for the verdict↔catalogue consistency
join and for the export, not for an abstract source.

### Stage 10 design note

[pipeline.md](../../docs/pipeline.md)'s stage-10 description propagates
uncertainty from a "full-text vs abstract-only" source basis. With
abstract-only extraction gone, that distinction can never arise; the
sentence is removed so a future stage-10 plan does not inherit a dead
requirement.

### Stage 3 export: XLSX only

[screening_abstracts/export.py](../../src/laglitsynth/screening_abstracts/export.py)
loses its `--format` flag and CSV path (`export_review_csv`, `COLUMNS`,
`build_row`) — `screening-abstracts-export` always writes the workbook. The
CSV existed to round-trip `reviewer_decision` / `reviewer_reason` into a
stage-4 ingestor that no longer exists; the JSONL is the machine form and
the XLSX is the human one. This sets the XLSX-only precedent the two new
exports follow.

### `fulltext-eligibility-export`

New `src/laglitsynth/fulltext_eligibility/export.py`, subcommand
`fulltext-eligibility-export`, modelled on the (now CSV-free)
[screening_abstracts/export.py](../../src/laglitsynth/screening_abstracts/export.py):
`--verdicts`, `--catalogue`, `--meta`, `--output`, `--n-subset`,
`--subset-seed`. It writes one workbook: an overview/Index sheet (one row per
sampled work — bibliographic columns plus `eligible` / `llm_reason` inline
and a hyperlink to the tab, as a navigation aid) and one tab per sampled
work carrying the bibliographic block, the eligibility **criterion pulled
verbatim from `eligibility-meta.json`** (the criteria system prompt) so the
reviewer sees the same question the LLM saw, reviewer placeholders
(`reviewer_eligible` / `reviewer_reason`), and a collapsed LLM-verdict block.
Registered in [cli.py](../../src/laglitsynth/cli.py).

### `extraction-codebook-export`

New `src/laglitsynth/extraction_codebook/export.py`, subcommand
`extraction-codebook-export`, same flags. The record shape drives the per-work
tab — ~28 codebook fields, each a value paired with a verbatim `*_context`,
with field names read from the codebook
([CodebookContext](../../src/laglitsynth/extraction_codebook/codebook.py)) /
record model rather than hard-coded (the record is codebook-driven). The
per-work tab is vertical: the bibliographic block, then one row per codebook
field with columns `field | value | context | reviewer_correction`, then a
collapsed LLM-meta block; the codebook prompt/criterion comes from
`extraction-codebook-meta.json`. The overview/Index sheet lists the sampled
works (bibliographic columns + a hyperlink to each tab) for navigation.
Registered in [cli.py](../../src/laglitsynth/cli.py).

## Implementation sequence

Six commits. `pixi run typecheck` and `pixi run test` pass between each.

1. **Stage 7 full-text-only.** Gate the active set on extraction presence;
   drop the abstract branch and empty-TEI fall-through in `_assess_one`;
   remove `SourceBasis` / `source_basis` / `no-source` / the dropped meta
   counters (no replacement counter); simplify `prompts.py`. Tests:
   a work with renderable full text gets a full-text verdict; a work with no
   extraction gets *no row* and is not counted; malformed TEI
   still records `tei-parse-failure`; a work with only an abstract is not
   assessed; `EligibilityVerdict` rejects a `source_basis` key.

2. **Stage 8 full-text-only.** The same on `_extract_one` /
   `_sentinel_record` / models / dynamic record model / `prompts.py`; rewrite
   the `--catalogue` help. Tests: an eligible work with full text extracts; an
   eligible work with no extraction gets no row and is not counted;
   malformed TEI records `tei-parse-failure`; the record model has no
   `source_basis` field; meta has no `abstract_only_count` / `by_source_basis`.

3. **Retire stage 3's CSV.** Drop `--format`, `export_review_csv`, `COLUMNS`,
   and `build_row` from `screening_abstracts/export.py`;
   `screening-abstracts-export` always writes the workbook. Remove the CSV
   tests. Tests: the subcommand runs with no `--format` flag and writes the
   xlsx; passing `--format` is rejected as an unknown argument.

4. **`fulltext-eligibility-export`.** Add `export.py` + subcommand (XLSX
   only); register in `cli.py`. Tests: the overview sheet has one row per
   sampled work with `eligible` / `llm_reason` inline and a tab hyperlink;
   per-work tabs embed the criterion from meta and carry reviewer
   placeholders; `--n-subset` / `--subset-seed` sample reproducibly; a verdict
   whose `work_id` is absent from the catalogue raises a naming error.

5. **`extraction-codebook-export`.** Add `export.py` + subcommand (XLSX only);
   register in `cli.py`. Tests: the per-work tab lists every codebook field
   with value/context/reviewer columns, driven off the codebook (not
   hard-coded) and following a non-default codebook's field set; the overview
   sheet links to each tab; subset/seed and missing-catalogue behave as in
   stage 7.

6. **Docs and roadmap.** Update [eligibility.md](../../docs/eligibility.md) and
   [extraction-codebook.md](../../docs/extraction-codebook.md) (source-basis and
   sentinel vocabulary, new export subcommands), [pipeline.md](../../docs/pipeline.md)
   (stage 7/8 cascade now full-text-only, stage 10 uncertainty note removed),
   [interfaces.md](../../docs/interfaces.md) (`EligibilityVerdict` /
   `ExtractionRecord` fields, meta counters, the XLSX-only export subcommands,
   and stage 3's dropped CSV), [README.md](../../README.md). Update the roadmap's
   deferred "shared CSV-export helper across stages 3, 4, 7, 8, 9" entry to
   record that exports are XLSX-only and 7/8 landed as separate modules by
   design. Move this plan to [plans/done/](.).

## Follow-ups

Stage 9 (`extraction-adjudication`) will read the stage-8 review export's
reviewer columns back in; that ingestion is its own plan, not built here.
The deferred stage-4 reviewer-decision ingestor and any cross-stage export
consolidation stay deferred — doing 7 and 8 as separate modules deliberately
declines to force a shared abstraction before stage 9 shows what it needs.

## Critical files

- [src/laglitsynth/fulltext_eligibility/eligibility.py](../../src/laglitsynth/fulltext_eligibility/eligibility.py)
- [src/laglitsynth/fulltext_eligibility/models.py](../../src/laglitsynth/fulltext_eligibility/models.py)
- [src/laglitsynth/fulltext_eligibility/prompts.py](../../src/laglitsynth/fulltext_eligibility/prompts.py)
- [src/laglitsynth/extraction_codebook/extract.py](../../src/laglitsynth/extraction_codebook/extract.py)
- [src/laglitsynth/extraction_codebook/models.py](../../src/laglitsynth/extraction_codebook/models.py)
- [src/laglitsynth/extraction_codebook/codebook.py](../../src/laglitsynth/extraction_codebook/codebook.py)
- [src/laglitsynth/extraction_codebook/prompts.py](../../src/laglitsynth/extraction_codebook/prompts.py)
- [src/laglitsynth/screening_abstracts/export.py](../../src/laglitsynth/screening_abstracts/export.py)
- [src/laglitsynth/cli.py](../../src/laglitsynth/cli.py)
- [docs/pipeline.md](../../docs/pipeline.md), [docs/eligibility.md](../../docs/eligibility.md), [docs/extraction-codebook.md](../../docs/extraction-codebook.md), [docs/interfaces.md](../../docs/interfaces.md)
