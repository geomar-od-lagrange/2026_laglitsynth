# Thin TEI wrapper for `ExtractedDocument`

## Goal

Stop rewriting GROBID TEI XML into a lossy
`list[TextSection(title, text)]` + concatenated `raw_text` pair on
disk. Keep the TEI bytes — which stage 6 already writes to
[`data/fulltext-extraction/tei/<work_id>.tei.xml`](../src/laglitsynth/fulltext_extraction/extract.py) —
as the canonical artefact, and shrink the JSONL record to
`work_id`, `tei_path`, `content_sha256`, `extracted_at`. Add a small
accessor API that parses TEI lazily on demand
(`doc.sections()`, `doc.figures()`, `doc.citations()`,
`doc.bibliography()`) via namespaced XPath, returning typed views over
the bytes we already keep on disk.

Subsumes the flat-vs-recursive `parse_tei` question raised in
[plans/review-2026-04-16-code.md](review-2026-04-16-code.md) lines
389–401: nested `<div>` hierarchy is surfaced via a `children`
field on `Section`, so sub-section heads are no longer lost.

Unblocks stage 7+ consumers
([plans/review-2026-04-16-system.md](review-2026-04-16-system.md)
D-13) that want figures, citations, and bibliography — not just
flattened section text.

## Non-goals

- A pydantic mirror of TEI. The wrapper exposes only what stages
  7+ plausibly consume (sections, figures, inline citations,
  bibliography). Paragraph-level offsets, reference targets inside
  figures, affiliations on bibliography entries, and every other
  nested TEI element stay unmodelled. Add fields when a concrete
  consumer needs them.
- Deleting stage 6's existing GROBID call and TEI-writing code.
  Stage 6 still POSTs to GROBID and writes
  `output_dir/tei/<work_id>.tei.xml`. Only the downstream
  parse-and-persist layer changes: we drop `sections` and
  `raw_text` from the stored record and read TEI lazily through
  the wrapper.
- `extraction_status` enum and quality-gate metrics. Tracked
  separately on the [roadmap](roadmap.md) under "`ExtractedDocument`
  quality gate — defer until stage 7/8 consumer arrives" and in
  [plans/review-2026-04-16-system.md](review-2026-04-16-system.md)
  D-1. Deliberately disjoint from this plan.
- Stage 7 (`fulltext-eligibility`) implementation or spec
  rewrite. This plan lands the consumer-facing API; stage 7's plan
  wires it up.
- Full-text search, chunking, or embedding. Out of scope.
- `_Base` deletion, nested `_RunMeta`, `extra="forbid"` split.
  Delivered by the [reproducibility meta refactor](reproducibility-meta-refactor.md).
  This plan assumes that refactor has landed and builds on the
  post-refactor shape.

## Target state

### Storage layout

Unchanged layout, trimmer records:

```
data/fulltext-extraction/
  extraction.jsonl        # one ExtractedDocument per successfully parsed PDF
  extraction-meta.json    # ExtractionMeta (unchanged)
  tei/
    W1234567890.tei.xml   # canonical artefact, referenced by ExtractedDocument.tei_path
```

`tei_path` is stored as the relative string `"tei/<work_id>.tei.xml"`,
mirroring the convention `RetrievalRecord.pdf_path = "pdfs/<id>.pdf"`
set in [fulltext_retrieval/retrieve.py](../src/laglitsynth/fulltext_retrieval/retrieve.py).
Consumers resolve against their output directory base.

### `ExtractedDocument` shape

```python
class ExtractedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    work_id: str
    tei_path: str                # relative to output_dir, e.g. "tei/W123.tei.xml"
    content_sha256: str          # sha256 hex digest of the TEI bytes on disk
    extracted_at: str            # per-record wall-clock timestamp (unchanged)
```

`sections` and `raw_text` are removed from the stored record. The old
`TextSection` pydantic model is deleted; its replacement lives in the
wrapper module below.

### Wrapper module

New file [`src/laglitsynth/fulltext_extraction/tei.py`](../src/laglitsynth/fulltext_extraction/tei.py):

```python
class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None            # TEI xml:id if present, else None
    title: str | None         # None when <head> is missing; not defaulted to a sentinel
    paragraphs: list[str]     # direct-child <p> text, in document order
    children: list["Section"] # nested sub-divs, recursive


class Figure(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None
    label: str | None         # "1", "2a", etc. from <figure><label>
    caption: str | None       # <figDesc> text


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Inline citation pointer from the body text.
    target_id: str | None     # the #bibN id this points at, without leading "#"
    text: str                 # the citation string as it appears inline


class BibReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str | None            # xml:id of the <biblStruct> (e.g. "b12")
    authors: list[str]        # "Surname, F." strings; empty list if absent
    title: str | None
    year: str | None          # kept as string — TEI sometimes emits "2021a"
    doi: str | None
    raw: str                  # full text of the <biblStruct>, whitespace-normalised


class TeiDocument:
    """Lazy typed views over a TEI XML file on disk."""

    def __init__(self, path: Path) -> None: ...

    @classmethod
    def from_bytes(cls, data: bytes) -> "TeiDocument": ...

    @property
    def content_sha256(self) -> str: ...

    def sections(self) -> list[Section]: ...
    def figures(self) -> list[Figure]: ...
    def citations(self) -> list[Citation]: ...
    def bibliography(self) -> list[BibReference]: ...
```

### Accessor on `ExtractedDocument`

One helper on the model for the common case ("resolve my TEI"):

```python
class ExtractedDocument(BaseModel):
    ...
    def open_tei(self, output_dir: Path) -> TeiDocument:
        """Load the TEI referenced by self.tei_path, relative to output_dir."""
        return TeiDocument(output_dir / self.tei_path)
```

No other accessors on the model — the wrapper class owns the API.

## Design decisions

### Separate `TeiDocument` class, not methods on `ExtractedDocument`

`ExtractedDocument` is a pydantic record — a row in a JSONL file. A
class with lazy state, XPath queries, and cached parse results is a
different kind of object with a different lifecycle. Collapsing them
would either force `ExtractedDocument` to hold an `lxml.etree` handle
(non-serialisable, breaks the pydantic contract) or force every
accessor to re-read the file on each call. Keeping the record
minimal (`work_id` + `tei_path` + `content_sha256` + `extracted_at`)
and the wrapper separate (`TeiDocument(path)`) gives a clean split:
the record persists, the wrapper parses. `ExtractedDocument.open_tei`
is the one-line bridge.

### New module at `fulltext_extraction/tei.py`

Keeps the XML-specific code (lxml imports, namespace constants, XPath
strings) co-located with its only caller. `models.py` stays pure
pydantic. `extract.py` stays focused on the GROBID subprocess and
record-writing. Three files, three concerns.

### `tei_path` is relative to `output_dir`

Matches [`RetrievalRecord.pdf_path`](../src/laglitsynth/fulltext_retrieval/models.py)
which stores `"pdfs/<id>.pdf"` relative to the retrieval output
directory. Portable (data dirs move between machines) at the cost of
one `output_dir` argument at read time — the same cost the retrieval
stage already pays. Absolute paths would be simpler but break as soon
as the data directory is copied.

### `content_sha256` lands now

The roadmap bullet reads "carry `tei_path` (+ `content_sha256` once
reproducibility lands)". The [reproducibility meta
refactor](reproducibility-meta-refactor.md) explicitly deferred
`input_content_sha` on JSONL inputs, on the grounds that work IDs
are the stable identity and file hashing adds plumbing without
answering a question we have. The TEI case is different: the TEI
file *is* the canonical identity of an extraction (stage 6 can be
re-run with a newer GROBID and produce a different TEI for the same
`work_id`), and the hash is cheap — stage 6 already holds the bytes
in memory right before writing. Compute once at extract time, store
on the record. Consumers comparing `ExtractedDocument` records
across runs now have a first-class identity to compare against.

### Lazy parse with per-instance caching

Accessors parse TEI on first call and cache the parsed `etree._Element`
on the `TeiDocument` instance. Subsequent calls to `sections()`,
`figures()`, etc. re-use the cached tree. Within one accessor call,
results are computed fresh (XPath is cheap; caching all four result
lists would double the memory for no ergonomic win). Eager parse on
construction would make `TeiDocument(path)` do I/O — surprising for
an object that might never be queried. Per-call parse with no cache
would re-parse on every accessor call — wasteful for the common
"open once, ask three things" pattern.

### Recursive `Section` with `children`, not flat with `depth`

GROBID nests sub-divs (Methods → Sub-methods) and the current
[`parse_tei`](../src/laglitsynth/fulltext_extraction/extract.py) flat
scan drops them entirely, as
[plans/review-2026-04-16-code.md](review-2026-04-16-code.md) lines
396–401 call out. A flat list with `depth: int` preserves order but
forces consumers to re-reconstruct the tree every time. A recursive
`children: list[Section]` preserves the structure GROBID emits and
lets a consumer that only cares about top-level sections iterate the
outer list. Stage 7's section-level eligibility check ("was the
methods section garbled?") and stage 8's two-pass selector
(["locate the methods section, then extract from it"](../docs/two-pass-extraction.md))
both want tree structure. A helper `Section.walk()` or
`Section.flatten()` can be added later if a consumer actually wants
the flat view.

### Accessor scope: four methods, small return types

`sections()`, `figures()`, `citations()`, `bibliography()`. Each
return type carries the smallest field set a plausible stage-7+
consumer would use: figure label/caption, citation target + text,
bibliography `authors/title/year/doi/raw`. `raw` on `BibReference`
is the escape hatch — any field we didn't model is still there in
the full text of the `<biblStruct>`. Resist adding fields until a
consumer names one.

### Failure modes: eager-existence-check on `TeiDocument.__init__`, lazy parse errors

`TeiDocument(path)` raises `FileNotFoundError` if the file does not
exist — fast feedback for the common "I forgot the output_dir
argument" mistake. Actual XML parsing happens on the first accessor
call and raises `lxml.etree.XMLSyntaxError` on malformed TEI.
`ExtractedDocument` load is unaffected: pydantic validation only
checks that `tei_path` is a string. Stage 7's resolve layer — when
it lands — can add a defensive "does the TEI file exist?"
pre-check before calling `open_tei`.

### Migration: delete the pre-refactor `extraction.jsonl`

Green-field per [AGENTS.md](../AGENTS.md). The old record shape
carries `sections` and `raw_text` and is `extra="forbid"` after the
reproducibility refactor, so loading old records through the new
model fails fast. Cheaper to re-run stage 6 than to write a
migration script. Existing `tei/` files on disk are re-used — they
are the canonical artefact and don't change shape.

### Hardened XML parser stays

`etree.XMLParser(resolve_entities=False, no_network=True)` is
already in use in [extract.py](../src/laglitsynth/fulltext_extraction/extract.py).
`TeiDocument` uses the same parser. GROBID is local and trusted,
but the hardening is cheap insurance against a future "feed a
TEI from somewhere else" misuse.

## Implementation sequence

Each step is a self-contained commit. Run `pixi run test` and
`pixi run typecheck` before each commit.

### 1. Write `fulltext_extraction/tei.py`

New module with `Section`, `Figure`, `Citation`, `BibReference`, and
`TeiDocument`. Port the existing namespace constant and hardened
parser from [extract.py](../src/laglitsynth/fulltext_extraction/extract.py).
Implement:

- `sections()`: recursive walk of `<text>/<body>/<div>`; each `<div>`
  becomes a `Section` with `id` (from `xml:id`), `title` (from
  `<head>` text or `None`), `paragraphs` (from direct-child `<p>`
  elements), and `children` (recursive on nested `<div>`). Figures
  are stripped before walking so captions don't leak into
  `paragraphs`.
- `figures()`: all `<figure>` under `<body>`, each yielding a
  `Figure` with `id`, `label`, `caption`.
- `citations()`: all `<ref type="bibr">` under `<body>`; `target_id`
  is the `target` attribute stripped of its leading `#`.
- `bibliography()`: all `<biblStruct>` under `<listBibl>`, each
  yielding a `BibReference` with the field set above. `raw` is the
  full text of the `<biblStruct>` with whitespace collapsed.

No changes outside this new file in this step.

### 2. Rewrite `ExtractedDocument` and drop `TextSection`

In [`fulltext_extraction/models.py`](../src/laglitsynth/fulltext_extraction/models.py):

- Delete `TextSection`.
- Rewrite `ExtractedDocument` to `work_id`, `tei_path`,
  `content_sha256`, `extracted_at`. `extra="forbid"` preserved.
- Add `ExtractedDocument.open_tei(output_dir: Path) -> TeiDocument`.

`ExtractionMeta` is unchanged.

### 3. Rewire `fulltext_extraction/extract.py`

In [`fulltext_extraction/extract.py`](../src/laglitsynth/fulltext_extraction/extract.py):

- Delete `parse_tei` and `_element_text` (moved into `tei.py` in
  step 1, or dropped if unused).
- The per-PDF loop now: POST to GROBID → receive `tei_bytes` →
  write `tei_path.write_bytes(tei_bytes)` → compute
  `content_sha256 = hashlib.sha256(tei_bytes).hexdigest()` →
  construct `ExtractedDocument(work_id=..., tei_path=f"tei/{fname}.tei.xml",
  content_sha256=..., extracted_at=...)` → `append_jsonl`.
- No `sections` / `raw_text` assembly.
- `_load_existing(output_dir)` keeps working — it reads
  `work_id` off the new record shape unchanged.

### 4. Tests

[`tests/test_fulltext_extraction.py`](../tests/test_fulltext_extraction.py):

- Remove `parse_tei` imports and the `TestParseTei*` test classes.
  Port their intent to `tests/test_fulltext_tei.py` (new file)
  against `TeiDocument.sections()`. The existing empty-body,
  missing-head, no-divs, and figure-stripped cases all translate
  directly.
- Add nested-`<div>` tests that the current flat `parse_tei`
  cannot express: a `<div><head>Methods</head><div><head>Sub</head>...`
  structure exposes `children[0].title == "Sub"`.
- Add malformed-XML test: `TeiDocument.from_bytes(b"not xml")`;
  `sections()` raises `XMLSyntaxError`.
- Update the `run` integration tests to assert the new record shape
  (`tei_path`, `content_sha256` present; no `sections`, no
  `raw_text`).

[`tests/test_fulltext_tei.py`](../tests/test_fulltext_tei.py) (new):

- `TestTeiDocumentSections`: multi-section, missing-head, no-divs,
  empty-body, figure-stripped, recursive-nested-divs.
- `TestTeiDocumentFigures`: figure with label and caption; figure
  with no label.
- `TestTeiDocumentCitations`: inline `<ref type="bibr"
  target="#b3">Smith 2020</ref>` yields
  `Citation(target_id="b3", text="Smith 2020")`.
- `TestTeiDocumentBibliography`: `<biblStruct xml:id="b3">` with
  authors, title, year, doi, raw.
- `TestTeiDocumentFailure`: missing-file raises `FileNotFoundError`
  at construction; malformed-XML raises `XMLSyntaxError` at first
  accessor call.

### 5. Docs

[docs/fulltext-extraction.md](../docs/fulltext-extraction.md):

- Data model section: replace the `TextSection` / `ExtractedDocument`
  listing with the new shape. `ExtractedDocument.sections` and
  `.raw_text` are gone; `.tei_path` and `.content_sha256` are in.
- Add a short "Reading TEI" subsection pointing at
  [`fulltext_extraction/tei.py`](../src/laglitsynth/fulltext_extraction/tei.py)
  and the four accessors. Link to `TeiDocument`, `Section`,
  `Figure`, `Citation`, `BibReference`.
- Update the "TEI XML parsing" subsection: drop "flat list of
  sections" language — sections are hierarchical now.
- Storage layout block: `extraction.jsonl` description changes
  from "one `ExtractedDocument` per successfully parsed PDF"
  (carrying sections) to "... carrying `tei_path` +
  `content_sha256`". TEI files in `tei/` are now "the canonical
  extraction artefact", not a debugging aid.

[docs/interfaces.md](../docs/interfaces.md): update the stage 6
artifact description to reflect the trimmer record.

### 6. Migration cleanup

Delete [`data/fulltext-extraction/extraction.jsonl`](../data/fulltext-extraction/)
during migration. Keep the `tei/` subdirectory. Re-running
stage 6 with `--skip-existing` would produce empty output because
the old `work_id` set is now empty — by design: every record needs
to be re-emitted with the new shape.

Two operator flows are acceptable:

- Re-run stage 6 end-to-end against the existing PDFs. GROBID is
  called again. Cleanest.
- Write a one-off script that walks `data/fulltext-extraction/tei/`,
  hashes each `.tei.xml`, and emits new `ExtractedDocument`
  records. Avoids a second GROBID run but adds a throwaway script.
  Not recommended unless GROBID-unavailable.

Pick "re-run stage 6". Drop the one-off script option.

## Test migration summary

Removed: `parse_tei` unit tests in
[`tests/test_fulltext_extraction.py`](../tests/test_fulltext_extraction.py).
Their intent moves to `tests/test_fulltext_tei.py` (six classes,
covering sections / figures / citations / bibliography / failure
modes).

Changed: `run` integration test in
`tests/test_fulltext_extraction.py` asserts the new record shape.

New: `tests/test_fulltext_tei.py` covering `TeiDocument`.

## Risks and rollback

- Stage 7 plan is not yet written. If stage 7's resolve design
  wants a different accessor shape (e.g. section-by-id lookup),
  the four methods here may need to grow. The four return types
  are small and additive; growing them doesn't break existing
  callers.
- `content_sha256` is new metadata that no consumer reads yet.
  That's fine — it is the kind of field that becomes useful the
  moment multi-run extraction or cross-run comparison lands.
  Cost to compute at extract time is one hash of the TEI bytes
  already in memory.
- Lazy parse plus per-instance cache means a consumer holding a
  `TeiDocument` across many accessor calls accumulates lxml state.
  Release by dropping the reference. Not a leak, but worth noting
  for any stage-7 batch loop.
- Rollback: revert the single cutover commit. Green-field per
  [AGENTS.md](../AGENTS.md); no external consumers.

## Verification

Automated:

- `pixi run typecheck` passes with zero issues.
- `pixi run test` passes, including the new `test_fulltext_tei.py`
  suite.

Manual end-to-end after a small real retrieval + GROBID run:

```sh
laglitsynth fulltext-extraction \
    --pdf-dir data/fulltext-retrieval/pdfs/ \
    --output-dir data/fulltext-extraction/ \
    --grobid-url http://localhost:8070
# Expect: data/fulltext-extraction/extraction.jsonl with records
#         of shape {work_id, tei_path, content_sha256, extracted_at};
#         data/fulltext-extraction/tei/*.tei.xml unchanged in shape.
```

Then, in a Python REPL:

```python
from pathlib import Path
from laglitsynth.fulltext_extraction.models import ExtractedDocument
from laglitsynth.io import read_jsonl

output_dir = Path("data/fulltext-extraction")
doc = next(read_jsonl(output_dir / "extraction.jsonl", ExtractedDocument))
tei = doc.open_tei(output_dir)
print([s.title for s in tei.sections()])
print([f.label for f in tei.figures()])
print(len(tei.bibliography()))
```

Expect titles, figure labels, and a non-zero bibliography length
for a typical journal article.

## Open questions

1. Should `Section.paragraphs` be `list[str]` or `list[Paragraph]`
   with a richer model (inline-citation targets, offsets)?
   Default: `list[str]`. Inline citations are already surfaced by
   `citations()` at document scope; tying them to paragraphs
   is useful but out of scope until a consumer asks.
   FB: list of strings is fine.
2. Should `BibReference.authors` be `list[str]` or `list[Author]`
   (given name / surname split)? Default: `list[str]` of
   `"Surname, F."` form. Matches the shape stages 9–12 bibliographic
   comparison will want; split later if needed.
   FB: Do we have an author model already? Then author. Otherwise: str.
3. Should `TeiDocument` expose a `walk()` helper that yields
   `(section, depth)` pairs for consumers that do want a flat
   view? Default: no — add only when a consumer names the need.
  FB: no.