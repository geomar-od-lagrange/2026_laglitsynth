# Simplification pass — 2026-05-03

A read of the 8 implemented pipeline stages plus the shared infra
(`cli.py`, `config.py`, `ids.py`, `io.py`, `models.py`) against the
docs (`pipeline.md`, `objectives.md`, `data-model.md`,
`interfaces.md`, all per-stage docs, `codebook.md`, `configs.md`,
`two-pass-extraction.md`, `llm-concurrency.md`, `glossary.md`,
`grobid-lessons.md`, `vocabulary.md`).

Every finding is tagged:

- **[obvious win]** — clearly redundant or simpler form exists, low risk.
- **[judgment]** — depends on direction; the trade-off is real.
- **[confirm]** — blocked on a user-only decision.

## 0. What the pipeline is for

WP1.1 systematic review of numerical methods in computational
Lagrangian oceanography. Three RQs (reproducibility, prevalence,
rationale). Twelve stages. The single load-bearing output is stage 12's
`synthesis-draft.md` — every other artifact is intermediate scaffolding
to support either (a) human spot-checking of an LLM stage or (b) the
join from a downstream stage back to the source records.

Implementation status: stages 1–8 ship, 9–12 are unwritten, stages 2
and 4 are pass-through MVPs.

## 1. Pipeline-level: in/out artifacts

### 1.1 The `flag, don't filter` design has been silently inverted [judgment]

[`docs/interfaces.md`](../interfaces.md) ("Design principle: flag, don't
filter") promises that gate stages (3, 4, 7) write verdict sidecars and
**never** copy or split `Work` records — downstream stages join the
catalogue against verdicts at read time via a shared `resolve` module
that "is the single most important new piece of shared infrastructure."

The shared module does not exist. Instead:

- Stage 4 emits a derived [`included.jsonl`](../../src/laglitsynth/screening_adjudication/adjudicate.py#L109)
  of `Work` records.
- Stage 7 emits a derived [`eligible.jsonl`](../../src/laglitsynth/fulltext_eligibility/eligibility.py#L460)
  of `Work` records, rebuilt every run by joining catalogue against
  verdicts.
- Stages 5 and 8 read those derived files as their inputs.

So in practice the pipeline filters; "flag, don't filter" is doc-only.
The cheapest route back to the documented design is to build the
`resolve` module:

```python
# laglitsynth/resolve.py
def included_works(catalogue: Path, verdicts: Path, threshold: int) -> Iterator[Work]: ...
def eligible_works(catalogue: Path, eligibility_verdicts: Path) -> Iterator[Work]: ...
```

Once it exists, `included.jsonl` and `eligible.jsonl` go away; stages 5,
7, 8 take `--catalogue` + `--verdicts` + threshold flags. Stage 4 in its
current pass-through form deletes entirely (only state it adds is a
`reviewer` field never read by any other stage).

### 1.2 Stage 2 `catalogue-dedup` is a no-op [obvious win]

[`dedup.py`](../../src/laglitsynth/catalogue_dedup/dedup.py) is 65 LOC
of plumbing that copies input → output unchanged, plus an empty
`dropped.jsonl`, plus a meta with `duplicates_removed=0`. The doc
specifies real matching rules (DOI normalize, then title + first
author + year). Two ways out:

- **Replace with a 30-line implementation** — DOI normalization +
  fallback dict on `(normalize_title(t), first_author, year)`. Solves
  the problem instead of pretending to.
- **Drop the stage** — replace with a documented `cat
  data/catalogue-fetch/*.jsonl > data/catalogue-dedup/deduplicated.jsonl`.
  The current stage adds nothing but the file move; the meta sidecar
  with `duplicates_removed=0` is misleading.

Either is better than the current placeholder. The first is preferable
because dedup is genuinely needed once multiple search runs land.

### 1.3 Stage 4 `screening-adjudication` is a pass-through that produces only derivable state [judgment]

[`adjudicate.py`](../../src/laglitsynth/screening_adjudication/adjudicate.py)
137 LOC. For every above-threshold work it emits:

- `AdjudicationVerdict(decision="accept", reviewer="pass-through", reason=…)`
- `included.jsonl` — derivable from verdicts + catalogue + threshold.

No downstream stage reads `decision`, `reviewer`, or
`adjudicated_at`. The future real-adjudication implementation is
described as a CSV/XLSX-ingest workflow analogous to stage 9
(`adjudication-extraction.md`) — none of the pass-through code carries
forward.

Recommendation: when the resolve module lands, delete this stage. Stage
5 (`fulltext-retrieval`) takes `--catalogue` + `--screening-verdicts` +
`--screening-threshold` and resolves at read time. The eventual real
adjudication is a separate ingest stage that consumes a reviewer-edited
sheet (matching the planned stage 9 shape), not this scaffold.

### 1.4 Run-id directories only on LLM stages [judgment]

[`configs.md`](../configs.md) describes the `<data-dir>/<stage>/<run-id>/`
layout for stages 3, 7, 8. Stages 2, 4, 5, 6 dump into bare directories.

The split is partly justified — PDFs (stage 5) and TEIs (stage 6) are
explicitly shared caches; the dedup catalogue is the single
pipeline-wide source. But stage 4's outputs are scoped to a single
adjudication "run" the same way the LLM stages are, and once stage 4
becomes real (CSV/XLSX ingest) it'll need run-id directories too. The
current asymmetry is an artifact of "we only built it where we needed
sweeps so far."

Either bring all gate stages onto run-id dirs, or document the split as
"input caches stay flat, gate decisions are run-scoped." Today's state
is undocumented.

## 2. Cross-cutting code findings

### 2.1 LLM-stage triplication [obvious win, scope-bounded]

Stages 3, 7, 8 share a common shape that has now been re-implemented
three times:

| concern | screen.py | eligibility.py | extract.py |
|---|---|---|---|
| `_preflight()` Ollama health check | L199 | L212 | L248 |
| `seed = random.randint(0, 2**31 - 1)` | L90 | L75 | L111 |
| `client.chat.completions.create(..., response_format={"type":"json_object"}, temperature=_TEMPERATURE, seed=seed)` | L92 | L77 | L114 |
| `except (APITimeoutError, APIConnectionError):` → emit `llm-timeout` sentinel | L105 | L88 | L125 |
| `except (json.JSONDecodeError, ...) / ValidationError:` → emit `llm-parse-failure` sentinel with raw_response | L126 | L101 | L137 |
| compute `prompt_sha256` | L281 | L320 | L354 |
| run-id dir + `save_resolved_config()` | L274/298 | L306/364 | L342/407 |
| `--skip-existing` + prompt-hash guard | n/a | L325 | L367 |
| `OpenAI(base_url=…, api_key="ollama", timeout=…, max_retries=…)` client | L164 | L370 | L413 |
| iterate catalogue → cascade (full_text → abstract → no-source) | n/a | L152 | L190 |

A `laglitsynth.llm_stage` module that exposes:

- `LlmStage` dataclass: `tool_name`, `temperature`, `num_ctx`,
  `timeout`, `max_retries`, `prompt_sha_recipe`.
- `make_client(args)` and `preflight(args)`.
- `call_with_sentinels(client, system_prompt, user_prompt, ...)`
  returning `(payload | None, sentinel_reason | None, seed, raw)`.
- `iter_works_with_cascade(catalogue, extractions, extraction_dir,
  ...) -> Iterator[(work, source_basis, rendered_text, truncated_flag)]`
  — yields the (work, branch, text) tuples; the caller turns each into
  a verdict/record.
- `setup_run_dir(args, stage_subdir, inlines=())` returning the
  resolved output dir.

Each stage's `run()` shrinks from 100–200 LOC to ~50 LOC of "build
sentinel; call LLM; emit verdict; tally counters." The cascade
duplicated between 7 and 8 collapses to one source.

**Bound on this:** don't invent the abstraction speculatively. With
only stage 7 and 8 using the cascade, two near-clones is borderline. If
stage 11 (thematic synthesis) is also LLM-driven over per-work text —
which the doc implies — the third clone is a strong signal. Worth
extracting now if stages 11/12 will land in the next round. If
synthesis stages are aggregate-level (one call per RQ, not per work),
the cascade isn't shared and the abstraction earns less.

### 2.2 Two `render_fulltext` helpers [obvious win]

- [`fulltext_eligibility/prompts.py:render_fulltext(tei) -> str`](../../src/laglitsynth/fulltext_eligibility/prompts.py#L38)
- [`extraction_codebook/prompts.py:render_fulltext(tei, *, char_budget) -> tuple[str, bool]`](../../src/laglitsynth/extraction_codebook/prompts.py#L54)

The codebook docstring acknowledges the duplication explicitly: "Stage
7 has its own ``render_fulltext`` without truncation. … this duplicates
the stage 7 helper with light factoring rather than introducing a
shared module."

Collapse to one signature in
[`fulltext_extraction/tei.py`](../../src/laglitsynth/fulltext_extraction/tei.py)
alongside `flatten_sections`:

```python
def render_fulltext(tei: TeiDocument, char_budget: int | None = None) -> tuple[str, bool]:
    blocks = flatten_sections(tei)
    if char_budget is None:
        return "\n\n".join(blocks), False
    kept, truncated = _truncate_blocks(blocks, char_budget)
    return "\n\n".join(kept), truncated
```

Stage 7 ignores `truncated`; stage 8 records it. Net delete: ~30 LOC
across two prompts.py files.

### 2.3 Sentinel reasons as bare string literals [obvious win]

The strings `no-abstract`, `no-source`, `tei-parse-failure`,
`llm-parse-failure`, `llm-timeout` are repeated across:

- the verdict construction site (one per stage),
- the count-summary computation (one `if r.reason == "X"` per sentinel),
- the docs (`screening-abstracts.md`, `eligibility.md`,
  `extraction-codebook.md` all have the same sentinel table).

Define once in `laglitsynth.models`:

```python
SentinelReason = Literal[
    "no-abstract", "no-source",
    "tei-parse-failure", "llm-parse-failure", "llm-timeout",
]
SENTINEL_REASONS: tuple[SentinelReason, ...] = get_args(SentinelReason)
```

Then verdict models use `reason: SentinelReason | str | None` and
counters iterate `SENTINEL_REASONS` instead of hand-listing them.
Future renames become a single edit.

### 2.4 Six separate `sum(1 for ...)` passes per LLM stage [obvious win]

[`eligibility.py` L420–435](../../src/laglitsynth/fulltext_eligibility/eligibility.py#L420)
and [`extract.py` L458–479](../../src/laglitsynth/extraction_codebook/extract.py#L458)
each scan `all_records` six times to compute counters. Replace with one
`collections.Counter` pass over `(reason or "ok", source_basis,
truncated)` tuples. ~40 LOC of arithmetic become ~10.

### 2.5 `_preflight()` triplicated verbatim [obvious win]

Same 7-line function in
[`screen.py:199`](../../src/laglitsynth/screening_abstracts/screen.py#L199),
[`eligibility.py:212`](../../src/laglitsynth/fulltext_eligibility/eligibility.py#L212),
[`extract.py:248`](../../src/laglitsynth/extraction_codebook/extract.py#L248).
Lift to `laglitsynth.llm_stage.preflight(base_url, model)`.

### 2.6 `RunMeta` constructor duplicated 8 times [obvious win]

```python
run_meta = RunMeta(
    tool=TOOL_NAME,
    run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
    validation_skipped=stats.skipped,
)
```

Appears identically in stages 1, 2, 3, 4, 5, 6, 7, 8. Lift to
`laglitsynth.models.make_run_meta(tool: str, stats: JsonlReadStats) ->
RunMeta`. Eight 5-line blocks become 8 one-liners.

### 2.7 `screening-meta.json.prompt: str = ""` default + tolerant fallback [obvious win]

[`models.py:29`](../../src/laglitsynth/screening_abstracts/models.py#L29)
defaults `prompt` to `""`. [`export.py:_load_meta`](../../src/laglitsynth/screening_abstracts/export.py#L425)
has 30 LOC of "tolerate missing/older meta files."

[`AGENTS.md`](../../AGENTS.md) is explicit: "Green field, no backwards
compatibility." Drop the default; require the prompt; let `_load_meta`
either succeed via `ScreeningMeta.model_validate()` or fail loudly.
Saves ~25 LOC.

## 3. Per-component LOC and over-engineering

### 3.1 `fulltext_retrieval/retrieve.py` (475 LOC) [judgment, partial win]

Reducible items:

- **`_RateLimiter` class** ([L39–L51](../../src/laglitsynth/fulltext_retrieval/retrieve.py#L39))
  is one method holding one dict. Inline as `_last_request:
  dict[str, float] = {}` plus a free function — saves the class
  scaffolding, ~10 LOC. **[obvious win]**
- **`_AllAttemptsFailedError`** ([L115](../../src/laglitsynth/fulltext_retrieval/retrieve.py#L115))
  is raised in one place and caught in one place. Replace with a
  `tuple[Status, str] | Exception` return on `_try_oa_urls`. ~15 LOC
  out. **[obvious win]**
- **Dry-run special case** ([L185–L210](../../src/laglitsynth/fulltext_retrieval/retrieve.py#L185))
  re-implements the cascade detection logic without actually calling
  anything. The doc says dry-run "report what would be retrieved without
  downloading." A simpler shape: pass `download=False` into
  `_download_pdf`, which returns the URL probe result without writing.
  Or: drop dry-run entirely — `--max-records 5` is a more honest
  smoke-test. **[judgment]**
- **Resumable rewrite-and-append** ([L334–L372](../../src/laglitsynth/fulltext_retrieval/retrieve.py#L334))
  is genuinely defensive engineering for long downloads (per
  `plans/done/stage-5-6-bugs.md`). Keep. **[no change]**

Estimated reachable size: ~330 LOC.

### 3.2 `extraction_codebook/extract.py` (519 LOC) [judgment]

Most of the LOC is `run()` boilerplate (load, loop, count, write meta).
After the cross-cutting fixes (2.1 + 2.4 + 2.5 + 2.6) the file is
~250 LOC. The remaining content is the `_extract_one` cascade and the
LLM call wrapper — neither has obvious slack independently.

The dynamic `ExtractionRecord` + `ExtractionRecordProto` structure
([codebook.py](../../src/laglitsynth/extraction_codebook/codebook.py))
is borderline. Pros (per `extraction-codebook.md`): YAML codebook
edits don't require code changes. Cons: every consumer must use
`ExtractionRecordProto` for types, and reading record fields by name
breaks Python's static help. **The plan rests on the codebook
churning during phase 3 review.** If phase 3 lands and the codebook
stabilises, replacing the dynamic build with a hand-written
`ExtractionRecord` Pydantic model is a 30-LOC change worth doing for
readability. **[confirm: when does phase 3 land?]**

### 3.3 `fulltext_eligibility/eligibility.py` (488 LOC) [judgment]

Same shape as 3.2 — most LOC is run() boilerplate. After cross-cutting
fixes (2.1 + 2.4 + 2.5 + 2.6) drops to ~250.

### 3.4 `screening_abstracts/screen.py` (406 LOC) [no significant change]

Already factored: `screen_works` is a generator, `classify_abstract` is
the single-call function. The main complexity is the meta-upfront-
then-rewrite pattern + ThreadPoolExecutor concurrency, both load-bearing
([`docs/llm-concurrency.md`](../llm-concurrency.md)). Cross-cutting
fixes reduce this to ~280 LOC; further reduction risks losing real
machinery.

### 3.5 `screening_abstracts/export.py` (624 LOC) [judgment + confirm]

XLSX libraries are verbose, and the per-work sheet has real layout
content (bibliographic block, scoring placeholders, collapsed LLM
block). Most of the size is appropriate.

Reducible:

- `build_work_sheet` ([L269–L422](../../src/laglitsynth/screening_abstracts/export.py#L269))
  hand-codes 23 row positions. A data-driven dispatch — list of
  `(label, value, *, hyperlink=None, font=BOLD, alignment=WRAP)` tuples
  iterated with an enumerate — shrinks the function by ~50%, ~70 LOC.
- `_load_meta` simplifies per 2.7.

Bigger question, **[confirm]**: has the human reviewer actually used
the XLSX export end-to-end? If yes, the 600 LOC is invested capital;
keep. If the workflow has never run, the per-work tab structure
(reviewer placeholders, collapsed LLM block, hyperlink hopping) is a
designed UX for a reviewer that hasn't tested it. A simpler CSV
might do for the first real adjudication run, with the workbook layout
landing only when feedback says "the CSV is too flat."

### 3.6 `fulltext_extraction/tei.py` (289 LOC) [no change]

Doing real work — TEI parsing has real complexity (namespace
handling, figure stripping, bib extraction, defensive author
formatting). LOC is plausible.

### 3.7 `fulltext_extraction/extract.py` (219 LOC) [no change]

GROBID client + cascade. Already minimal.

### 3.8 `catalogue_fetch/fetch.py` (203 LOC) [no change]

OpenAlex client + abstract reconstruction + slug. Already minimal.

## 4. Summary table

| # | finding | tag | est. saving (LOC) | risk |
|---|---|---|---|---|
| 1.1 | build `resolve` module; delete `included.jsonl` and `eligible.jsonl` derivations | judgment | ~150 (stage 4 + dedup logic) | low if stage 9 reads via resolve |
| 1.2 | replace stage 2 with real dedup (or `cat`) | obvious win | net: shrink to ~30 LOC | low |
| 1.3 | delete stage 4 pass-through | judgment | ~130 | requires resolve |
| 1.4 | unify run-id dir policy | confirm | n/a | doc-only |
| 2.1 | `laglitsynth.llm_stage` module | obvious win once stage 11 confirmed | ~300 across 3 stages | medium (premature?) |
| 2.2 | one `render_fulltext` | obvious win | ~30 | low |
| 2.3 | sentinel reasons as `Literal` | obvious win | ~10 + future correctness | low |
| 2.4 | one `Counter` pass per stage | obvious win | ~40 | low |
| 2.5 | shared `_preflight` | obvious win | ~15 | low |
| 2.6 | `make_run_meta` helper | obvious win | ~30 | low |
| 2.7 | drop screening-meta `prompt` default + fallback | obvious win | ~25 | low |
| 3.1 | retrieve.py: drop `_RateLimiter` class + `_AllAttemptsFailedError` | obvious win | ~25 | low |
| 3.1 | retrieve.py: drop dry-run special case | judgment | ~30 | low |
| 3.2 | static `ExtractionRecord` once codebook stabilises | confirm | ~30 | post-phase-3 only |
| 3.5 | data-driven `build_work_sheet` | judgment | ~70 | low |
| 3.5 | XLSX justification | confirm | n/a | reviewer-feedback-gated |

Adding the obvious wins together: ~250–350 LOC reduction with no design
change, plus ~150–300 if `resolve` lands. Source code drops from 4480
to roughly 3700–3900.

## 5. Things explicitly **not** to simplify

- **`Work` model breadth.** OpenAlex's surface is what it is; the
  pydantic mirror is honest.
- **Extra-policy split** (`extra="ignore"` upstream, `extra="forbid"`
  internal). This catches real bugs.
- **Per-stage pydantic `*Meta` models.** They look duplicative but
  carry stage-specific counters that drive the run summary.
- **TEI parser defensiveness.** Test history (`grobid-lessons.md`)
  shows real failure modes.
- **Catalogue/corpus terminology** (`docs/glossary.md`). The
  systematic-review domain has these terms; they are load-bearing in
  the eventual write-up.

## 6. Open questions for the user

1. When does codebook phase 3 review run? That gates 3.2.
2. Has the XLSX reviewer workflow run end-to-end? That gates 3.5.
3. Is stage 11 (thematic synthesis) per-work LLM-driven, or
   aggregate? That gates 2.1 — a third LLM-stage clone tilts the
   judgment toward extracting `llm_stage` now; an aggregate stage
   means two clones is the steady state and the abstraction would
   over-fit.
4. Is the asymmetric run-id directory layout intentional, or a
   "we didn't get to it yet" gap?
