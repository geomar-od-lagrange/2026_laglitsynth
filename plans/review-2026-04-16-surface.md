# Surface-layer review 2026-04-16

Scope: terminology consistency, documentation quality, and CLI UX at the
surface of the repo after the recent rename to two-component stage names
(`catalogue-fetch`, `catalogue-dedup`, `screening-abstracts`,
`screening-adjudication`, `fulltext-retrieval`, `fulltext-extraction`,
`fulltext-eligibility`, `extraction-codebook`, `extraction-adjudication`,
`synthesis-quantitative`, `synthesis-thematic`, `synthesis-narrative`).

Files reviewed: [`AGENTS.md`](../AGENTS.md), [`README.md`](../README.md),
all docs in [`docs/`](../docs/), [`plans/multi-run-consensus.md`](multi-run-consensus.md),
[`plans/done/stages-4-5-plumbing.md`](done/stages-4-5-plumbing.md), and all
source files under [`src/laglitsynth/`](../src/laglitsynth/). I also ran
`laglitsynth --help` and each subcommand's `--help` to compare the live CLI
with the documented one.

## Terminology

Two-component stage names are mostly consistent at the directory / CLI
surface (`catalogue-fetch`, `catalogue-dedup`, etc. — `laglitsynth --help`
confirms the canonical form). The big remaining drifts are: (a) the
`Filter*` model names and "filter" vocabulary inside the screening stage,
(b) pipeline.md still using old hyphenated stage names in section headings
and mermaid diagrams, and (c) stale artifact/file names (`accepted.jsonl`,
`_filtered_`, `screened.jsonl`) in several docs.

### High priority

- `[should-fix]` [`src/laglitsynth/screening_abstracts/models.py:4`](../src/laglitsynth/screening_abstracts/models.py)
  — `FilterVerdict` / `FilterMeta` are now the only surviving `Filter*`
  names in the code. The stage is called `screening-abstracts` everywhere
  else; rename to `ScreeningVerdict` / `ScreeningMeta` (or
  `AbstractScreeningVerdict` / `AbstractScreeningMeta`) and update all
  call sites. Affects
  [`src/laglitsynth/screening_abstracts/screen.py`](../src/laglitsynth/screening_abstracts/screen.py)
  (imports, docstring on `ClassifyError` at line 33, return type at
  line 43, constructions at lines 61, 89, 103),
  [`tests/test_screening_abstracts.py:11-17,96,121,125,249,270,274`](../tests/test_screening_abstracts.py),
  and the docs listed below.

- `[should-fix]` [`src/laglitsynth/screening_abstracts/screen.py:1`](../src/laglitsynth/screening_abstracts/screen.py)
  — module docstring `"""LLM-based abstract filtering via Ollama."""` still
  says "filtering". Rename to "abstract screening" / "screening" to match
  the stage.

- `[should-fix]` [`src/laglitsynth/screening_abstracts/screen.py:73`](../src/laglitsynth/screening_abstracts/screen.py)
  — `filter_works()` function name. Rename to `screen_works()` (and
  everywhere it's imported/patched, including tests).

- `[should-fix]` [`src/laglitsynth/screening_abstracts/screen.py:127,130`](../src/laglitsynth/screening_abstracts/screen.py)
  — subparser help text `"Filter JSONL works by abstract relevance ..."`
  and argument help `"Relevance filter prompt string"`. Both still say
  "filter"; reword to "screen"/"screening". This help text also appears
  verbatim on `laglitsynth --help`.

- `[should-fix]` [`src/laglitsynth/screening_abstracts/screen.py:119`](../src/laglitsynth/screening_abstracts/screen.py)
  — default output filename still uses `_screened_` (good) but
  [`docs/filter-abstracts.md:48,61,66`](../docs/filter-abstracts.md) still
  document it as `_filtered_`. Either update the docs or the code, not
  both diverging.

- `[should-fix]` [`src/laglitsynth/screening_abstracts/models.py:17`](../src/laglitsynth/screening_abstracts/models.py)
  — `filtered_at: str` field. Rename to `screened_at` for consistency
  with `deduplicated_at`, `adjudicated_at`, `retrieved_at`, etc.

- `[should-fix]` [`src/laglitsynth/screening_abstracts/screen.py:175`](../src/laglitsynth/screening_abstracts/screen.py)
  — runtime log line `"Filtering {total} works with model {model}"`.
  Should say "Screening ...".

- `[should-fix]` [`docs/fetch-publications.md`](../docs/fetch-publications.md)
  — filename is stale. The stage is `catalogue-fetch`; rename the file to
  `docs/catalogue-fetch.md` and update references. Same issue for
  [`docs/filter-abstracts.md`](../docs/filter-abstracts.md) (→
  `screening-abstracts.md`),
  [`docs/full-text-retrieval.md`](../docs/full-text-retrieval.md) (→
  `fulltext-retrieval.md`),
  [`docs/full-text-extraction.md`](../docs/full-text-extraction.md) (→
  `fulltext-extraction.md`),
  [`docs/adjudication-screening.md`](../docs/adjudication-screening.md)
  (→ `screening-adjudication.md`),
  [`docs/adjudication-extraction.md`](../docs/adjudication-extraction.md)
  (→ `extraction-adjudication.md`),
  [`docs/deduplication.md`](../docs/deduplication.md) (→
  `catalogue-dedup.md`),
  [`docs/eligibility.md`](../docs/eligibility.md) (→
  `fulltext-eligibility.md`). After renaming, update the incoming links:
  - [`README.md:23-25,45`](../README.md) (points to
    `docs/fetch-publications.md`, `docs/filter-abstracts.md`).
  - [`docs/pipeline.md:7,189`](../docs/pipeline.md) (links out to
    `vocabulary.md` / `two-pass-extraction.md` — those are fine; but no
    per-stage docs are linked from pipeline.md table despite the entries
    listing anchors).
  - [`docs/full-text-retrieval.md:7`](../docs/full-text-retrieval.md)
    links to `full-text-extraction.md`.
  - [`docs/full-text-extraction.md:19,28`](../docs/full-text-extraction.md)
    links to `two-pass-extraction.md` and `grobid-lessons.md` (those are
    fine).

- `[should-fix]` [`README.md:22-25`](../README.md) — still says
  `laglitsynth fetch-publications` and `laglitsynth filter-abstracts` as
  the available commands. Neither is the actual subcommand name any more
  (`catalogue-fetch` and `screening-abstracts` are). The `laglitsynth
  filter-abstracts ...` example will fail. Update to the canonical names
  and update the description (`filter abstracts ... using a local
  Ollama-hosted LLM` → `screen abstracts ...`).

- `[should-fix]` [`docs/pipeline.md:17,50,172,247`](../docs/pipeline.md)
  — section heading and mermaid node labels use the old hyphenated
  `screen-abstracts`, and table entry links to anchor
  `#3-screen-abstracts-exists`. The canonical name is
  `screening-abstracts`. Update the section heading at line 50, the
  prose at line 172 (`"Every LLM-driven stage (screen-abstracts,
  eligibility, data-extraction) ..."`), the mermaid label at line 247,
  and the table anchor at line 17. Same for stage 5/6 — sections at
  lines 72, 85 say `full-text-retrieval` / `full-text-extraction` but
  the table column and the CLI/directory use `fulltext-retrieval` /
  `fulltext-extraction`. Same for stages 8–12 (`data-extraction`,
  `quantitative-synthesis`, etc. at lines 109, 134, 145, 159, 252–256) —
  the canonical forms are `extraction-codebook` / `extraction-adjudication`
  / `synthesis-quantitative` / `synthesis-thematic` /
  `synthesis-narrative` per the table header and
  [`docs/interfaces.md`](../docs/interfaces.md).

- `[should-fix]` [`docs/pipeline.md:59`](../docs/pipeline.md) — section 4
  is titled `### 4. adjudication` (old generic name), but stage 4 is now
  `screening-adjudication` and stage 9 is `extraction-adjudication`.
  Retitle to `### 4. screening-adjudication` (and likewise `### 9.
  extraction-adjudication` at line 120, which currently says
  `### 9. adjudication (extraction)`).

- `[should-fix]` [`docs/interfaces.md:180`](../docs/interfaces.md) and
  [`docs/interfaces.md:265,269`](../docs/interfaces.md) — existing-CLI
  block and end-to-end sequence still reference
  `data/screening-abstracts/accepted.jsonl` as stage 3's output and as
  stage 4's input. That filename is not produced by the current code
  (stage 3 writes a timestamped `_screened_` file per
  [`src/laglitsynth/screening_abstracts/screen.py:119`](../src/laglitsynth/screening_abstracts/screen.py)
  or whatever `-o` specifies). The design-principle section earlier in
  the same file (lines 57–68) explicitly states there should be no
  `accepted.jsonl` / `screened.jsonl` / `rejected.jsonl`. These three
  references are leftover from before the flag-don't-filter rewrite.

- `[should-fix]` [`docs/interfaces.md:63`](../docs/interfaces.md) —
  artifact-map entry says stage 3 writes
  `data/screening-abstracts/screening-meta.json`. The code actually
  writes `<output>.meta.json` (derived from `-o`, e.g.
  `..._screened_<ts>.meta.json`), per
  [`src/laglitsynth/screening_abstracts/screen.py:171`](../src/laglitsynth/screening_abstracts/screen.py).
  Either align the code to the spec (write a single well-known
  `screening-meta.json` as interfaces.md describes) or correct the spec
  to match the timestamped pattern used by all other implemented
  stages' sidecars.

- `[should-fix]` [`docs/interfaces.md:62`](../docs/interfaces.md) — same
  table says stage 3 writes `data/screening-abstracts/verdicts.jsonl`.
  The actual code writes `<output>.verdicts.jsonl` alongside the
  accepted-works JSONL. As the "Stage 3 implementation" note at lines
  70–73 / 152–157 already says, the code hasn't been brought in line
  with the flag-don't-filter spec yet — the spec/code gap is flagged
  but not resolved. Either close it or keep the `(needs code change)`
  warning at the top of the section so readers aren't misled by the
  artifact map.

- `[should-fix]` [`docs/adjudication-screening.md:23-41`](../docs/adjudication-screening.md)
  — CLI example at line 23 uses
  `--input data/screening-abstracts/screened.jsonl`; the file name
  `screened.jsonl` is not produced by any implemented code. Also the
  meta model shown at line 35 has
  `tool: str = "laglitsynth.screening_adjudication.screening"`, but the
  implementation
  ([`src/laglitsynth/screening_adjudication/models.py:5`](../src/laglitsynth/screening_adjudication/models.py))
  uses `"laglitsynth.screening_adjudication.adjudicate"` (module file
  is `adjudicate.py`, not `screening.py`). The docstring block needs a
  refresh against the code.

- `[should-fix]` [`docs/adjudication-extraction.md:30`](../docs/adjudication-extraction.md)
  — same pattern: `tool: str =
  "laglitsynth.extraction_adjudication.extraction"`. Model file does
  not yet exist but this string will be stale by the same rule when it
  does; by convention with
  [`src/laglitsynth/screening_adjudication/models.py`](../src/laglitsynth/screening_adjudication/models.py)
  it should be `"laglitsynth.extraction_adjudication.adjudicate"`.

- `[should-fix]` [`docs/eligibility.md:72,78-80,86`](../docs/eligibility.md)
  — doc still describes an `eligible.jsonl` artifact ("The
  `eligible.jsonl` file contains only the works that passed — this is
  the eligible corpus consumed by stage 8"), but
  [`docs/interfaces.md:112-114`](../docs/interfaces.md) says
  explicitly "No `eligible.jsonl` — downstream stages resolve the
  eligible set from the verdict sidecar". The eligibility doc still
  follows the old flag-and-split pattern. The CLI flag name also
  differs: eligibility.md uses `--catalogue` (line 86), interfaces.md
  uses `--data-dir` (line 208).

### Medium priority

- `[should-fix]` [`plans/multi-run-consensus.md:5,22,35,122`](multi-run-consensus.md)
  — plan mentions "stage 3 (screen-abstracts)" and "stage 8
  (data-extraction)" (old names), uses `FilterVerdict` (old model
  name), and references `--stage screen-abstracts` in the CLI sketch.
  Update to current names (`screening-abstracts`, `extraction-codebook`,
  and whatever the renamed verdict model is called).

- `[nit]` [`docs/full-text-retrieval.md:7`](../docs/full-text-retrieval.md)
  — prose refers to `full-text-extraction.md` (hyphenated) as the
  separate concern. After the doc filename rename this becomes
  `fulltext-extraction.md`. Also the doc title itself ("Full-text
  retrieval") uses the hyphenated word; the stage name is
  `fulltext-retrieval`. Decide whether the English prose
  ("full-text retrieval") is allowed to differ from the stage token
  (`fulltext-retrieval`) — I think both can live side-by-side but
  the filename and stage-anchor strings should match the CLI.

- `[nit]` [`docs/two-pass-extraction.md:76`](../docs/two-pass-extraction.md)
  — "The laglitsynth filter (stage 3) already uses JSON-mode..." —
  update "filter" → "screener".

- `[nit]` [`docs/data-model.md:5`](../docs/data-model.md) —
  "(BibTeX export, citation graph, filtering)" — update "filtering" →
  "screening" if we are abandoning the "filter" vocabulary.

- `[nit]` [`docs/explorations/nesh-ollama.md:4,8,16,213,281`](../docs/explorations/nesh-ollama.md)
  and [`docs/explorations/hawki-api.md:164,172`](../docs/explorations/hawki-api.md)
  — still use `filter-abstracts` as the stage name. Explorations are
  historical write-ups and
  [`AGENTS.md`](../AGENTS.md) calls them "not authoritative", so this
  is arguably fine. If keeping them as-is, consider adding a short
  "stage renamed to screening-abstracts in PR #5" note at the top of
  each so readers are not misled.

- `[nit]` [`src/laglitsynth/catalogue_fetch/fetch.py:103-105`](../src/laglitsynth/catalogue_fetch/fetch.py)
  — the `FetchMeta.tool` default is already
  `"laglitsynth.catalogue_fetch.fetch"` (consistent). Good — noting it
  as a reference for the other tool strings that need updating.

- `[nit]` [`src/laglitsynth/io.py:12`](../src/laglitsynth/io.py) —
  `from laglitsynth.catalogue_fetch.models import Work` — import path
  is correct but it may be worth noting that `io.py` hard-codes
  knowledge of one stage's model. If `Work` will continue to be
  pipeline-global, consider promoting it to
  `laglitsynth.models.Work`.

- `[nit]` `deduplicated.jsonl` vs the dedup module named `dedup.py`:
  the directory is `catalogue-dedup` (short), the module is `dedup`
  (short), the output file is `deduplicated.jsonl` (long). Consistent
  enough but if harmonising, pick one. Not blocking.

## Documentation

The pipeline's textual spec is in good shape at the high level
([`docs/pipeline.md`](../docs/pipeline.md),
[`docs/interfaces.md`](../docs/interfaces.md),
[`docs/glossary.md`](../docs/glossary.md) are coherent and mostly
up-to-date) but the per-stage docs and the README have not been
swept after the rename. There is also one broken link, several
filename-vs-title mismatches, and the `flag-don't-filter` principle is
asserted in interfaces.md but not yet reflected in the stage-3 code or
stage-3/4 per-stage docs. A handful of `**Bold:**` paragraph-heading
uses violate the AGENTS.md principle but are fairly minor.

### High priority

- `[blocker]` [`docs/interfaces.md:20`](../docs/interfaces.md) —
  `See [retuning-propagation.md](../plans/retuning-propagation.md)` is a
  **broken link**. The file is at
  [`plans/done/retuning-propagation.md`](done/retuning-propagation.md)
  (it was moved when the plan was completed). Update the link to
  `../plans/done/retuning-propagation.md`.

- `[should-fix]` [`README.md:22-25,45`](../README.md) — code examples
  use the old command names `fetch-publications` / `filter-abstracts`.
  Neither works today: `laglitsynth fetch-publications` is not a
  subcommand. `laglitsynth --help` lists `catalogue-fetch`,
  `catalogue-dedup`, `screening-abstracts`,
  `screening-adjudication`, `fulltext-retrieval`,
  `fulltext-extraction`. Fix the command names, the per-stage doc
  filenames linked from here (see terminology section), and update
  the tool descriptions (the second bullet still says "filter
  abstracts by relevance").

- `[should-fix]` [`docs/filter-abstracts.md`](../docs/filter-abstracts.md)
  entire file — reads as if it's still about the old `filter-abstracts`
  command: title, prose ("Filter a JSONL file..."), default output
  filename pattern (`_filtered_<timestamp>`), argument descriptions
  ("Relevance filter prompt string"). The actual subcommand,
  behavior, and default filenames have changed. Suggested rewrite:
  rename file to `screening-abstracts.md`, retitle to "Screening
  abstracts", replace "filter" with "screen" throughout, update the
  example output path to `data/screening-abstracts/<stem>_screened_<ts>.jsonl`,
  reconcile with interfaces.md (which says this stage should only
  write verdicts and a single meta, not timestamped accepted/rejected
  files).

- `[should-fix]` [`docs/interfaces.md:70-73,152-157`](../docs/interfaces.md)
  — "The existing code uses timestamped filenames and splits
  accepted/rejected Work records into separate files. Both must
  change: write only verdicts, drop the Work-record split." This is
  a planning statement, not a description of reality. It's duplicated
  verbatim in two places. Since this is a descriptive reference
  document (per
  [`AGENTS.md`](../AGENTS.md) "descriptive documents that remain
  useful after implementation as ongoing references"), it should
  describe reality. Either (a) actually make the code change and
  delete both blocks, or (b) move the note to a plan in
  [`plans/`](.) and have interfaces.md describe the current state
  accurately.

- `[should-fix]` [`docs/eligibility.md`](../docs/eligibility.md) and
  [`docs/interfaces.md`](../docs/interfaces.md) disagree on stage-7
  CLI interface and artifacts. eligibility.md line 86 shows
  `--catalogue data/screening-adjudication/included.jsonl` and line 87
  shows `--extractions ...`. interfaces.md line 208 shows
  `--data-dir data/`. Since stage 7 isn't implemented yet, pick one.
  The interfaces.md `--data-dir` plan is consistent with the
  flag-don't-filter design (lines 7–13); eligibility.md still
  reflects the old carry-the-file pattern. Update eligibility.md.

- `[should-fix]` [`docs/adjudication-screening.md:23`](../docs/adjudication-screening.md)
  — sample command `--input
  data/screening-abstracts/screened.jsonl` references a file name
  not produced by any implementation (actual outputs are the
  timestamped `_screened_` files or `accepted.jsonl` under an
  explicit `-o`). Either change to the actual filename or change
  the surrounding text to the flag-don't-filter model.

- `[should-fix]` [`docs/adjudication-screening.md:35`](../docs/adjudication-screening.md)
  and
  [`docs/adjudication-extraction.md:30`](../docs/adjudication-extraction.md)
  — `tool` strings for `AdjudicationMeta` /
  `ExtractionAdjudicationMeta` are wrong per current convention (the
  module file is named `adjudicate.py`, not `screening.py` /
  `extraction.py`). These docs still refer to the pre-rename
  submodule layout from
  [`plans/done/stages-4-5-plumbing.md`](done/stages-4-5-plumbing.md).
  Update both strings.

- `[should-fix]` [`docs/pipeline.md:9`](../docs/pipeline.md) — intro
  claims "Stages 1–4 operate on the catalogue (metadata + abstracts,
  no full text). Stage 5 retrieves PDFs; stage 6 extracts structured
  text from them. From stage 6 onward we work with the corpus." The
  table at line 19 says stage 5's domain is already `catalogue →
  corpus`; stage 6's is `corpus`. The glossary entry at line 56–59
  agrees with the table ("The catalogue becomes a corpus at
  full-text retrieval"). Fix the intro to say "From stage 5 onward
  we work with the corpus" (or more precisely, stage 5 is the
  boundary).

### Medium priority

- `[nit]` AGENTS.md principle **"Link, don't just name"** is
  reasonably well followed in descriptive docs. Bare backticks are
  used for runtime outputs (`data/.../*.jsonl`, `*.meta.json`) and
  for in-line markdown artifacts — acceptable. I did not find a
  systematic violation; no action needed unless the rule is read
  more strictly.

- `[should-fix]` AGENTS.md principle **"Plain markdown headings over
  bold-text formatting"** is violated in a few places:
  - [`docs/pipeline.md:37-38,47-48,56-57,65-66,81-82,93-94,105-107,117-118,127-128,142-143,155-156,166-167`](../docs/pipeline.md)
    use `- **Consumes:**` / `- **Produces:**` list-item bold-text
    headings. These are arguably not headings — they're
    definition-list-style labels. Still, per the explicit AGENTS.md
    rule ("Never simulate headings with `**Bold:**` paragraphs or
    `- **Bold.** text` list items"), this pattern is not allowed.
    Convert to a proper sub-heading (H4 or H5) under each stage
    section, or to a two-column markdown table, or to plain prose
    ("Consumes retrieved catalogue. Produces deduplicated
    catalogue.").
  - [`docs/data-model.md:18-29`](../docs/data-model.md) — same
    `- **Name.** text` pattern. In this case they really are labels
    for bullets (design decisions), so the value of converting to
    headings is debatable.
  - [`docs/two-pass-extraction.md:14,17`](../docs/two-pass-extraction.md)
    — "Context limits." / "Attention dilution." — debatable as
    headings vs. list labels.
  - [`docs/full-text-retrieval.md:54-58`](../docs/full-text-retrieval.md)
    — "Sci-Hub...", "Publisher APIs", "Google Scholar scraping" —
    these *are* named subsections inside "What we skip" and should
    be H5 headings.
  - [`docs/codebook.md:195,197`](../docs/codebook.md) — "Validation"
    / "Two-pass calibration" — again list labels for bulleted
    purposes; marginal.

- `[should-fix]` [`docs/pipeline.md:50,72,85,109,120,134,145,159`](../docs/pipeline.md)
  — section headings `### 3. screen-abstracts`, `### 5.
  full-text-retrieval`, `### 6. full-text-extraction`, `### 8.
  data-extraction`, `### 9. adjudication (extraction)`, `### 10.
  quantitative-synthesis`, `### 11. thematic-synthesis`, `### 12.
  narrative-synthesis` — all mix conventions. Use the canonical
  two-component names (`screening-abstracts`, `fulltext-retrieval`,
  `fulltext-extraction`, `extraction-codebook`,
  `extraction-adjudication`, `synthesis-quantitative`,
  `synthesis-thematic`, `synthesis-narrative`). The table at line
  13–26 already lists them correctly; the anchors will need to
  change together with the headings (they're currently
  `#3-screen-abstracts-exists` etc.).

- `[should-fix]` [`docs/pipeline.md:247,249-256`](../docs/pipeline.md)
  — mermaid node labels. `SCREEN[screen-abstracts]`,
  `RETRIEVE[full-text-retrieval]`, `GROBID[full-text-extraction]`,
  `EXTRACT[data-extraction]`, `QSYNTH[quantitative-synthesis]`,
  `TSYNTH[thematic-synthesis]`, `NARR[narrative-synthesis]`. These
  rendered labels are the user-facing names in the flowchart and
  should match the CLI/directory names.

- `[should-fix]` [`docs/pipeline.md:143-144`](../docs/pipeline.md) —
  `statistics.json` in stage 10's "Produces" line is the artifact
  filename, but no path prefix. interfaces.md says the file lives at
  `data/synthesis-quantitative/statistics.json`. Likewise for
  `rationale-taxonomy.json` and `synthesis-draft.md`. Consider
  making these paths explicit (as in interfaces.md) to avoid
  ambiguity about where the files appear in the tree.

- `[nit]` [`docs/interfaces.md:191-194`](../docs/interfaces.md) —
  "Stages 1 and 3 use positional arguments. All other subcommands
  use `--input` / `--output-dir` keyword flags. Stages 1 and 3
  should be harmonized to keyword flags when updated. No backwards
  compatibility constraints ([AGENTS.md](../AGENTS.md))." — Live
  CLI confirms stage 1 still takes `query` positionally, stage 3
  still takes `input` and `prompt` positionally. The "should be
  harmonized" note is a call to action that hasn't been done, and
  this document is supposed to describe present state. Move the
  harmonization plan to
  [`plans/`](.) and have interfaces.md merely note that stages 1 and
  3 use positional args today.

- `[nit]` [`docs/full-text-retrieval.md:105-117`](../docs/full-text-retrieval.md)
  — contains an embedded code example of `RetrievalMeta`. Should
  instead link directly to
  [`src/laglitsynth/fulltext_retrieval/models.py`](../src/laglitsynth/fulltext_retrieval/models.py)
  since this doc is a descriptive reference and the model is the
  source of truth. Same for the `RetrievalRecord` block at lines
  89–101. Currently the inline code matches the real models, but
  it's duplication that can and will drift (e.g. the `error` field
  wording differs slightly).

- `[nit]` [`docs/deduplication.md:53-64`](../docs/deduplication.md)
  — embeds `DeduplicationMeta` class body. Same argument: link to
  [`src/laglitsynth/catalogue_dedup/models.py`](../src/laglitsynth/catalogue_dedup/models.py)
  instead of duplicating. And the inline example references
  `tool: str = "laglitsynth.catalogue_dedup.dedup"` which matches
  the code — good — but has been correct only because someone
  kept them in sync.

- `[nit]` [`docs/full-text-extraction.md:112-122`](../docs/full-text-extraction.md)
  — ditto for `ExtractedDocument`, `ExtractionMeta`, `TextSection`.

- `[nit]` [`docs/eligibility.md:46-66`](../docs/eligibility.md) —
  inline `EligibilityVerdict` / `EligibilityMeta` class bodies.
  These models aren't implemented yet, so the inline code is
  hypothetical. When they are added, remove the duplication and
  link to the module.

- `[nit]` [`docs/full-text-retrieval.md:176-188`](../docs/full-text-retrieval.md)
  — the `Retrieval summary:` example uses `NNN` placeholders, which
  complies with AGENTS.md's "No fabricated numbers". Compare with
  actual stderr format in
  [`src/laglitsynth/fulltext_retrieval/retrieve.py:362-368`](../src/laglitsynth/fulltext_retrieval/retrieve.py):
  the code prints per-source lines like
  `"  Retrieved Oa:   4  (20.0%)"` — "Retrieved (OA)" in the doc
  vs "Retrieved Oa" from `source.replace("_", " ").title()` in the
  code. The capitalization differs (`OA` vs `Oa`) and the bracketing
  differs. Low-priority cosmetic drift.

- `[nit]` [`docs/grobid-lessons.md`](../docs/grobid-lessons.md) and
  [`docs/two-pass-extraction.md`](../docs/two-pass-extraction.md) —
  these are "lessons learned" from a prior prototype and are clearly
  written in present tense. They don't need renaming but refer to
  pipeline stages by old name ("Stage 7 (eligibility assessment)",
  "Stage 8 (data extraction)"). Worth a quick rename pass.

- `[nit]` [`src/laglitsynth/`](../src/laglitsynth/) — most public
  functions have docstrings, but a few in retrieval/extraction are
  bare (e.g.
  [`src/laglitsynth/fulltext_retrieval/retrieve.py:27-32,36-37,40-45,48,76,99,128,145,233,271`](../src/laglitsynth/fulltext_retrieval/retrieve.py)
  have no docstrings; the private helpers are self-explanatory but
  `work_id_to_filename` is used elsewhere too). Not a blocker.

- `[nit]` [`src/laglitsynth/models.py`](../src/laglitsynth/models.py)
  — `_Base` has no docstring. Given
  [`docs/data-model.md`](../docs/data-model.md) explicitly calls out
  "extra=ignore" as a design decision, a one-line docstring pointing
  to that decision (and to the rationale) would help.

## CLI UX

The CLI works and `--help` output is coherent. Main issues are: (1)
stage 1 and 3 still use positional arguments while 2, 4, 5, 6 use
keyword `--input` / `--output-dir` (the interfaces.md "harmonize"
action is still outstanding); (2) stage 3 exposes legacy flags
(`--reject-file`) that don't fit the flag-don't-filter pattern; (3)
help text is terse and inconsistent in style across subcommands.

### High priority

- `[should-fix]` Flag harmonization (interfaces.md's unfinished
  action). Current state:
  - `catalogue-fetch`: positional `query`, `-o/--output`,
    `--from-year`, `--to-year`, `--max-records`. No `--input`
    because it fetches from the network.
  - `catalogue-dedup`: `--input`, `--output-dir`.
  - `screening-abstracts`: positional `input`, positional `prompt`,
    `-o/--output`, `--model`, `--threshold`, `--base-url`,
    `--reject-file`, `--max-records`, `--dry-run`.
  - `screening-adjudication`: `--input`, `--output-dir`.
  - `fulltext-retrieval`: `--input`, `--output-dir`, `--email`,
    `--manual-dir`, `--skip-existing`, `--dry-run`.
  - `fulltext-extraction`: `--pdf-dir`, `--output-dir`,
    `--grobid-url`, `--timeout`, `--skip-existing`.

  `screening-abstracts` is the clear odd one out. Converting
  positional `input` → `--input` and positional `prompt` →
  `--prompt` would bring it into line with the rest. `catalogue-fetch`
  has a natural argument (the query), so positional is fine there.
  This matches the explicit plan in
  [`docs/interfaces.md:191-194`](../docs/interfaces.md).

- `[should-fix]` [`src/laglitsynth/screening_abstracts/screen.py:147-150`](../src/laglitsynth/screening_abstracts/screen.py)
  — `--reject-file` flag exists and
  [`docs/interfaces.md:174`](../docs/interfaces.md) still lists it,
  but the flag-don't-filter principle (interfaces.md lines 7–13)
  explicitly removes the Work-record split. Once the code is
  updated to write only verdicts, `--reject-file` should be
  removed. Keep a deprecation note in the plan; remove cleanly per
  the "Green field, no backwards compatibility" principle.

- `[should-fix]` `screening-abstracts` vs `fulltext-retrieval`
  inconsistent treatment of `--dry-run`:
  - `screening-abstracts --dry-run`: "Print verdicts to stderr
    without writing output" (per help) but actually does not run
    the LLM as a preview — it calls the LLM for every work and
    discards output (see
    [`src/laglitsynth/screening_abstracts/screen.py:190-272`](../src/laglitsynth/screening_abstracts/screen.py)).
  - `fulltext-retrieval --dry-run`: "Report what would be retrieved
    without downloading" — actually reports the planned source
    without network calls (see
    [`src/laglitsynth/fulltext_retrieval/retrieve.py:159-187`](../src/laglitsynth/fulltext_retrieval/retrieve.py)).

  Different semantics under the same flag name. Options: (a)
  rename stage 3's to `--no-write` / `--preview`; (b) make stage 5's
  also "run everything, just don't persist"; (c) define `--dry-run`
  consistently ("skip the expensive/side-effectful operation, print
  what would happen") and change stage 3's to skip the LLM calls.
  Pick one and make the help text match.

- `[should-fix]` `--skip-existing` semantics are also not
  documented identically:
  - `fulltext-retrieval --skip-existing`: "Skip works already
    retrieved successfully" (skips `retrieved_*` statuses, retries
    `failed` and `abstract_only`; see
    [`src/laglitsynth/fulltext_retrieval/retrieve.py:233-241`](../src/laglitsynth/fulltext_retrieval/retrieve.py)).
  - `fulltext-extraction --skip-existing`: "Skip PDFs that already
    have an `ExtractedDocument` record" (see
    [`src/laglitsynth/fulltext_extraction/extract.py:112-118`](../src/laglitsynth/fulltext_extraction/extract.py)).

  Consistent in spirit; differences in help wording are fine. But
  `screening-abstracts` has no `--skip-existing`, and it is the
  most expensive stage (LLM per abstract). A one-line note in
  [`docs/filter-abstracts.md`](../docs/filter-abstracts.md) about
  "re-running replaces the output" would prevent user surprise
  when the `open(output, "x")` call throws `FileExistsError`.

- `[should-fix]` [`src/laglitsynth/screening_abstracts/screen.py:192,193,196`](../src/laglitsynth/screening_abstracts/screen.py)
  — the code uses `open(output, "x")` (exclusive create) for the
  output, verdicts, and reject files. If the output path exists
  (e.g. re-running with an explicit `-o` to overwrite), the
  command fails with a bare `FileExistsError` traceback rather
  than a clear error. Catch and print a helpful message ("Output
  file already exists: <path>. Delete it or pick a different
  `-o`.").

- `[should-fix]` [`src/laglitsynth/catalogue_fetch/fetch.py:126-128`](../src/laglitsynth/catalogue_fetch/fetch.py)
  — the `SystemExit` message for a missing API key is clear and
  actionable. Good.
  [`src/laglitsynth/screening_abstracts/screen.py:107-114`](../src/laglitsynth/screening_abstracts/screen.py)
  — the `_preflight` catches *any* `Exception` and translates to a
  `SystemExit`. That obscures the real error (model not pulled,
  auth, TLS, etc.). Consider logging the underlying exception.
  [`src/laglitsynth/fulltext_extraction/extract.py:163-169`](../src/laglitsynth/fulltext_extraction/extract.py)
  — same GROBID health check pattern; also terse.

### Medium priority

- `[should-fix]` Help text style is inconsistent at the argument
  level. Subcommand-level helps all end with a period (good).
  Argument-level helps are mostly period-less noun phrases but
  differ in richness: some include defaults (`"Ollama model name
  (default: gemma3:4b)"`), some don't (`"Manual PDF dir"` in
  [`src/laglitsynth/fulltext_retrieval/retrieve.py:256`](../src/laglitsynth/fulltext_retrieval/retrieve.py)
  is terse and omits the runtime default of `<output-dir>/manual/`).
  Pick a guideline ("short noun phrase, no period, include default
  in parentheses where applicable") and apply uniformly.

- `[should-fix]` `screening-abstracts` has no required-argument
  enforcement for `--threshold` — defaults to 50, fine — but the
  help string says `"Relevance score cutoff, 0-100 (default: 50)"`
  without validating the range. Not a blocker but a passing
  mention.

- `[should-fix]` [`src/laglitsynth/fulltext_retrieval/retrieve.py:256`](../src/laglitsynth/fulltext_retrieval/retrieve.py)
  — `--manual-dir` help is `"Manual PDF dir"`. Should be
  "Directory to scan for manually placed PDFs (default:
  `<output-dir>/manual/`)" — current help is terse and omits the
  default, which is computed at runtime (line 275).

- `[should-fix]` [`src/laglitsynth/fulltext_retrieval/retrieve.py:337-348`](../src/laglitsynth/fulltext_retrieval/retrieve.py)
  — `unretrieved.txt` generation scans for PDF existence
  (`pdf_path.exists()`) rather than looking at the `RetrievalRecord`
  status. In `--dry-run` mode, PDFs do not exist on disk, so every
  work is written to `unretrieved.txt` as "unretrieved". This is
  likely wrong for dry-run: either skip writing `unretrieved.txt`
  in dry-run, or derive it from the planned statuses. The inline
  comment at line 342 ("Re-scan: just check if the PDF exists")
  suggests the author knew this was a shortcut.

- `[should-fix]` [`src/laglitsynth/catalogue_fetch/fetch.py:155,168`](../src/laglitsynth/catalogue_fetch/fetch.py)
  — the help text `"Maximum number of results to fetch (default:
  199)"` is inaccurate: the argparse default is `None`, and the
  199 is a runtime safety cap applied in `run()`. The help text
  makes it sound like argparse will inject 199, which then affects
  how a downstream caller sees `args.max_records`. The current
  behavior is arguably fine (the warning at line 166–172 tells the
  user), but the help is misleading. Either:
  - Move `default=199` into `add_argument` and remove the special
    casing; or
  - Change the help to `"Cap on number of results. Defaults to
    199 as a safety limit; pass explicitly to fetch more."`

- `[nit]` [`src/laglitsynth/cli.py:24-45`](../src/laglitsynth/cli.py)
  — `main()` has no docstring. Minor.

- `[nit]` [`src/laglitsynth/cli.py:29`](../src/laglitsynth/cli.py)
  — `subparsers = parser.add_subparsers(dest="command")`. Missing
  `required=True`. On Python 3.12, running `laglitsynth` with no
  subcommand falls through to the `if args.command is None` check
  and prints help + exits 1 — that works. But using `required=True`
  is cleaner and gives a friendlier error, and then the explicit
  fallback at lines 41–43 is no longer needed.

- `[nit]` Error messages could be more consistent in voice:
  - fetch: "OPENALEX_API_KEY environment variable is not set."
  - screening: "Cannot reach Ollama at {base_url}. Is `ollama
    serve` running?"
  - extraction: "GROBID is not running at {grobid_url}.\nStart it
    with: docker run ...".

  All actionable, but stylistically mixed. Not a blocker.

- `[nit]` [`src/laglitsynth/fulltext_retrieval/retrieve.py:363-368`](../src/laglitsynth/fulltext_retrieval/retrieve.py)
  — summary output uses `source.replace("_", " ").title()`, which
  produces e.g. `"Retrieved Oa"`. Pretty, but stylistic. Matters
  when users copy/paste or grep. The doc example
  ([`docs/full-text-retrieval.md:180-188`](../docs/full-text-retrieval.md))
  uses `"Retrieved (OA)"` form — worth aligning one to the other.

### Low priority

- `[nit]` `catalogue-fetch` prints `"Query matches {total}
  works."` to stderr (at
  [`src/laglitsynth/catalogue_fetch/fetch.py:92`](../src/laglitsynth/catalogue_fetch/fetch.py))
  before applying `max_records`. A follow-up "will fetch only N of
  these" when a cap applies would reduce surprise.

## Open questions

Two items where I wasn't sure what the intended state is, rather than a
concrete proposal:

- Is the flag-don't-filter refactor of stage 3
  ([`docs/interfaces.md:7-13,70-73,152-157`](../docs/interfaces.md))
  still the intended near-term direction? If so, updating stage-3
  code, stage-3 CLI, stage-4 docs, and README all hinge on that
  decision. If not, interfaces.md should be updated.

- Should per-stage docs be renamed to match the canonical CLI names
  (`docs/fetch-publications.md` → `docs/catalogue-fetch.md`, etc.)?
  It's the consistent choice, but it creates many link updates.
  Worth asking before doing the rename.

## Positive notes

Not the review's remit, but worth mentioning since the request was to
focus on the surface:

- [`docs/glossary.md`](../docs/glossary.md) and
  [`docs/interfaces.md`](../docs/interfaces.md) are well-written,
  up-to-date (modulo the issues above), and internally consistent.
  Terminology in the glossary is the canonical reference.
- [`docs/pipeline.md`](../docs/pipeline.md)'s stage table and mermaid
  diagram are very useful as an overview; fixing the stage-name
  mismatches will make it self-consistent too.
- [`AGENTS.md`](../AGENTS.md) principles are concrete and
  well-phrased. The surface review found few principle violations
  once terminology drift is excluded.
- [`src/laglitsynth/cli.py`](../src/laglitsynth/cli.py) dispatcher is
  clean and scales as more stages land — each stage registers its
  own subparser. No changes needed.
