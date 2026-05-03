# Simplification pass 2 — 2026-05-03

A second, more aggressive pass over the same code as
[simplification-pass.md](simplification-pass.md). The first pass was
scope-bounded — it accepted the 12-stage decomposition, the per-stage
`*Meta` framework, the `cli.py` + 9 subparsers shape, mypy-strict, and
Pydantic everywhere as givens. This pass questions all of that. The
mandate is the AGENTS.md line: "this is a research tool, not a
product. Robustness matters, over-engineering doesn't."

I read every implemented stage, the test suite, both driver scripts,
and the doc set before writing. Where the first pass agreed with my
read, I link rather than re-argue. Where I disagree, I say so.

## Top-line position

The pipeline has the architecture of a productised system (subcommand
tree, per-stage Pydantic meta sidecars, dynamic-payload model loaded
from YAML, run-id-namespaced output trees, 1.2:1 test-to-source ratio,
mypy strict) and the user base of a one-developer prototype. That
mismatch is the source of most of the slack. The first pass picked
off symptoms (six counter loops here, two `render_fulltext` there).
This pass goes after the framework.

Three architectural moves; each subsumes a chunk of the first pass:

1. **Kill the `*Meta` sidecar framework.** Eight Pydantic classes
   (~150 LOC of model + ~80 LOC of construction) collapse to one
   helper that writes `provenance.json` with stage-specific keys.
2. **Collapse stages 2 and 4 into call sites of stages 3 and 5.** No
   more pass-through subcommands; no more `included.jsonl` /
   `eligible.jsonl` / `deduplicated.jsonl` files whose only consumer
   is the next stage.
3. **Replace the `cli.py` + 9 subparsers + 9 `build_subparser` /
   `run` pairs with one `pipeline.py` that exposes functions and a
   thin wrapper script per stage.** This is more honest about what
   the system is — a small Python library a single user drives — and
   removes hundreds of lines of argparse boilerplate.

Combined with the obvious wins the first pass already identified, the
source goes from 4480 LOC to well under 3000. The test suite shrinks
proportionally because half the tests are testing argparse wiring,
counter arithmetic, and the meta framework.

## What the first pass got right (link, don't repeat)

I agree with all the "obvious win" items in
[simplification-pass.md §2.2-2.7 and §3.1](simplification-pass.md). Do
them. Specifically:

- One `render_fulltext` in [tei.py](../../src/laglitsynth/fulltext_extraction/tei.py)
  ([§2.2](simplification-pass.md#22-two-render_fulltext-helpers-obvious-win)).
- Sentinel reasons as a `Literal`
  ([§2.3](simplification-pass.md#23-sentinel-reasons-as-bare-string-literals-obvious-win)).
- One `Counter` pass per stage
  ([§2.4](simplification-pass.md#24-six-separate-sum1-for--passes-per-llm-stage-obvious-win)).
- Drop `_RateLimiter` class and `_AllAttemptsFailedError`
  ([§3.1](simplification-pass.md#31-fulltext_retrievalretrievepy-475-loc-judgment-partial-win)).

These are all non-controversial; just commit them. Some of them get
subsumed by the moves below (`make_run_meta` is moot if `*Meta` dies)
but for any not subsumed, do the small fix.

## Where I disagree with the first pass

### "Build the resolve module" is the wrong answer

The first pass [§1.1](simplification-pass.md#11-the-flag-dont-filter-design-has-been-silently-inverted-judgment)
recommends building a `resolve` module so `flag, don't filter`
becomes real, costing ~150 LOC saved by deleting `included.jsonl` /
`eligible.jsonl`. I think the design principle is the problem, not
the implementations.

`flag, don't filter` exists to make threshold-tuning cheap: re-run a
gate stage with a different threshold, downstream stages re-pick
their input set without recomputing LLM verdicts. But:

- The catalogue is small (the first NESH smoke run was N=10; the
  sample run discussed in `extraction-codebook/prompts.py:CHAR_BUDGET`
  comments was N=57). At this scale, "filter at write time, the
  next stage reads a tiny `included.jsonl`" is operationally
  identical to "join at read time."
- Re-thresholding without re-running LLM stages is a real benefit
  exactly once: when phase 3 codebook review reveals stage 7's
  threshold was wrong. That's a one-shot manual operation, not a
  daily workflow.
- The implementations *already filter*. The doc principle is doc-only.

The cheap move is dropping the principle from the docs and
[interfaces.md](../interfaces.md), keeping the current
filter-at-write-time shape, and not building a `resolve` module at
all. Net new code: zero. Net deleted: the §1.1 paragraph in
[interfaces.md](../interfaces.md), the queued
[multi-run-consensus.md](../../plans/multi-run-consensus.md) plan
that depends on it, and ~30 lines of doc cross-references.

### Stage 4 isn't worth deleting in isolation

The first pass [§1.3](simplification-pass.md#13-stage-4-screening-adjudication-is-a-pass-through-that-produces-only-derivable-state-judgment)
recommends deleting stage 4 once `resolve` lands. I'd go further:
delete stage 4 *now*, fold its logic into stage 5's
[fulltext-retrieval entry point](../../src/laglitsynth/fulltext_retrieval/retrieve.py),
and don't build resolve. Stage 5's `--input` becomes
`--catalogue` + `--screening-verdicts` + `--screening-threshold`,
and the threshold logic from
[adjudicate.py:78-103](../../src/laglitsynth/screening_adjudication/adjudicate.py#L78)
moves into stage 5's input loop (~15 lines).

When real human adjudication arrives, it will be a separate ingest
stage that consumes a reviewer-edited XLSX (per the planned stage
9 shape in [adjudication-extraction.md](../adjudication-extraction.md)),
not a resurrection of this one. The pass-through scaffold isn't
the skeleton of the real thing.

### Stage 2 should disappear, not be replaced with a real implementation

The first pass [§1.2](simplification-pass.md#12-stage-2-catalogue-dedup-is-a-no-op-obvious-win)
proposes either replacing the no-op with a 30-line dedup or
documenting `cat ... > deduplicated.jsonl`. I think it's clearer:
delete the stage. Do dedup inline in
[catalogue-fetch](../../src/laglitsynth/catalogue_fetch/fetch.py) when
multiple `--query` runs land in the same output (DOI normalization
+ title/author/year fallback dict, ~25 lines). The current pipeline
*requires* manual `cat data/catalogue-fetch/*.jsonl >
combined.jsonl` between fetch runs anyway (per
[interfaces.md§artifact-map](../interfaces.md), stage 1 produces
`<slug>_<ts>.jsonl` files); folding dedup into the cat step is
honest. If the user prefers fetch-stays-pure, then dedup is a
2-line shell pipeline: `jq -s 'unique_by(.doi)' ...` or a 15-line
Python one-shot in `scripts/`. It is not a pipeline stage.

This collapses 65 LOC of `dedup.py` + 14 LOC of
[catalogue_dedup/models.py](../../src/laglitsynth/catalogue_dedup/models.py)
+ 93 LOC of [test_catalogue_dedup.py](../../tests/test_catalogue_dedup.py)
+ argparse wiring + a stage subdir. Net: a directory disappears.

### The XLSX exporter belongs in a separate repo or a notebook

The first pass [§3.5](simplification-pass.md#35-screening_abstractsexportpy-624-loc-judgment--confirm)
defers on this pending "has the human reviewer used it?" The answer
hedges around 624 LOC of openpyxl plumbing in a literature-review
pipeline.

This file:

- Has zero LLM, scientific-data, or pipeline-flow content.
- Implements a workbook layout (per-work tab, frozen panes, hyperlink
  navigation, collapsed LLM details, percent-formatted cells) that is
  reviewer UX, designed before any reviewer has used it.
- Is the largest file in the codebase (624 LOC, beating
  [extract.py at 519](../../src/laglitsynth/extraction_codebook/extract.py)).
- Has a 647 LOC test file.

Recommendation: keep
[`export_review_csv`](../../src/laglitsynth/screening_abstracts/export.py#L78)
(~50 LOC), delete everything below
[the XLSX section divider at L107](../../src/laglitsynth/screening_abstracts/export.py#L107),
delete the 26 XLSX-specific tests in
[test_screening_abstracts_export.py](../../tests/test_screening_abstracts_export.py).
When the first reviewer asks "this CSV is hard to navigate, can I
have a workbook?" — that's the trigger, and the right answer might
be a Jupyter notebook in `scripts/` or a tiny separate package, not
624 LOC inside the pipeline package.

Saving: ~600 LOC source + ~600 LOC test.

If the user has *actually* run the XLSX through a reviewer end-to-
end and gotten value, ignore this finding. The first pass's hedge
("if yes, the 600 LOC is invested capital; keep") is right
*conditional on usage*. I'm calling it because pre-prod cleanup
landed in commit `3eaab1c` ("Nesh prod ready") less than a week
ago, suggesting the workflow has not yet had a reviewer pass.

## The three architectural moves

### Move 1: Kill the `*Meta` framework

The current state. Every stage carries a Pydantic class:

| Stage | Model | LOC | Fields beyond `run` (and `llm`) |
|---|---|---|---|
| 1 | [`FetchMeta`](../../src/laglitsynth/catalogue_fetch/models.py#L118) | 6 | `query`, `total_count`, `records_written` |
| 2 | [`DeduplicationMeta`](../../src/laglitsynth/catalogue_dedup/models.py) | 14 | `input_count`, `output_count`, `duplicates_removed`, `by_rule` |
| 3 | [`ScreeningMeta`](../../src/laglitsynth/screening_abstracts/models.py#L17) | 12 | `threshold`, `input_path`, 5 counters, `prompt` |
| 4 | [`AdjudicationMeta`](../../src/laglitsynth/screening_adjudication/models.py#L19) | 8 | `threshold`, 4 counters |
| 5 | [`RetrievalMeta`](../../src/laglitsynth/fulltext_retrieval/models.py#L28) | 7 | `total_works`, 3 counters, `by_source` |
| 6 | [`ExtractionMeta`](../../src/laglitsynth/fulltext_extraction/models.py#L23) | 7 | `grobid_version`, 4 counters |
| 7 | [`EligibilityMeta`](../../src/laglitsynth/fulltext_eligibility/models.py#L66) | 14 | 2 paths, `input_count`, 6 counters, `by_source_basis` |
| 8 | [`ExtractionCodebookMeta`](../../src/laglitsynth/extraction_codebook/models.py#L24) | 14 | 2 paths, `input_count`, 7 counters, `by_source_basis` |

Plus [`RunMeta`](../../src/laglitsynth/models.py#L4) and
[`LlmMeta`](../../src/laglitsynth/models.py#L12) shared, plus the
8 construction sites at the bottom of each stage's `run()` (~15
LOC each), plus the matching counter-arithmetic blocks (~15 LOC
each), plus tests asserting model-validation behaviour, plus a
[whole `data-model.md` section on `*Meta`](../data-model.md#shared-run-level-types).

What does any of this provenance buy?

- `tool` and `tool_version` on every record? No consumer reads
  these. They're "alpha" or a TOOL_NAME constant. Hardcoded in
  every constructor.
- `run_at`? Used nowhere. Useful for "when did this run?" but
  the run-id directory already names the timestamp.
- `validation_skipped`? Surfaced in `read_jsonl` warnings already.
  Logged by the underlying call. Recording it separately is
  belt-and-braces.
- `model`, `temperature`, `prompt_sha256` on LLM stages? The hash
  is the only one with a consumer (the `--skip-existing` guard in
  [eligibility.py:325](../../src/laglitsynth/fulltext_eligibility/eligibility.py#L325)
  and [extract.py:367](../../src/laglitsynth/extraction_codebook/extract.py#L367)).
  Model and temperature are recorded in `config.yaml` already (it's a
  CLI-arg snapshot).
- Per-stage counters? Stderr-logged in the same `run()` block
  that writes the meta. Nobody reads them programmatically.
- `by_source_basis`, `by_rule`, `by_source`? Same.

The pattern is "audit theater": eight stage-shaped Pydantic models
proving the pipeline could be audited, exercised only by the
counter-printout at end of `run()`. The only meta field with a
genuine consumer is `prompt_sha256`.

The replacement. Each stage writes a single `provenance.json` next
to its other outputs. Format is loose JSON — a dict with whatever
keys make sense — using a single helper:

```python
# laglitsynth/provenance.py  (~30 LOC)
def write_provenance(
    run_dir: Path,
    *,
    tool: str,
    counts: Mapping[str, int],
    extra: Mapping[str, Any] | None = None,
) -> None:
    payload = {
        "tool": tool,
        "run_at": _utc_iso_now(),
        **(extra or {}),
        "counts": dict(counts),
    }
    (run_dir / "provenance.json").write_text(json.dumps(payload, indent=2))
```

LLM stages add `"llm": {"model": ..., "prompt_sha256": ...}` via
`extra=`. The hash guard in stages 7 and 8 reads
`json.loads(meta_path.read_text())["llm"]["prompt_sha256"]` —
already how it works (see
[eligibility.py:333](../../src/laglitsynth/fulltext_eligibility/eligibility.py#L333)
and [extract.py:375](../../src/laglitsynth/extraction_codebook/extract.py#L375)),
no Pydantic involvement at the read site today.

What's deleted:

- All eight `*Meta` classes (~85 LOC).
- [`models.py`](../../src/laglitsynth/models.py) (`RunMeta` and
  `LlmMeta` go away).
- All eight `make_run_meta` / `RunMeta(...)` construction sites in
  `run()` (~70 LOC saved across the pipeline).
- [`make_run_meta` helper](simplification-pass.md#26-runmeta-constructor-duplicated-8-times-obvious-win)
  proposed by the first pass (subsumed; never built).
- Tests asserting `model_dump()` shape on each `*Meta`
  (`test_screening_abstracts.py:test_screening_meta_serialization`,
  similar in eligibility, extraction, retrieval, fetch, dedup,
  adjudication tests — six tests, ~150 LOC).
- The "Shared run-level types" / "Per-stage `*Meta` classes"
  sections of [data-model.md](../data-model.md#shared-run-level-types)
  and [interfaces.md](../interfaces.md#shared-meta-shapes).

What survives:

- The counter logic in each `run()` (already simplified by the
  first pass's §2.4 to one `Counter` pass).
- The `prompt_sha256` guard in stages 7 and 8.
- `config.yaml` written via [save_resolved_config](../../src/laglitsynth/config.py#L87) —
  this is the actually-useful provenance file, recording every
  CLI value verbatim.

Migration. Per AGENTS.md ("Green field, no backwards compatibility"):
delete the models, rename `screening-meta.json` /
`eligibility-meta.json` / `extraction-codebook-meta.json` /
`retrieval-meta.json` / `extraction-meta.json` / `dedup-meta.json` /
`adjudication-meta.json` / `*.meta.json` to `provenance.json`
uniformly. One commit, one PR. There is no on-disk meta file from a
prior run that anyone needs to read.

Net change: ~250 LOC source + ~150 LOC test deleted; one
`provenance.py` of ~30 LOC added.

### Move 2: Collapse to 6 stages by deleting passes 2 and 4

The first pass argues stage 4 is scaffolding ([§1.3](simplification-pass.md#13-stage-4-screening-adjudication-is-a-pass-through-that-produces-only-derivable-state-judgment))
and stage 2 is a no-op ([§1.2](simplification-pass.md#12-stage-2-catalogue-dedup-is-a-no-op-obvious-win)),
treating each as a separate decision. Together they're the same
decision: stages whose only product is a moved/copied file are not
stages, they are calls.

The new pipeline:

| New | What it does | Replaces |
|---|---|---|
| 1. catalogue-fetch | OpenAlex query + dedup if multiple `--query` runs share an output | old 1 + 2 |
| 2. screening-abstracts | Score abstracts + emit `verdicts.jsonl` | old 3 |
| 3. fulltext-retrieval | Apply screening threshold inline → fetch PDFs | old 4 + 5 |
| 4. fulltext-extraction | GROBID → TEI | old 6 |
| 5. fulltext-eligibility | LLM eligibility on full text | old 7 |
| 6. extraction-codebook | LLM codebook fill | old 8 |

Future stages 9–12 stay numbered as currently planned, or shift —
either is fine. The pipeline-numbering should stop being a
load-bearing claim about "12 stages": it's a tradition, not a
constraint.

What's deleted:

- [`src/laglitsynth/catalogue_dedup/`](../../src/laglitsynth/catalogue_dedup/)
  (entire subpackage).
- [`src/laglitsynth/screening_adjudication/`](../../src/laglitsynth/screening_adjudication/)
  (entire subpackage).
- [`tests/test_catalogue_dedup.py`](../../tests/test_catalogue_dedup.py)
  and [`tests/test_screening_adjudication.py`](../../tests/test_screening_adjudication.py).
- [`docs/catalogue-dedup.md`](../catalogue-dedup.md) and
  [`docs/screening-adjudication.md`](../screening-adjudication.md).
- The "Stage 2" and "Stage 4" rows from
  [`docs/pipeline.md`](../pipeline.md) (table + section + mermaid
  graph) and [`docs/interfaces.md`](../interfaces.md) (artifact map +
  CLI contract + end-to-end sequence).

What changes:

- [`fulltext_retrieval/retrieve.py:run`](../../src/laglitsynth/fulltext_retrieval/retrieve.py#L320)
  takes `--catalogue`, `--screening-verdicts`, `--screening-threshold`
  and computes the active set inline (the threshold filter from
  [adjudicate.py:78-103](../../src/laglitsynth/screening_adjudication/adjudicate.py#L78)
  is ~15 lines, including the null-score-rides-through behaviour).
  No more `included.jsonl`.
- [`catalogue_fetch/fetch.py`](../../src/laglitsynth/catalogue_fetch/fetch.py)
  optionally accepts a list of queries (`--query` repeatable) and
  dedupes across them. Or — simpler — stays single-query and the
  driver script deduplicates with `jq` if multiple queries are run.
  My preference is the second; fetch should not own dedup logic.
- [`fulltext_eligibility/eligibility.py`](../../src/laglitsynth/fulltext_eligibility/eligibility.py):
  drop the [`eligible.jsonl` rebuild at L455-460](../../src/laglitsynth/fulltext_eligibility/eligibility.py#L455).
  Stage 6 (was 8) takes `--catalogue` + `--eligibility-verdicts` and
  filters at read time. (This is the *one* place "join at read time"
  is worth doing — the codebook stage's input set genuinely is a
  small subset of the catalogue, and threshold-tuning the eligibility
  stage is plausible.)

Net change: ~250 LOC source + ~350 LOC test deleted; ~25 LOC of
threshold-filter code added to retrieve.py.

### Move 3: Replace `cli.py` + 9 subparsers with one `pipeline.py`

The current shape ([cli.py](../../src/laglitsynth/cli.py)) is one
top-level dispatcher with nine subparsers, each defined by a
`build_subparser(subparsers)` function in the relevant stage module
plus a `run(args)` function. This is fine for a tool with external
users running unfamiliar subcommands. The user base here is one
person who runs `scripts/run-pipeline.sh` or
`scripts/nesh-pipeline.sbatch`.

The argparse wiring is a non-trivial slice of the codebase:

- [`screen.py:209-266`](../../src/laglitsynth/screening_abstracts/screen.py#L209) — 58 LOC.
- [`eligibility.py:222-298`](../../src/laglitsynth/fulltext_eligibility/eligibility.py#L222) — 77 LOC.
- [`extract.py:258-334`](../../src/laglitsynth/extraction_codebook/extract.py#L258) — 77 LOC.
- [`fetch.py:131-158`](../../src/laglitsynth/catalogue_fetch/fetch.py#L131) — 28 LOC.
- [`retrieve.py:289-317`](../../src/laglitsynth/fulltext_retrieval/retrieve.py#L289) — 29 LOC.
- [`extract.py:72-103`](../../src/laglitsynth/fulltext_extraction/extract.py#L72) — 32 LOC.
- [`adjudicate.py:21-53`](../../src/laglitsynth/screening_adjudication/adjudicate.py#L21) — 33 LOC.
- [`dedup.py:17-29`](../../src/laglitsynth/catalogue_dedup/dedup.py#L17) — 13 LOC.
- [`export.py:527-583`](../../src/laglitsynth/screening_abstracts/export.py#L527) — 57 LOC.
- [`cli.py`](../../src/laglitsynth/cli.py) — 82 LOC, plus the `--config` sniffing dance ([config.py:42-71](../../src/laglitsynth/config.py#L42)).

Total: ~480 LOC of argparse + dispatch + config-sniffing
infrastructure for a tool driven by two shell scripts.

The replacement. Two-tier:

- `src/laglitsynth/pipeline.py` (~80 LOC) exposes one function per
  stage. Each takes pure Python kwargs (paths, model name,
  threshold, etc.) — no `argparse.Namespace`. Returns the outcome
  (count summary, output paths) as a small dataclass or just
  prints to stderr.
- `scripts/run.py` (or extend `scripts/run-pipeline.sh` to call
  Python directly) is the only place argparse exists, and it
  exists at the orchestration level not the stage level. About
  20–30 LOC.

A caller from a notebook becomes:

```python
from laglitsynth import pipeline
pipeline.fetch(query="lagrangian particle tracking",
               output=Path("data/run/catalogue/works.jsonl"),
               api_key=os.environ["OPENALEX_API_KEY"])
pipeline.screen(input=..., prompt=..., model="gemma3:4b", ...)
```

No `--config` / `set_defaults` / config-sniffing dance — kwargs
and defaults are Python's native shape. The
[`config.py`](../../src/laglitsynth/config.py) module shrinks to
just `save_resolved_config` (the run-snapshot writer, which is
load-bearing for the codebook-inlining audit value); the
input-config-loading half goes away. If you still want to drive
runs from YAML on NESH, the orchestration script does it:
`pipeline.screen(**yaml.safe_load(open("sweep.yaml")))`.

What's deleted:

- All nine `build_subparser()` functions (~400 LOC across stages).
- [`cli.py`](../../src/laglitsynth/cli.py) (82 LOC).
- The `--config` sniff in [config.py:42-71](../../src/laglitsynth/config.py#L42)
  (~30 LOC).
- The `register_config_arg` helper.
- Per-stage `run(args: argparse.Namespace)` indirection — call sites
  go from `args.foo` to a kwarg.
- Tests that build `argparse.Namespace` manually and call
  `run(args)` — they call `pipeline.screen(...)` directly. Search
  finds these throughout
  [test_screening_abstracts.py:_run_args](../../tests/test_screening_abstracts.py#L422),
  [test_extraction_codebook.py:_make_run_args](../../tests/test_extraction_codebook.py#L378),
  [test_fulltext_eligibility.py](../../tests/test_fulltext_eligibility.py).

What survives:

- One small `scripts/run.py` (or a bash script that calls
  `python -m laglitsynth.cli ...` — bikeshed-territory).
- The `[project.scripts]` entry in
  [pyproject.toml](../../pyproject.toml#L21). Point it at the new
  shape.

Net change: ~400 LOC source removed; ~80 LOC `pipeline.py` added.

### Combined effect

Approximate LOC after all three moves plus the obvious wins from
pass 1:

| | Before | Pass 1 obvious wins | + Move 1 (`*Meta` kill) | + Move 2 (collapse stages) | + Move 3 (drop CLI) | + XLSX kill |
|---|---|---|---|---|---|---|
| src | 4480 | 4180 | 3930 | 3680 | 3280 | 2680 |
| tests | 5512 | 5300 | 5150 | 4800 | 4400 | 3800 |

Numbers are rough — exact counts depend on decisions about how
much ceremony to keep around the JSON `provenance.json` writer and
how thin the orchestration script ends up. The shape is right: a
~40% drop in source and a ~30% drop in tests.

## Other findings

### Drop mypy strict

AGENTS.md says "mypy with strict mode is the bar." That bar is
load-bearing for a library shipped to outside consumers. For a
single-user research pipeline, it costs more than it pays:

- The `Iterator[T]` invariance dance in `read_jsonl` (the function
  is generic over a `_T = TypeVar("_T", bound=BaseModel)`) is real
  type theatre.
- Casts: [`extract.py:91`](../../src/laglitsynth/extraction_codebook/extract.py#L91)
  and [`extract.py:156`](../../src/laglitsynth/extraction_codebook/extract.py#L156)
  cast to `ExtractionRecordProto` because the dynamic record class
  can't be expressed as a static type.
- `# type: ignore[attr-defined]` annotations in tests
  ([test_extraction_codebook.py](../../tests/test_extraction_codebook.py)
  passim).
- Three `[[tool.mypy.overrides]]` blocks in
  [pyproject.toml](../../pyproject.toml) for libraries without
  stubs.

Recommendation: drop `--strict`, run mypy without the flag (or
`--strict-equality`-ish subset), keep type annotations in source as
documentation. This is consistent with AGENTS.md's "Keep it
practical" but inconsistent with the "Mypy with strict mode is the
bar" line — that line should change.

If the user disagrees, this is a local optimum; ignore. But the
choice is sharper than AGENTS.md acknowledges.

### Drop dynamic `ExtractionRecord`

The first pass [§3.2](simplification-pass.md#32-extraction_codebookextractpy-519-loc-judgment)
hedged on this. I won't.

[`codebook.py:108-147`](../../src/laglitsynth/extraction_codebook/codebook.py#L108)
builds the LLM-payload class at startup from a YAML codebook spec,
on the premise that "YAML codebook edits don't require code
changes." But:

- AGENTS.md explicitly endorses code changes that match data-model
  changes ("Green field, no backwards compatibility").
- The codebook is in `examples/codebooks/lagrangian-oceanography.yaml`
  — *one* file. There is no second codebook in the repo.
- The dynamic class costs `ExtractionRecordProto`, runtime-built
  Pydantic models, casts in extraction code, and the `_PayloadBase`
  before-validator's `set(cls.model_fields)` filtering trick.
- The codebook is supposed to converge during phase 3 review.
  After it converges, it is stable.

Replace with a hand-written `ExtractionRecord` Pydantic model in
`extraction_codebook/models.py`. The 28 fields per
[codebook.md](../codebook.md) are spelled out. Adding a field is a
2-line edit (model + codebook YAML), not a config change. Net:
~80 LOC of `codebook.py` deleted, `ExtractionRecordProto` deleted,
two `cast()` calls removed.

The first pass said "post-phase-3 only." I'd commit it now and
explicitly accept that codebook iteration during phase 3 is a
"git diff src/laglitsynth/extraction_codebook/models.py" review, not
a YAML-edit review. That's *better*: you see the code and the
codebook diverging at the same diff hunk.

### Drop dry-run on retrieval

[`retrieve.py:185-210`](../../src/laglitsynth/fulltext_retrieval/retrieve.py#L185)
duplicates the cascade detection logic without calling out — first
pass already noted this. The cleanest fix isn't "use `download=False`,"
it's just deleting the `--dry-run` flag. Honest smoke tests use
`--max-records 5`. Saves ~30 LOC.

Same logic for `--dry-run` on
[`screen.py`](../../src/laglitsynth/screening_abstracts/screen.py),
[`eligibility.py`](../../src/laglitsynth/fulltext_eligibility/eligibility.py),
and [`extract.py`](../../src/laglitsynth/extraction_codebook/extract.py).
On the LLM stages dry-run is *less* useful (the actual cost is the
LLM call, which dry-run skips, leaving only argparse round-tripping
to test). Delete in the Move 3 commit when CLI subparsers go away.

### Drop the per-stage `TOOL_NAME` constant

Eight separate `TOOL_NAME = "laglitsynth.<thing>.<func>"` strings:
[`catalogue_fetch/models.py:115`](../../src/laglitsynth/catalogue_fetch/models.py#L115),
[`catalogue_dedup/models.py:5`](../../src/laglitsynth/catalogue_dedup/models.py#L5),
[`screening_abstracts/models.py:5`](../../src/laglitsynth/screening_abstracts/models.py#L5),
[`screening_adjudication/models.py:7`](../../src/laglitsynth/screening_adjudication/models.py#L7),
[`fulltext_retrieval/models.py:7`](../../src/laglitsynth/fulltext_retrieval/models.py#L7),
[`fulltext_extraction/models.py:8`](../../src/laglitsynth/fulltext_extraction/models.py#L8),
[`fulltext_eligibility/models.py:9`](../../src/laglitsynth/fulltext_eligibility/models.py#L9),
[`extraction_codebook/models.py:18`](../../src/laglitsynth/extraction_codebook/models.py#L18).

Each is the dotted path to the stage's main function. Each appears
exactly twice: at definition and in the `RunMeta(tool=TOOL_NAME, ...)`
construction. The Move 1 deletion subsumes this — once `*Meta` dies,
the tool name is just a string in `provenance.json`, which the
`write_provenance` helper takes as a kwarg with a sensible default
(`__name__` of the calling module).

### Drop `tool_version: str = "alpha"`

Same: dies with `RunMeta`. There has never been a non-"alpha"
`tool_version` and there will not be. If a future synthesis paper
needs to cite a specific revision, the `git rev-parse HEAD` of the
repo at run time is the right answer, recorded in
`provenance.json` as `git_sha` if needed (one-line `subprocess.run`
or `dulwich`). Better: don't bother. The pipeline is run from a
clean working tree by a single user; the commit's there in the log.

### `screening-meta.json.prompt` default + tolerant fallback

First pass §2.7 — agreed; subsumed by Move 1.

### `_PayloadBase._coerce_llm_types` — keep

[`codebook.py:71-101`](../../src/laglitsynth/extraction_codebook/codebook.py#L71).
This *is* a load-bearing 30 LOC. LLMs at t=0.8 do return bools and
lists where strings are asked for; coercing rather than
`ValidationError`-ing the whole record is real value, with no
shorter shape. Keep.

The corresponding eligibility-stage version
([`fulltext_eligibility/models.py:31-53`](../../src/laglitsynth/fulltext_eligibility/models.py#L31))
is similar but eligibility-specific. After Move 1 the file
restructures and these merge — both want the "drop unknown keys,
flatten lists, pass through None" behaviour. ~10 LOC saved.

### Tests testing the framework

A 1.2:1 test-to-source ratio is high for this kind of code. Looking
at where the tests are spent:

- [`test_screening_abstracts.py:test_screening_meta_serialization`](../../tests/test_screening_abstracts.py#L376)
  tests Pydantic correctly dumps a `ScreeningMeta` with a `RunMeta`
  inside it. This is testing Pydantic, not the pipeline.
- [`test_screening_abstracts.py:test_screening_verdict_extra_fields_forbidden`](../../tests/test_screening_abstracts.py#L361)
  tests that `extra="forbid"` works as Pydantic documents.
- Similar `*Meta` serialization tests in
  [test_fulltext_eligibility.py](../../tests/test_fulltext_eligibility.py),
  [test_extraction_codebook.py](../../tests/test_extraction_codebook.py),
  etc.
- Tests that build `argparse.Namespace` and call `run(args)` exist
  to test the argparse → run wiring. After Move 3, calls become
  pure-Python `pipeline.screen(...)` with kwargs and these tests
  shrink correspondingly.

Recommendation: as part of each move, delete the corresponding
tests outright (they test the deleted machinery, not the surviving
behaviour). Don't try to retain coverage on
"`ScreeningMeta.model_dump()` produces the right shape" — the meta
file isn't there any more.

### `screening-abstracts-export` remains a separate subcommand

After Move 3, the export becomes `pipeline.export_screening_csv(...)`.
It's not part of the linear pipeline and the driver script doesn't
call it; it's a separate utility. That's fine — `pipeline.py` is a
namespace, and a `screening_export` function alongside `screen`,
`retrieve`, etc., reflects what the operation is. Don't try to
hide it behind the eight-stage progression.

## What stays — and why

The first pass had a §5 "things explicitly **not** to simplify"
list. Mostly I agree, but I'm calling out my reasoning:

- **`Work` model breadth.** OpenAlex is what it is. Trimming would
  require the rest of the team to re-decide what's
  citation-graph-relevant. ~123 LOC of Pydantic that mirrors the
  upstream surface; the LOC is honest data-model work, not
  ceremony.
- **`extra="ignore"` upstream / `extra="forbid"` internal.** This
  catches real bugs at the boundary. Cheap policy, real value.
- **TEI parser ([`tei.py`](../../src/laglitsynth/fulltext_extraction/tei.py)).**
  The lazy-on-disk model + lxml parsing is doing real work; the
  defensiveness is justified by `grobid-lessons.md`. 289 LOC of
  parsing for a real XML format, not over-engineering.
- **`prompt_sha256` and the `--skip-existing` guard.** This is the
  *one* meta field with a consumer. Survives Move 1.
- **`save_resolved_config` snapshot writer.** The codebook-inlining
  audit value (config snapshot tells you what the run was
  configured with a year later) is real. Survives Move 3 even
  though `register_config_arg` and the input-config-load path go
  away.
- **`flatten_sections`** in
  [tei.py](../../src/laglitsynth/fulltext_extraction/tei.py#L212).
  Shared across stages 5 and 6 (was 7 and 8); justified.
- **Run-id directories** for stages 5 and 6 (was 7 and 8). The
  multi-run sweep shape this enables is real: a single user
  comparing model outputs across two prompt versions wants
  side-by-side directories, not a clobber.
- **Per-record `append_jsonl` for crash safety.** Real bug-driven
  defensiveness ([plans/done/stage-5-6-bugs.md](../../plans/done/stage-5-6-bugs.md)).
- **The OpenAI / Ollama client itself.** Don't reinvent.

## Disagreement with AGENTS.md

Two principles I'd weaken or invert:

### "Plans before code"

AGENTS.md insists every component starts with a plan in `plans/`,
reviewed before implementation. This is the right discipline for
genuine new features (stages 9–12 are real design work). It is
overhead for moves like the three above, where the goal is *less
code, less ceremony*. Plans are spec; for "delete N files," prose
in a PR description is plenty.

I'd carve out: simplification PRs reference the relevant section of
this exploration doc; they don't need a separate plan in `plans/`.

### "Mypy with strict mode is the bar"

Argued above under "Drop mypy strict." This is the most concrete
point of disagreement — strict mypy on a one-developer research
prototype that calls into mostly-untyped LLM responses costs more
than it pays.

### "Typed data, not dicts"

I'd keep this for the data model (`Work`, the LLM payloads,
`ExtractionRecord` post-hand-write). I'd relax it for `*Meta` /
provenance. Provenance is a one-shot write from a known source;
nobody constructs a `RunMeta` from a dict at runtime that could
have wrong fields. The Pydantic discipline is *defensive* —
worth paying for data crossing the LLM/disk boundary, not for
data the same `run()` call wrote moments ago.

## Recommended next concrete commit

**Replace `RunMeta` / `LlmMeta` / all eight `*Meta` classes with a
single `provenance.py` helper.**

This is Move 1, on its own, no other changes. It is:

- Mechanical: delete eight files' worth of `*Meta` model code,
  rewrite the eight `run()` constructors as one-liners, rename
  the eight meta-file basenames to `provenance.json`.
- Single-PR-scoped: ~250 LOC source change, ~150 LOC tests.
- Self-contained: no follow-ups gated on it. The first move
  unlocks the cleanest state of the meta layer; subsequent moves
  (stage collapse, CLI rewrite) inherit a clean baseline.
- Testable: `provenance.json` shape is asserted in two tests
  (one for an LLM stage, one for a non-LLM stage) — full coverage,
  in <50 LOC.
- Lowest risk: nothing other than the `--skip-existing` hash
  guard reads any field of the meta file; the guard's read path
  uses raw `json.loads` already.

After that lands, Move 2 (collapse stages 2 + 4) is the second
commit; Move 3 (drop the CLI subparser tree) is the third. The
XLSX deletion can interleave anywhere — independent of the moves.

I would *not* recommend starting with the first pass's "obvious
wins" list (one `render_fulltext`, sentinel `Literal`, one
`Counter` pass). Those are real but are sub-improvements *within*
the structure that Move 1 partially deletes. Doing them first means
re-doing some work after Move 1 lands. After the three moves, the
remaining `render_fulltext` + sentinel + counter-pass cleanups land
in one tidy follow-up commit.
