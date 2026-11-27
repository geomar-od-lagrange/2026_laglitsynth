# Cross-machine and storage convention

The pipeline runs across more than one machine: the operator's laptop
for the pipeline stages, with GROBID in Docker and the LLM stages
calling the review's hosted endpoint; and one or more collaborators'
machines, where PDFs are fetched by hand behind their own institutional
access. The two halves of the [pipeline](pipeline.md) meet at the
self-contained [corpus](corpus.md), so a machine running the analysis
half needs `data/corpus/` and nothing else from the first half. This
document states the conventions that make that work — the project
boundary, the path contract, per-machine credentials, and the safe
direction to sync each `data/` subdir. It is the written form of the
"many machines" and "where to store things" sections of
[running-the-pipeline.md](explorations/running-the-pipeline.md); read
that exploration for the reasoning, this document for the rules.

## The project is the working directory

A project — one literature review — is a clone of this repository with its
own [`data/`](../data) directory. There is no separate project or
run-grouping object: `data/` is a flat set of per-stage subdirs
([`data/catalogue-fetch/`](../data), [`data/catalogue-dedup/`](../data),
[`data/fulltext-retrieval/`](../data), …) as laid out in
[interfaces.md](interfaces.md), and the working directory itself is the
boundary that holds them together. Two independent reviews — a different
query, a different deduplicated catalogue — live in two separate clones,
not two subtrees of one `data/`. Moving a project between machines means
moving (a subset of) its `data/` tree; the clone travels with it or is
re-cloned on the far side.

## Run from the root; keep `data/` where it is

Every stage is run from the repository root. Two cwd-relative defaults
make this load-bearing:

- `--data-dir` defaults to `data` relative to the current directory, so
  the per-stage subdirs resolve under `<repo>/data/` only when the cwd is
  the repo root.
- `.env` is read from the current directory by
  [`load_env_var`](../src/laglitsynth/dotenv.py) (`Path(".env")`), as a
  fallback for `--api-key` / `--email` when the flag (and, for the LLM
  stages, the `LLM_API_KEY` environment variable) is absent.

Stage outputs store **repo-relative** paths — for example a
`PdfProvenanceRecord`'s `pdf_path` is `pdfs/<stem>.pdf` under `data/`,
not an absolute path. This is what makes the tree portable: rsync the
same relative subtree to another machine, run from that machine's repo
root, and the recorded paths resolve unchanged. The contract, stated
plainly: **always run from the project root and keep `data/` at its fixed
relative location.** Do not pass an absolute `--data-dir`, and do not
relocate `data/` outside the repo root, or the recorded relative paths
stop resolving on the next machine.

## `.env` is per-machine and never synced

Each machine that runs stages has its own `.env` (see
[.env.example](../.env.example)), and it is never copied between
machines: it is a credentials file and has no place in a synced data
tree. Collaborators run no stage and need no `.env`. Keep it out of any
rsync of `data/` (it lives at the repo root, not under `data/`, so a
`data/`-scoped sync already excludes it); when setting up a new machine,
write a fresh `.env` there. The explicit `--api-key` / `--email` flags
always win over `.env` (as does an exported `LLM_API_KEY` for the LLM
stages), and the `.env` read is announced on stderr (`Loaded <KEY> from
.env`), never silent — see
[interfaces.md](interfaces.md#configuration-flags-first-env-fallback-for-credentials).

## The division of labour

The stage graph suggests a natural split across machines:

- Laptop — every stage the operator drives:
  [`catalogue-fetch`](catalogue-fetch.md),
  [`catalogue-dedup`](catalogue-dedup.md),
  [`screening-abstracts`](screening-abstracts.md),
  [retrieval](fulltext-retrieval.md) and [`corpus-assemble`](corpus.md),
  and the analysis half
  ([`fulltext-extraction`](fulltext-extraction.md),
  [`fulltext-eligibility`](eligibility.md),
  [`extraction-codebook`](extraction-codebook.md)).
  GROBID runs in Docker and the LLM stages call the review's hosted
  endpoint (see [external-services.md](external-services.md)), so no stage
  needs local GPU. Because the analysis half reads only the corpus, it can
  move to any other machine with GROBID by copying `data/corpus/`.
- Collaborators — paywalled PDFs fetched through their own institutional
  access from an export bundle, returned as a folder (see the drop-zone
  rule below). This is the one step that cannot be centralised, because
  access is per-person; the
  [diversified-retrieval design](../plans/done/fulltext-retrieval-diversified.md)
  is built around it.

### Drop-zone rule for collaborator PDFs

The shared PDF store is single-writer: only A (the operator driving the
pipeline) ever writes `data/pdfs/` and its `provenance.jsonl`. Collaborators
B–D never run a stage that writes A's `data/` — not retrieval, not import.
They receive an export bundle, fetch PDFs through their own access, and hand
A a *folder* of PDFs; A then runs
[`fulltext-retrieval-import`](fulltext-retrieval.md#fulltext-retrieval-import)
to integrate it. The shared file store is a drop zone for those folders, not
a shared mutable store: B–D push artifacts, A integrates them serially.
Because A is the sole writer, the whole-file `provenance.jsonl` never races —
there is no second writer to overwrite a concurrent edit. This is what lets
provenance stay a plain read-all/rewrite-atomic record with no journal or
locking.

## Syncing `data/`: safe direction per subdir

Syncing between machines is rsync of subtrees of `data/`. The right
direction and merge mode differ by subdir, because the stages differ in
how they write. The categories:

- The PDF store, `data/pdfs/` — not rsynced. It is versioned in the
  private `data/` submodule ([pdf-store.md](pdf-store.md)): A commits and
  pushes after retrieval or import, other machines `git pull` inside
  `data/`.
- Rewritten whole — the corpus, `data/corpus/`. `corpus-assemble`
  replaces it in one step on the machine that holds the store and the
  screening verdicts. Move it from there as a unit: remove the far
  side's old `data/corpus/` first and copy the new one in, since a copy
  over the old one would leave PDFs of works no longer selected. Never
  assemble on two machines and merge. Its
  `corpus_id` tells both sides whether they hold the same corpus.
- Bulk, accumulating, newest-extraction-wins — the TEI,
  `data/fulltext-extraction/`. A TEI file is GROBID's output for one
  corpus PDF, keyed by work. Stage 6's `--skip-existing` leaves it alone
  while the work's corpus PDF is unchanged, but a work whose PDF changed
  in a re-assembly is extracted again into the same `tei/<stem>.tei.xml`,
  overwriting it, and a new record is appended to `extraction.jsonl`. So
  a union sync is not safe in either direction: rsync from a machine that
  extracted against an older corpus copies its old TEI over the new one,
  which then no longer matches the `content_sha256` of the work's last
  record. Sync `data/fulltext-extraction/` (the TEI and
  `extraction.jsonl` together) **one way, from the machine that last ran
  stage 6 on the current corpus**, and prefer to move it **once** rather
  than round-trip the bulk. Never `rsync --delete` it — a stale side
  would delete files the other side produced.
- Per-run-id, authoritative-side-wins — the gate stages
  ([`screening-abstracts`](screening-abstracts.md),
  [`fulltext-eligibility`](eligibility.md), and
  [`extraction-codebook`](extraction-codebook.md)) each write whole-file
  outputs under a `<run-id>/` leaf — its `verdicts.jsonl` /
  `records.jsonl`, the meta, and a `config.yaml` snapshot. A given
  run-id directory is produced whole by one machine; the machine that
  ran that stage is authoritative for that run-id. Sync the whole
  `<run-id>/` directory **from the authoritative side**, treating it as
  a unit, and **never `--delete`** — a wrong-way delete loses a run's
  verdicts irrecoverably. These are tiny JSONL plus meta and travel
  trivially, but they are whole-file outputs, not append-only logs: if
  two machines each ran the stage over part of the catalogue under the
  *same* run-id there is no merge tool. Different run-ids do not
  collide, so unioning distinct run directories across machines is safe;
  what is unsafe is two machines writing the **same** run-id and then
  overwriting each other.

The spine — the deduplicated catalogue and its siblings under
[`data/catalogue-dedup/`](../data) (`deduplicated.jsonl`,
`dropped.jsonl`, the meta) — is machine-independent, small, and has no
run-id. Rsync it once, early, and treat it as read-only on the
downstream machines; the first-half stages and `corpus-assemble` read
`deduplicated.jsonl`, so it must be identical everywhere they run. The
stage-1 files under `data/catalogue-fetch/` are likewise run-id-less
whole-file outputs; sync them deliberately from the side that produced
them, never with `--delete`. Stage 5 writes into the versioned store
`data/pdfs/` (above) and leaves only its per-invocation counts in
`data/fulltext-retrieval/retrieval-meta.json`, which belongs to the
machine that ran it and needs no sync.

What makes a wrong-direction sync dangerous is the combination of
whole-file outputs and `--delete`: rsyncing a stale `data/` over a fresh
one with `--delete` removes outputs the fresh side produced. The rule is
blunt and safe: **do not use `rsync --delete` on `data/` subdirs.** Move
the TEI, the per-run-id directories and the spine from their
authoritative side. For the PDF store specifically, the
[drop-zone rule](#drop-zone-rule-for-collaborator-pdfs) is the stronger
guard against exactly this — a single writer (A) means
`provenance.jsonl` is never synced from two diverging copies, because
collaborators hand back folders of PDFs that A integrates serially, not a
rewritten store.

## The run-ids travel with the data

Stage 3 writes under a screening `<run-id>/` leaf that `corpus-assemble` and
the export read, and stages 7 and 8 write under an analysis `<run-id>/` leaf
they share. Both must be the same across machines: if one machine runs
eligibility and another later runs extraction, both must use the same
analysis run-id.
`data/manifest.json` carries the screening run-id: `laglitsynth
manifest-init` mints it once, and the screening-side stages adopt it when no
run-id is passed. `data/corpus/corpus-meta.json` carries the analysis
run-id, which stages 7 and 8 adopt when `--run-id` is omitted. Both strings
travel with `data/` under rsync instead of living in operator memory and
shell history. An explicit run-id still wins where a stage needs to target
a different leaf. See [run-manifest.md](run-manifest.md).

The manifest is written once and never rewritten, so it is the one file under
`data/` with no sync direction to get wrong: copy it with the rest of the
tree, in either direction.
