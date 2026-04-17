# System review — 2026-04-16

System-level review of the laglitsynth pipeline at the point where stages
1–6 are implemented but stages 7–12 are not. Scope: integration,
design-level fit for the remaining stages, and reproducibility /
provenance of the six existing stages.

Source files examined: [src/laglitsynth/](../src/laglitsynth/) (all
stage subpackages, [cli.py](../src/laglitsynth/cli.py),
[io.py](../src/laglitsynth/io.py), [models.py](../src/laglitsynth/models.py)),
every doc under [docs/](../docs/), [plans/multi-run-consensus.md](multi-run-consensus.md),
[plans/done/](done/), and the populated [data/](../data/) tree.

Tag legend: `[blocker]` = pipeline will not compose or will silently
produce wrong results; `[should-fix]` = causes pain when next stage lands
or when someone needs to rerun; `[nit]` = stylistic / low-impact.

## Integration

The six implemented stages do run end-to-end today — the populated
[data/](../data/) tree proves it. But they compose only because every
stage currently hands the next one a `Work`-records file through the
legacy split-files contract. The flag-don't-filter contract promised by
[docs/interfaces.md](../docs/interfaces.md) is described in docs but
not implemented in code: there is no `laglitsynth.resolve` module,
[`screening-abstracts`](../src/laglitsynth/screening_abstracts/screen.py)
still writes an accepted/rejected Work split,
[`screening-adjudication`](../src/laglitsynth/screening_adjudication/adjudicate.py)
still copies Works into `included.jsonl`, and
[`fulltext-retrieval`](../src/laglitsynth/fulltext_retrieval/retrieve.py)
consumes `Work` records from that copy. Stage 7 cannot be added without
either (a) building resolve and refactoring stages 3–5 to match, or
(b) explicitly choosing to continue the split-files pattern and
updating [docs/interfaces.md](../docs/interfaces.md). This is the single
largest integration risk.

### Findings

#### I-1. `laglitsynth.resolve` does not exist [blocker]

[docs/interfaces.md](../docs/interfaces.md) lines 26–36 and lines 404–409
declare that every stage from 5 onward resolves its active work set via
a shared `resolve` module. A quick search of
[src/laglitsynth/](../src/laglitsynth/) confirms: no `resolve.py`, no
module import, no stage that joins verdicts against the deduplicated
catalogue.
[plans/done/stages-4-5-plumbing.md](done/stages-4-5-plumbing.md) lines
24–32 acknowledge this and defer the module explicitly.

What breaks because of its absence:

- Stage 5 ([fulltext_retrieval/retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py)
  line 288) reads `Work` records from `--input`, which is fed by the
  adjudication pass-through copy. If stage 3 were refactored to stop
  writing `accepted.jsonl`, stage 5 would have no input.
- Stage 4 ([screening_adjudication/adjudicate.py](../src/laglitsynth/screening_adjudication/adjudicate.py))
  is not yet a verdict-writer — it is a copy-of-works pass-through.
  Swapping it to `AdjudicationVerdict` requires resolve so that stage 5
  can re-derive the active set.
- Stage 7 (not yet implemented) is specced in
  [docs/eligibility.md](../docs/eligibility.md) lines 32–41 and
  [docs/interfaces.md](../docs/interfaces.md) lines 291–294 to take
  `--data-dir` and `--extractions`. Without resolve it has nothing to
  call.
- Multi-run consensus ([plans/multi-run-consensus.md](multi-run-consensus.md))
  assumes a `run_id` discriminator on `FilterVerdict` and verdict
  sidecars that accumulate across runs. Building resolve is prerequisite
  to any multi-run work.

What to change: build `src/laglitsynth/resolve.py` as the next piece of
work. Minimal surface: given a `--data-dir` (or an explicit
deduplicated-catalogue path plus a list of verdict sidecars and
thresholds), yield the active `Work` records plus a typed row of
upstream verdicts per work. Keep it narrow; grow it with each new gate.

#### I-2. Stage 3 still splits accepted vs. rejected Work records [blocker]

[docs/interfaces.md](../docs/interfaces.md) lines 66–73 and 153–158
explicitly call this out as an inconsistency that must change: "write
only verdicts, drop the Work-record split." The current code still
writes three files per run: the accepted Work records, a verdict sidecar,
and (optionally) a rejected file —
[screening_abstracts/screen.py](../src/laglitsynth/screening_abstracts/screen.py)
lines 227–238 branch on `verdict.accepted` and write Works into separate
streams; lines 169–171 compute timestamped output paths; line 119
generates a timestamped default filename.

The populated
[data/screening-abstracts/](../data/screening-abstracts/) directory
confirms this pattern (`accepted.jsonl`, `accepted.verdicts.jsonl`,
`rejected.jsonl`, `accepted.meta.json`). The artifact map in
[docs/interfaces.md](../docs/interfaces.md) line 62 specifies the only
output file should be `data/screening-abstracts/verdicts.jsonl` with no
timestamp and no Works.

What to change: strip the accept/reject writing out of
[screen.py](../src/laglitsynth/screening_abstracts/screen.py) `run()`,
drop `--output`, `--reject-file`, and the timestamped `_default_output_path`,
and write a single `data/screening-abstracts/verdicts.jsonl`. The
threshold stays on the command line and gets recorded in `FilterMeta` for
audit. The `reject-file` affordance can re-emerge as a post-hoc export
tool that consumes verdicts.

#### I-3. Stage 4 writes `included.jsonl` instead of an `AdjudicationVerdict` sidecar [blocker]

[screening_adjudication/adjudicate.py](../src/laglitsynth/screening_adjudication/adjudicate.py)
line 38 copies input Works to `included.jsonl`. No verdict file is
written;
[screening_adjudication/models.py](../src/laglitsynth/screening_adjudication/models.py)
defines only `AdjudicationMeta`. The artifact map
([docs/interfaces.md](../docs/interfaces.md) line 78) expects
`verdicts.jsonl` with `AdjudicationVerdict` records, one per work
reviewed. The model `AdjudicationVerdict` is not defined at all — it is
in the "models not yet defined" table ([docs/interfaces.md](../docs/interfaces.md)
line 348).

The current stage-4 behaviour is technically a valid pass-through of
Works, but it is fundamentally incompatible with the resolve design:
resolve needs verdicts to join against, not copies of Works. The copy
also obscures the intended semantics — stage 5's `--input` today is
whatever stage 4 wrote, which is "the entire screened+accepted set
unchanged", which means a threshold change in stage 3 silently
propagates through stage 4's copy without any marker saying "this work
was included because the LLM scored it 52, not because a human reviewed
it."

What to change: pair with I-1 and I-2. Define `AdjudicationVerdict`
(work_id, decision in {accept, reject, skip}, reviewer, timestamp,
reason) and have the pass-through emit either an empty verdict file or
an auto-`accept` verdict per resolved work. Stop writing
`included.jsonl`.

#### I-4. Stage 5 input path in the end-to-end doc is the copy, not the resolved set [should-fix]

[docs/interfaces.md](../docs/interfaces.md) line 274 has
`--input data/screening-adjudication/included.jsonl` in the end-to-end
sequence, which matches the current code but not the target design. Same
for the CLI-contract block at lines 186 and 273. Once I-1/I-2/I-3 land,
every caller should move to `--data-dir data/` plus threshold flags, as
described at [docs/interfaces.md](../docs/interfaces.md) lines 32–36.
Leave the doc until the code changes, but flag that the CLI surface will
need a shape change when resolve arrives.

#### I-5. Stage 6 parses `work_id` out of a PDF filename without cross-checking stage 5 [should-fix]

[fulltext_extraction/extract.py](../src/laglitsynth/fulltext_extraction/extract.py)
line 193 reconstructs the work ID as
`f"https://openalex.org/{pdf.stem}"`. This only works because stage 5
happens to name PDFs by the OpenAlex short ID
([fulltext_retrieval/retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py)
line 37). The two stages are coupled through a filename convention that
lives in neither model nor constant. If a manual PDF ever gets dropped
with a DOI-like name or any other stem, stage 6 will synthesise a bogus
OpenAlex ID and record it against the wrong work.

What to change: make the naming convention load-bearing by extracting
`work_id_to_filename` and its inverse into a small shared module (e.g.
`laglitsynth.ids`) and import from both stages. Alternatively: stage 6
reads `data/fulltext-retrieval/retrieval.jsonl`, filters to
`retrieval_status in retrieved_*`, and processes by work ID (looking up
`pdf_path` from the record). That would also let stage 6 report misses
(PDF on disk but no retrieval record, or vice versa). Either approach is
cheap compared to silent ID corruption.

#### I-6. Stage 5 `unretrieved.txt` logic re-scans PDFs instead of using the just-written `RetrievalRecord` [should-fix]

[fulltext_retrieval/retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py)
lines 335–348 write the unretrieved list by checking whether each PDF
exists on disk. This is correct in the happy case but drifts from the
authoritative artifact (`retrieval.jsonl`). If `--dry-run` is set the
file is empty regardless (line 346 `and not args.dry_run`). If a PDF was
written for a different earlier run and a later retry logged `failed`,
the PDF-exists check will declare "retrieved" even though the record
says failure. Better to derive the unretrieved set from the
`RetrievalRecord` iterable we just built — filter where `status in
{abstract_only, failed}` — and drop the filesystem scan. Same
information, one source of truth.

#### I-7. Stage 5 `--skip-existing` skips the unretrieved.txt write for previously-skipped works [nit]

Same block as I-6: the `for work in works` loop (line 338) skips IDs in
`skip_ids` (line 339–340). For skipped works we never re-emit them in
`unretrieved.txt` even if they previously failed. In practice a user
running with `--skip-existing` probably wants the same unretrieved list
as before, but the current code silently drops those lines on re-run.
If the loop iterates `retrieval.jsonl` instead (see I-6) this is fixed
for free.

#### I-8. `write_jsonl` uses exclusive-create mode, but stages also call `append_jsonl` on the same path [should-fix]

[io.py](../src/laglitsynth/io.py) line 35 opens with mode `"x"` (create
new, fail if exists). That is correct for dedup (line 38–39 of
[dedup.py](../src/laglitsynth/catalogue_dedup/dedup.py)) and
adjudication, but it means re-running stage 2 or stage 4 with an
existing output directory will crash mid-run. That is probably
intentional for catalog-level artifacts (don't overwrite), but it is not
matched by the rest of the pipeline:
[fulltext_retrieval/retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py)
uses `append_jsonl` which opens with `"a"`, and
[fulltext_extraction/extract.py](../src/laglitsynth/fulltext_extraction/extract.py)
also `append_jsonl`s into `extraction.jsonl` (line 226).

The result: stage 2 and stage 4 are non-resumable and abort on a re-run
with an existing dir; stage 5 and stage 6 are resumable via append and
`--skip-existing`. That inconsistency is fine per-stage (batch stages
don't need resumability) but should be documented in
[AGENTS.md](../AGENTS.md) or a module-level docstring. A single
[io.py](../src/laglitsynth/io.py) convention ("always overwrite unless
stage explicitly opts into append") would be easier to reason about.

#### I-9. `screening_abstracts.run()` crashes on second run (mode "x") [should-fix]

Follow-on to I-8: [screen.py](../src/laglitsynth/screening_abstracts/screen.py)
lines 192–196 open three files with mode `"x"`. Re-running screening
without deleting the output dir raises `FileExistsError`. The verdict
table (which is the only thing the new design keeps) should be
append-only with a `run_id` so multi-run consensus is even possible.
Right now there is no route from here to
[plans/multi-run-consensus.md](multi-run-consensus.md) without a
rewrite.

#### I-10. `read_works_jsonl` swallows `ValidationError` silently [should-fix]

[io.py](../src/laglitsynth/io.py) lines 19–29 log at `warning` level and
skip. For a pipeline that trusts its upstream artifact this is fine, but
it means if stage 3 changes the `Work` schema shape and stage 5 reads an
older file, the whole corpus silently drops records with no
non-warning-level signal. A `--strict` flag or a final summary line
(`N records read, M skipped due to validation`) would make the failure
mode louder. Given AGENTS principle "None means None" (line 41), a
record with partial data should validate; only truly malformed records
should hit the exception. Consider counting skips and reporting them at
the end of each stage.

#### I-11. Stage 3 writes Works that failed to classify as accepted=None but does not retain the reason [nit]

[screen.py](../src/laglitsynth/screening_abstracts/screen.py) line 103
creates a `FilterVerdict(work_id=work.id)` on LLM parse failure — all
fields default to `None`, including `reason`. A tiny improvement: record
`reason="llm-parse-failure"` so a human scanning verdicts can tell
"skipped because no abstract" apart from "LLM returned junk". Both
currently show up as identical null-everything rows.

#### I-12. Downstream sidecars are keyed by work_id; resolve must join on it [should-fix]

Every verdict/record model uses a `work_id: str` field ([FilterVerdict](../src/laglitsynth/screening_abstracts/models.py),
[RetrievalRecord](../src/laglitsynth/fulltext_retrieval/models.py),
[ExtractedDocument](../src/laglitsynth/fulltext_extraction/models.py)).
Stage 5 writes `work_id` as the full OpenAlex URL
(`https://openalex.org/W...`). Stage 6 reconstructs it from the PDF
filename stem (I-5). There is nowhere central that fixes the canonical
form. When resolve lands it should normalise on one form and error on
mismatch. Worth a short docstring in the as-yet-unwritten
[models.py](../src/laglitsynth/models.py) about `work_id` canonical form
(recommend the full URL, since that is what OpenAlex emits).

#### I-13. `test_screening_abstracts.test_run_writes_output_files` encodes the legacy contract [nit]

[tests/test_screening_abstracts.py](../tests/test_screening_abstracts.py)
lines 345–407 assert the accepted/rejected split exists. When I-2 lands
these tests need to be rewritten (expect `verdicts.jsonl` only). Noting
here so the refactor does not get blocked on "tests pass" — the tests
themselves lock in the legacy contract.

#### I-14. `DeduplicationMeta.dropped.jsonl` stores Works but has no merge-reason field yet [nit]

[docs/interfaces.md](../docs/interfaces.md) line 55 says the model for
`dropped.jsonl` is "Work + merge reason (TBD)". Today the stage writes
an empty file
([dedup.py](../src/laglitsynth/catalogue_dedup/dedup.py) line 39) so
nothing breaks yet, but the model gap should be closed before real dedup
logic lands — otherwise every dedup PR will need to design the shape of
a merge-reason record.

## Design

The 12-stage design is coherent on paper and the partial implementation
shows most of the backbone is sound. But several pieces that felt
optional during stages 1–6 will become forcing functions when stages
7–12 land: the missing resolve module (I-1), the half-implemented
flag-don't-filter contract (I-2, I-3), the ExtractedDocument shape not
carrying any retrieval or eligibility context, the `Work` model being
OpenAlex-native with no provenance / source-catalogue field, and the
DOC_NAME hyphen-vs-underscore asymmetry between docs/CLI/data-dirs and
Python packages. The multi-run consensus plan slots cleanly on top of
the verdict-sidecar design, but only after the verdict-sidecar design is
actually implemented in code.

### Findings

#### D-1. `ExtractedDocument` has no quality gate or status fields [should-fix]

[fulltext_extraction/models.py](../src/laglitsynth/fulltext_extraction/models.py)
lines 9–13 define `ExtractedDocument` as `work_id + sections + raw_text
+ extracted_at`. [docs/full-text-extraction.md](../docs/full-text-extraction.md)
lines 61–73 say every extraction should pass a quality gate and that
documents failing the gate "are flagged in the extraction record and
excluded from downstream LLM stages." There is no `quality` or `status`
field in the model. Stage 6 currently either writes an
`ExtractedDocument` on success or writes nothing on GROBID failure
(line 202–208 of
[extract.py](../src/laglitsynth/fulltext_extraction/extract.py)). That
"absence is the signal" approach works for hard failures but not for
the quality gate.

Why this matters for stages 7–12: stage 7 (eligibility) and stage 8
(codebook) both plan to read `ExtractedDocument` as their primary text
source. If stage 6 cannot mark a document "parsed but garbled", those
stages will LLM-extract from garbage text and emit plausibly-shaped but
wrong verdicts. The stage 9 human adjudicator cannot efficiently catch
this because the broken-text signal is buried in the raw text.

What to add: an `extraction_status` enum (`ok`, `quality_gate_failed`,
`empty`) and quality metrics (median sentence length, char-class entropy,
tokens in body). Stage 7's resolve filter can include "only
extraction_status == ok" and fall back to abstract-only otherwise.

#### D-2. `ExtractedDocument` assumes one-pass extraction [should-fix]

[docs/two-pass-extraction.md](../docs/two-pass-extraction.md) describes
a pattern applied to stages 7, 8, 11 where an initial locate pass
reduces the document to passages. Stage 6 today emits full sections and
the full concatenated `raw_text` (line 216–218 of
[extract.py](../src/laglitsynth/fulltext_extraction/extract.py)). That
is fine if pass-1 retrieval is done inline by stages 7/8, but nothing in
the `ExtractedDocument` model supports caching the pass-1 result or
recording which passages were selected. The codebook field
`passage_locations` ([docs/codebook.md](../docs/codebook.md) line 144)
is described as "load-bearing" — it is supposed to be the ground truth
for tuning pass-1.

Why this matters for stages 7–12: if pass-1 and pass-2 live in
different stages and share nothing, the `passage_locations` recorded by
stage 8 (extraction) cannot be cross-validated against what stage 7
(eligibility) saw. A richer `ExtractedDocument` — or a sibling
`ExtractedPassage` model — would let stages 7+ index into sections by
ID rather than by prose reference.

Pragmatic compromise: keep `ExtractedDocument` as is but ensure each
`TextSection` has a stable identifier (section index + title hash, for
example) so downstream stages can reference "section 3" reliably even
if section titles drift.

#### D-3. `Work` model carries no source-catalogue provenance [should-fix]

[catalogue_fetch/models.py](../src/laglitsynth/catalogue_fetch/models.py)
lines 80–99 define `Work` as an OpenAlex record with no `source` or
`catalogue_origin` field. [docs/glossary.md](../docs/glossary.md) line
34 and [plans/done/pr3-review-themes.md](done/pr3-review-themes.md)
lines 24–28 both flag that OpenAlex is a placeholder and Clarivate /
Web-of-Science may enter the catalogue later. The data model must
"document provenance and support deduplication" across sources
([pr3-review-themes.md](done/pr3-review-themes.md) lines 27–28), but no
such field exists.

Why this matters for stages 7–12: once WoS arrives, stage 2 (dedup)
needs to merge records from two catalogues and decide which fields win
(OpenAlex vs. Clarivate peer-review status, for example). That decision
needs to travel with the merged `Work`. Stage 9/10 will want to filter
"only WoS-indexed papers" for quantitative RQ results while keeping
OpenAlex-only records for vocabulary discovery (pr3 review theme:
preprints stay in the catalogue). That is impossible without a
provenance field.

What to add: `source_catalogues: list[str]` (e.g. `["openalex"]`,
later `["openalex", "wos"]`) and `catalogue_ids: dict[str, str]`
(mapping source name → source-native ID). Keep the existing `id` field
as the primary key for the pipeline's internal use.

#### D-4. `Work` model has no peer-review / preprint discriminator [should-fix]

Same table: `primary_location.source.type` and `type` are the only
current signals for whether a work is a preprint or a peer-reviewed
article. That is reconstructable but not a first-class field.
[plans/done/pr3-review-themes.md](done/pr3-review-themes.md) lines
13–19 explicitly mandate that preprints stay in the catalogue but must
be excludable from quantitative analyses.

Why this matters for stages 7–12: stage 10 (quantitative synthesis)
will want to stratify results by peer-review status. The flag-don't-filter
pattern means stage 2 should not drop preprints; it should just tag
them so downstream resolve can filter. The tag does not exist.

What to add: a derived boolean `is_peer_reviewed: bool | None` on
`Work`, computed at ingest from OpenAlex fields (`type ==
"preprint"`, `primary_location.source.type == "repository"`, etc.), or
a richer `publication_status` enum. Make the derivation a single
function in one place so it can be re-run if OpenAlex schema shifts.

#### D-5. Hyphenated-vs-underscored DOC_NAME convention is a footgun [should-fix]

Stage docs use hyphens (`screening-abstracts`), CLI subcommands use
hyphens, data directories use hyphens (`data/screening-abstracts/`), but
Python packages use underscores (`src/laglitsynth/screening_abstracts/`).
Meta sidecars record the underscored form in `tool`
(`laglitsynth.screening_abstracts.screen`). That is Python-legal and
readable, but it means any script that wants to map "CLI subcommand" →
"module to import" has to translate, and any doc that wants to link
both the docs page and the source package has to write two separate
paths.

Why this matters for stages 7–12: stages 7–12 all need resolve and all
need to emit their tool-name in meta sidecars. The mapping logic —
hyphen to underscore — lives in the author's head, not in code. A
minor slip (e.g. stage 10 `synthesis_quantitative` vs. stage 10
`synthesis-quantitative`) will break resolve's ability to look up the
right sidecar if it is ever keyed on stage name.

What to change: nothing mechanical unless resolve starts using stage
names as keys. If it does (and it probably should, since `params.json`
in [plans/done/retuning-propagation.md](done/retuning-propagation.md)
key thresholds by stage), define a constant map in
`laglitsynth.stages` (one dict from CLI name to package name to doc
path) and import it everywhere. Cheap insurance.

#### D-6. `_Base(extra="ignore")` is right for ingest, potentially wrong for internal models [should-fix]

[models.py](../src/laglitsynth/models.py) line 5 sets `extra="ignore"`
on the root `_Base`. The rationale
([docs/data-model.md](../docs/data-model.md) lines 17–19) is explicitly
about OpenAlex adding new fields silently. That is the right choice for
`Work` and its nested OpenAlex types.

But every other model in the pipeline inherits the same config:
`FilterVerdict`, `AdjudicationMeta`, `RetrievalRecord`, `ExtractedDocument`,
`ExtractionMeta`, and every planned model for stages 7–12. For these
internal models, silently dropping unknown fields is a foot-gun:

- If someone typos a field name when writing a verdict (e.g.
  `relevence_score` instead of `relevance_score`), Pydantic will
  silently drop it and the reader will get `None` for the real field.
- When [plans/multi-run-consensus.md](multi-run-consensus.md) adds a
  `run_id` field, older runs missing it need to error loudly or be
  handled explicitly — `extra="ignore"` makes that distinction
  disappear.

Why this matters for stages 7–12: every new model inherits permissive
behaviour. When extraction records land, missing a codebook field silently
vs. erroring is a material difference for what "the pilot run found" means.

What to change: split `_Base` into `_OpenAlexBase(extra="ignore")` for
OpenAlex-sourced records and `_InternalBase(extra="forbid")` (or just
`BaseModel` with default `extra="allow"` but mypy-strict) for verdicts
and metadata. Migrating 8 files to a new base is a one-line change each.

#### D-7. Meta-file shapes are diverging per stage [should-fix]

A table of what every existing meta records:

| Stage | Timestamp | Tool | Version | Input path | Counts | Prompt | Model | Threshold | Commit SHA | Output paths |
|---|---|---|---|---|---|---|---|---|---|---|
| FetchMeta | fetched_at | yes | alpha | no | total_count, records_written | no (has `query`) | n/a | n/a | no | no |
| DeduplicationMeta | deduplicated_at | yes | alpha | no | input_count, output_count, duplicates_removed, by_rule | n/a | n/a | n/a | no | no |
| FilterMeta | filtered_at | yes | alpha | no | accepted, rejected, skipped | yes | yes (name only) | yes | no | no |
| AdjudicationMeta | adjudicated_at | yes | alpha | no | input_count, output_count, human_reviewed | n/a | n/a | n/a | no | no |
| RetrievalMeta | retrieved_at | yes | alpha | no | total, retrieved, abstract_only, failed, by_source | n/a | n/a | n/a | no | no |
| ExtractionMeta | extracted_at | yes | alpha | no | total_pdfs, extracted, failed | n/a | grobid_version | n/a | no | no |

Every meta has timestamp + tool + version + counts. None has input file
paths, git commit, or cross-stage join keys. They are close enough to
deduplicate into a shared base, say `_RunMeta` with timestamp, tool,
tool_version, tool_commit_sha, input_paths (list), counts (dict), and a
stage-specific `details` sub-object. The current divergence is minor but
will grow as stages 7–12 land.

Why this matters for stages 7–12: `EligibilityMeta` and
`DataExtractionMeta` will want prompts and models (like FilterMeta) plus
upstream-count tracking (like DeduplicationMeta). Without a common base
each stage reinvents both.

What to change: pull common fields into `_RunMeta` in
[laglitsynth/models.py](../src/laglitsynth/models.py). Keep per-stage
subclasses for stage-specific details. Low-risk refactor. Cascade I-1/I-2
will touch these files anyway — do it then.

#### D-8. Multi-run consensus fits cleanly, except for I-9 [nit]

[plans/multi-run-consensus.md](multi-run-consensus.md) plans a `run_id`
field on verdicts and record models, append semantics in
`verdicts.jsonl`, and a separate `consensus` subcommand. The
verdict-sidecar design supports this once I-2/I-3 land. No design
changes needed beyond:

- `FilterVerdict`, `EligibilityVerdict`, `ExtractionRecord` gain
  `run_id: str`.
- `FilterMeta` etc. become list-of-entries or grow per-run sub-records.
- Stage 3 must switch from mode `"x"` (exclusive create) to append
  (covered by I-8/I-9).

The plan is sensible; it just requires the verdict-sidecar contract to
be implemented first.

#### D-9. Stage 9 corrections model is not specified for a flag-don't-filter world [should-fix]

[docs/interfaces.md](../docs/interfaces.md) lines 124–131 says
`ExtractionCorrection` stores "per-field corrections with original and
corrected values" and that downstream stages apply corrections at read
time. That is consistent with flag-don't-filter, but
[plans/done/retuning-propagation.md](done/retuning-propagation.md) line
30 describes stage 9 as writing "corrections to `ExtractionRecord`" —
which is fine — but the resolve module then needs to know how to
overlay corrections onto extraction records for stages 10/11.

Why this matters for stages 7–12: resolve's scope grows from "join
catalogue to verdicts" to "apply field-level corrections to
ExtractionRecords at read time." That is more complex. Consider whether
the corrections layer is genuinely needed for stage 10/11, or whether a
simpler "corrected extraction" artifact is sufficient (at the cost of
losing the audit trail).

Suggest: write the `ExtractionCorrection` and consensus
(`ExtractionConsensus` from multi-run-consensus) interactions explicitly
before stage 9 is planned. Today there is no design for "how does
resolve merge a field-level correction with a consensus verdict across
multiple runs." Answering that question now avoids a surprise later.

#### D-10. `AdjudicationMeta.tool` drifted from the doc [nit]

[docs/adjudication-screening.md](../docs/adjudication-screening.md) line
36 says `tool: str = "laglitsynth.screening_adjudication.screening"`.
The code
([screening_adjudication/models.py](../src/laglitsynth/screening_adjudication/models.py)
line 5) says `"laglitsynth.screening_adjudication.adjudicate"`. Code is
right (module name is `adjudicate.py`). Update doc.

#### D-11. No shared `laglitsynth.stages` enum / manifest [nit]

The six stage names are repeated in CLI setup, in doc tables, in meta
tool-names, and in data directory paths. If resolve needs to iterate
"every verdict sidecar in order," it should drive off a single list, not
six hardcoded strings. As stages 7–12 arrive this list grows to 12.
Consider a simple module-level list of dataclasses:

```python
@dataclass(frozen=True)
class Stage:
    number: int
    name: str          # "screening-abstracts"
    package: str       # "laglitsynth.screening_abstracts"
    data_dir: Path     # data/screening-abstracts/
    verdict_path: Path | None
```

Resolve then walks this list to collect upstream verdicts. Not required
today but a clear inflection point is coming.

#### D-12. `Work` carries enough metadata for late stages — with caveats [nit]

Scanning what late stages will need:

- Sub-discipline tagging (stage 8): `Work.keywords`, `Work.topics`,
  `Work.abstract` — enough.
- Peer-review status (stages 7, 10): inferable from `Work.type` and
  `Work.primary_location.source.type`, but as a derived boolean it
  should be first-class (D-4).
- Journal / publisher: `Work.primary_location.source` has it.
- Referenced works for citation network (optional extension,
  [docs/pipeline.md](../docs/pipeline.md) line 212): `referenced_works`
  is present.
- `cited_by_count` for centrality-lite: present.

So `Work` is largely sufficient and the two things worth adding are (a)
provenance / source-catalogue (D-3) and (b) peer-review discriminator
(D-4). Everything else stages 10–12 need lives in the extraction
records, not in `Work`.

#### D-13. `ExtractedDocument` is adequate for stages 7 and 8 but will need co-evolution with codebook [should-fix]

Stage 7 needs abstract + full text (eligibility). Today it reads
`Work.abstract` + `ExtractedDocument.raw_text` via resolve: fine.

Stage 8 needs full text + section titles (to implement two-pass):
`ExtractedDocument.sections[*].title + .text` is the plain form. Fine.

But stage 8's output (`ExtractionRecord`) is undefined (see gap in
[docs/interfaces.md](../docs/interfaces.md) lines 394–399). The codebook
([docs/codebook.md](../docs/codebook.md) lines 96–145) lists around 20
fields each with a companion `*_context` field. That is a lot of typed
surface to define in one go, and the codebook itself is explicitly a
hypothesis that will change during phase-3 review
([docs/codebook.md](../docs/codebook.md) lines 202–209). Consider
whether `ExtractionRecord` should be:

- **Option A:** a fully-typed Pydantic model generated from the
  codebook table (regenerated when the codebook changes).
- **Option B:** a thin wrapper `ExtractionRecord(work_id,
  run_id, fields: dict[str, FieldValue])` where `FieldValue` is
  `{value: str | None, context: str | None}`. Typing lives in a separate
  `Codebook` object that validates `fields` at runtime.

Option B is more flexible during phase 3 but loses compile-time type
safety — which matters less in a research tool where the codebook is
the real contract. Worth deciding explicitly before someone writes
`extraction_codebook/models.py`.

#### D-14. `TopicHierarchy` / `Topic` / `Keyword` are marked non-nullable but OpenAlex sometimes returns partial records [should-fix]

[catalogue_fetch/models.py](../src/laglitsynth/catalogue_fetch/models.py)
lines 60–77: `Topic.score: float`, `TopicHierarchy.id: str`,
`TopicHierarchy.display_name: str`, `Keyword.score: float`. These are
all non-optional. The fetch code at
[fetch.py](../src/laglitsynth/catalogue_fetch/fetch.py) line 107 skips
invalid records at validation time — so any record with a partial
topic/keyword entry is dropped entirely. Combined with AGENTS.md's
"None means None" principle (line 41), these should be `Optional` too
— otherwise the models are quietly throwing away whole papers because
of a per-topic score being missing. Worth an audit: run the fetcher
against a broad query, log validation skips, and relax the non-nullable
fields that actually fail.

## Reproducibility

Reproducibility is the weakest area. Meta sidecars capture _when_ a run
happened but not _how_ it could be replayed: no git commit / tool
version beyond the string `"alpha"`, no input file paths, no sampling
parameters for LLM stages (temperature, seed, top-p), no prompt
versioning beyond storing the prompt string, and no acknowledgment in
meta that LLM output is non-deterministic. Six months from now, someone
rerunning stage 3 with the same `FilterMeta.prompt` and `model` will get
different scores — and no part of the system will flag that.

### Findings

#### R-1. `tool_version` is hard-coded to `"alpha"` in every meta model [blocker]

[FetchMeta](../src/laglitsynth/catalogue_fetch/models.py) line 104,
[DeduplicationMeta](../src/laglitsynth/catalogue_dedup/models.py) line 6,
[FilterMeta](../src/laglitsynth/screening_abstracts/models.py) line 13,
[AdjudicationMeta](../src/laglitsynth/screening_adjudication/models.py)
line 6,
[RetrievalMeta](../src/laglitsynth/fulltext_retrieval/models.py) line 26,
[ExtractionMeta](../src/laglitsynth/fulltext_extraction/models.py) line 18.
All six set `tool_version: str = "alpha"`. There is no mechanism to
derive the real version from git or from `pyproject.toml`.

What to change: compute `tool_version` from
`importlib.metadata.version("laglitsynth")` at module load, or — more
useful for replay — capture a `tool_commit_sha` by calling `git rev-parse
HEAD` at run time (fallback to "unknown" if not a git checkout). Store
both in a shared base (see D-7). Six-month replay needs the commit
SHA; the version string is mostly cosmetic.

#### R-2. No meta records input file paths [blocker]

None of the existing meta models include the input file(s) they
consumed. [FilterMeta](../src/laglitsynth/screening_abstracts/models.py)
stores the prompt but not _which file_ was screened. If two catalogues
are fetched (say, `search_a.jsonl` and `old_papers.jsonl` — both of
which are sitting in
[data/catalogue-fetch/](../data/catalogue-fetch/) right now) and
concatenated before dedup, a future reader can't tell which catalogue(s)
fed the pipeline.

What to change: every meta grows an `input_paths: list[str]` field. For
stage 1 it's `[]`. For stage 2 it's `["data/catalogue-fetch/combined.jsonl"]`
(or the expanded list of `search_*.jsonl` files). For stage 3 it's the
dedup output. Store paths as strings (absolute or repo-relative); do not
try to keep them machine-independent.

#### R-3. No provenance for upstream counts [should-fix]

Each meta records its own output counts (e.g.
`FilterMeta.accepted_count`, `RetrievalMeta.retrieved_count`) but none
records the upstream count from which its run started. Without the
upstream count in the same sidecar, a reader cannot reconstruct the
PRISMA-style flow diagram from artifacts alone — they'd have to open
every upstream meta file in sequence.

What to change: add `input_count: int` (already in
[DeduplicationMeta](../src/laglitsynth/catalogue_dedup/models.py) and
[AdjudicationMeta](../src/laglitsynth/screening_adjudication/models.py)
— generalise) to every meta. Stage 3: `input_count = len of dedup
catalogue`. Stage 5: `input_count = len of post-adjudication active
set`.

#### R-4. `FilterMeta` does not record LLM sampling parameters [blocker]

[screen.py](../src/laglitsynth/screening_abstracts/screen.py) lines
44–55 call OpenAI's chat.completions with only `model`,
`response_format`, and `messages`. No `temperature`, no `top_p`, no
`seed`, no `max_tokens`. Whatever defaults the Ollama-via-OpenAI server
uses are in effect, which means the meta sidecar does not capture
enough to reproduce the run.

[FilterMeta](../src/laglitsynth/screening_abstracts/models.py) captures
`prompt`, `model`, `threshold`. No sampling params. No seed. No base URL
(so replays against a different Ollama instance silently produce
different results).

What to change: pass explicit `temperature=0.0` (and `seed=...` if the
server supports it) in the API call — make the stage as deterministic as
the model allows. Record sampling parameters in `FilterMeta`:

```python
class FilterMeta(_Base):
    ...
    prompt: str
    prompt_sha: str              # so re-runs can detect prompt drift
    model: str
    model_digest: str | None     # from /api/tags — ollama's digest
    temperature: float
    seed: int | None
    base_url: str
    threshold: int
```

The `model_digest` (Ollama returns it on `/api/tags`) is particularly
important: `gemma3:4b` today and `gemma3:4b` in six months may not be
the same weights. Without the digest there is no way to tell.

#### R-5. LLM non-determinism is not acknowledged anywhere [should-fix]

There is no note in [FilterMeta](../src/laglitsynth/screening_abstracts/models.py),
[docs/filter-abstracts.md](../docs/filter-abstracts.md), or
[docs/interfaces.md](../docs/interfaces.md) about rerun variance. A
reader might reasonably assume that with the same prompt + same model,
scores are reproducible. They are not.

[plans/multi-run-consensus.md](multi-run-consensus.md) addresses this
at the _consensus_ level but not in the stage-3 contract itself. Add a
one-liner in [docs/filter-abstracts.md](../docs/filter-abstracts.md)
stating that scores are expected to vary across runs, and link to the
multi-run plan for the intended mitigation. Helps any human re-running
the pipeline understand why they need multiple runs.

#### R-6. Prompts are embedded in source, not versioned [should-fix]

The system prompt in
[screen.py](../src/laglitsynth/screening_abstracts/screen.py) lines 22–29
is a module-level constant. The relevance-criterion prompt comes in via
CLI argv and is recorded in [FilterMeta.prompt](../src/laglitsynth/screening_abstracts/models.py).
The system prompt is not recorded anywhere.

That means the verdict sidecar can be interpreted only if you also have
the exact source file revision. Six months out, if someone edits the
system prompt, old verdicts produced under the old system prompt become
un-auditable.

What to change: store the system prompt in the meta too
(`FilterMeta.system_prompt: str`) or — cleaner — move both system and
user-prompt-template to a versioned file under
[docs/prompts/](../docs/prompts/) (create it), record the file path and
a SHA256 digest in the meta. This applies to every future LLM stage
(7, 8, 11).

#### R-7. No seed is passed to the LLM and no seed is recorded [should-fix]

Follow-on to R-4. OpenAI-compatible servers (including Ollama) typically
accept a `seed` parameter. Nothing in stage 3 sets one, nothing in
`FilterMeta` stores one. Even if the server doesn't enforce
determinism, recording the seed is useful for the audit trail. Set seed
= 0 (or a stage-controlled value) and record it.

#### R-8. `RetrievalMeta` does not record input paths, email used, or user-agent [should-fix]

[RetrievalMeta](../src/laglitsynth/fulltext_retrieval/models.py) stores
only timestamps and counts. Not the input path, not the email passed to
Unpaywall, not the user-agent sent, not the rate limits. The email is
especially load-bearing — if a reviewer asks "which Unpaywall account
was used to retrieve these PDFs?", there is no answer in the artifacts.
The user-agent is a liability check: the pipeline claims
`laglitsynth/0.1 (mailto:{email})`
([retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py) line
298), but that string is not in the meta.

What to change: add `input_path`, `email`, `user_agent`, `rate_limits`
(per-domain min_interval as a dict). Even for a research tool this
matters — a future rerun with a different email to Unpaywall is a
different retrieval population and results should not be conflated.

#### R-9. `ExtractionMeta` does not record GROBID model flavor [should-fix]

[ExtractionMeta](../src/laglitsynth/fulltext_extraction/models.py) has
`grobid_version`. GROBID's output varies considerably depending on which
model weights (CRF vs. BiDLSTM vs. ELMo-based) are active and on
consolidation settings. The stage passes `consolidateCitations=0`
([extract.py](../src/laglitsynth/fulltext_extraction/extract.py) line
105) but that setting is not in the meta.

What to change: record the full API parameters dict in the meta (as
`grobid_params: dict[str, str]`). If GROBID exposes model-flavour info
via `/api/version`, capture it too.

#### R-10. `DeduplicationMeta.by_rule` is empty because rules don't exist yet [nit]

[DeduplicationMeta.by_rule](../src/laglitsynth/catalogue_dedup/models.py)
line 11 is specced to carry per-rule counts
([docs/deduplication.md](../docs/deduplication.md) lines 55–64). The
pass-all MVP leaves it empty. Once real dedup lands, ensure the rule
keys match [docs/deduplication.md](../docs/deduplication.md) lines
18–40 (`openalex_id`, `doi`, `normalised_title_author_year`).

#### R-11. `FetchMeta.query` does not capture filters [should-fix]

[FetchMeta](../src/laglitsynth/catalogue_fetch/models.py) stores `query`
and nothing else about the search. The CLI accepts `--from-year`,
`--to-year`, `--max-records`
([fetch.py](../src/laglitsynth/catalogue_fetch/fetch.py) lines 145–156);
none of these are recorded. A reader can't tell from the artifact
whether a run was year-filtered.

What to change: add `from_year: int | None`, `to_year: int | None`,
`max_records: int | None`, and `fields: list[str]` (the OpenAlex
projection — currently the `FIELDS` tuple at line 24; capturing this
makes schema drift auditable).

#### R-12. `records_written` equals `total_count` in FetchMeta on success [nit]

[FetchMeta.total_count](../src/laglitsynth/catalogue_fetch/models.py)
line 107 is set to `count` (records actually written) at line 196,
_not_ the OpenAlex-reported total (which is printed to stderr at line 92
but discarded). The model field name `total_count` reads like
"OpenAlex's result-set size" but stores "number of records in our
file". That is misleading. Either rename to `records_written` /
`records_in_file` and add a separate `openalex_total: int` field, or
document explicitly in the model.

#### R-13. No summary of records skipped (due to validation) [should-fix]

[fetch.py](../src/laglitsynth/catalogue_fetch/fetch.py) lines 106–109
and [io.py](../src/laglitsynth/io.py) lines 28–29 both skip records on
`ValidationError` with a `warning` log. Neither counts them. The meta
does not record "N records were attempted but rejected for schema
reasons." That makes corpus size opaque: a user sees "99 records
fetched" in the meta but OpenAlex may have returned 120 and 21 were
dropped.

What to change: accumulate skip count and include it in every meta as
`validation_skipped: int` (default 0).

#### R-14. No cross-stage join keys — meta files do not reference each other [should-fix]

A run of the full pipeline produces six meta files, none of which
cross-reference. There is no `parent_run_id` or `input_meta_path` linking
`FilterMeta` back to `DeduplicationMeta`. If someone re-runs stage 2
with new data but does not re-run stage 3, the stage-3 meta still says
"filtered_at = some timestamp" but the upstream catalogue has changed,
and the stage-3 artifact is now stale without a marker saying so.

What to change: add `input_meta_paths: list[str]` or compute a content
hash of the input file and store `input_content_sha: str`. The content
hash approach is more robust — any stage can verify "do I still match
the input I was run against?" by hashing the input and comparing. The
hash is cheap for JSONL (content-addressable).

#### R-15. Timestamps are written in local-machine wall time (UTC) with no drift protection [nit]

Every stage uses `datetime.now(UTC).isoformat(...)`. That is fine for
provenance (UTC is standard) but "what time zone was the run" is not
recoverable from the string. Low-impact unless runs are distributed
across machines. Leave as-is, but consider logging the hostname /
machine ID once resolve starts federating runs.

#### R-16. Reproducibility of fulltext retrieval across time [should-fix]

Stage 5 depends on OA URLs published by OpenAlex, which are not
versioned or pinned. A PDF retrieved today from `publisher.com/paper.pdf`
may return a different file next year (corrected preprint, retracted
paper, paywalled). Nothing in `RetrievalRecord` records the PDF's
content hash or file size.

What to change: add `content_sha256: str | None` and `size_bytes: int |
None` to [RetrievalRecord](../src/laglitsynth/fulltext_retrieval/models.py).
Downstream stages can check that the same PDF is still in place; a
rerun detects content drift.

#### R-17. `ExtractedDocument` has no link back to the PDF it was extracted from [should-fix]

[ExtractedDocument](../src/laglitsynth/fulltext_extraction/models.py)
stores `work_id` and `extracted_at` but not the PDF path, nor a
content hash of the source PDF. If the PDF is replaced (see R-16), the
extraction cannot be invalidated automatically. Add `source_pdf_path:
str | None` and `source_pdf_sha256: str | None`. Six months out, a
reader can check whether the extraction still matches its source.

#### R-18. Stage 6 silently re-uses old `extraction.jsonl` if it exists (append mode) [should-fix]

[extract.py](../src/laglitsynth/fulltext_extraction/extract.py) line 226
uses `append_jsonl`. If `extraction.jsonl` has prior entries from an old
GROBID version, and the new run processes only new PDFs (due to
`--skip-existing`), the resulting file is a mix of different GROBID
versions. The `ExtractionMeta` records only the current run's GROBID
version, not the history. So the meta sidecar lies by omission.

Matches R-14 (cross-stage join keys) but inside a single stage: different
invocations leave different fingerprints. Solutions: include a
`grobid_version` field on each `ExtractedDocument` record (not just on
the meta), or make the meta a list of run entries.

#### R-19. `AdjudicationMeta` — no sampling strategy, no reviewer identity [should-fix]

Today the stage is pass-through so this is moot. But
[AdjudicationMeta](../src/laglitsynth/screening_adjudication/models.py)
line 5 has only `mode`, `input_count`, `output_count`,
`human_reviewed`. For a real adjudication the meta should record:
reviewer identity (initials suffice), sampling strategy
("stratified-by-score"), sample size, borderline-window definition,
random seed. Needed when stage 7 and stage 9 adjudication follow the
same pattern.

#### R-20. Prompts mentioned in eligibility doc are not yet in a versioned file [should-fix]

[docs/eligibility.md](../docs/eligibility.md) lines 100–117 includes a
draft prompt. When stage 7 is implemented, store the prompt under
[docs/prompts/](../docs/prompts/) (new dir) or
[src/laglitsynth/fulltext_eligibility/prompts/](../src/laglitsynth/fulltext_eligibility/)
(as a `.txt` or `.md` resource file loaded at runtime). Record the
resource name + content digest in `EligibilityMeta`. Apply the same
pattern retroactively to stage 3 (R-6) so the convention is consistent
across LLM stages. Do the same for stage 8 and stage 11 prompts.

## Summary

The six-stage pipeline runs end-to-end today. Getting stages 7–12 in
without friction requires three pieces of work, in order:

1. **Close the resolve gap.** Build `laglitsynth.resolve`, refactor
   stage 3 to stop splitting and stage 4 to write verdicts. Only then
   can stage 7 land without bolting onto the wrong contract. See I-1
   through I-4.
2. **Decide on `ExtractionRecord` shape.** The codebook will evolve; a
   fully-typed model vs. a `dict[str, FieldValue]` wrapper is the key
   design call before stage 8 is written. See D-13.
3. **Harden meta sidecars for replayability.** Capture git commit,
   input paths, LLM sampling params with seed, prompt content digest,
   and PDF content hashes. Shared `_RunMeta` base avoids doing this six
   times. See R-1 through R-20, especially R-1/R-2/R-4/R-6.

Low-risk wins that can ship independently: I-5 (extract `work_id`
utilities into a shared module), I-6/I-7 (derive unretrieved from
retrieval records), D-6 (split `_Base` into OpenAlex vs. internal), D-7
(common `_RunMeta` base), R-15/R-18 (per-record provenance in
extraction stage). These do not require any architectural decision.

All file-path references in this review are relative to the repo root
at commit `241d33b` on branch `wr/review-2026-04-16-01`.
