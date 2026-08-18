# Cross-machine and storage convention

The pipeline runs across more than one machine: a laptop for the light,
interactive stages; the [NESH HPC cluster](external-services.md#nesh-hpc-cluster)
for the GPU- and storage-heavy stages; and one or more collaborators'
machines for full-text retrieval behind their own institutional access.
This document states the conventions that make that work — the project
boundary, the path contract, per-machine credentials, and the safe
direction to sync each `data/` subdir. It is the written form of the
"many machines" and "where to store things" sections of
[running-the-pipeline.md](explorations/running-the-pipeline.md); read that
exploration for the reasoning, this document for the rules.

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
  fallback for `--api-key` / `--email` when the flag is omitted.

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

Each machine has its own [`.env`](../src/laglitsynth/dotenv.py), and it is
never copied between machines. A collaborator retrieving paywalled PDFs
needs **their own** Unpaywall email and OpenAlex key — their
institutional access, not yours — so the credentials must differ per
machine by design. `.env` is also a credentials file and has no place in
a synced data tree. Keep it out of any rsync of `data/` (it lives at the
repo root, not under `data/`, so a `data/`-scoped sync already excludes
it); when setting up a new machine, write a fresh `.env` there. The
explicit `--api-key` / `--email` flags always win over `.env`, and the
`.env` read is announced on stderr (`Loaded <KEY> from .env`), never
silent — see [interfaces.md](interfaces.md#configuration-flags-first-env-fallback-for-credentials).

## The division of labour

The stage graph suggests a natural split across machines:

- Laptop — [`catalogue-fetch`](interfaces.md#stage-1--catalogue-fetch-exists),
  [`catalogue-dedup`](interfaces.md#stage-2--catalogue-dedup-exists), and
  [`screening-abstracts`](interfaces.md#stage-3--screening-abstracts-exists)
  (or screening against a tunneled GPU). Light data, interactive work.
- NESH — the GPU- and storage-heavy stages
  ([`fulltext-extraction`](interfaces.md#stage-6--fulltext-extraction),
  [`fulltext-eligibility`](interfaces.md#stage-7--fulltext-eligibility-exists),
  [`extraction-codebook`](interfaces.md#stage-8--extraction-codebook-exists)),
  and ideally the corpus itself so the PDFs and TEI never have to come
  home. Keeping the bulk where the disk is is as much the reason to use
  NESH as the inference is. See the
  [NESH section of external-services.md](external-services.md#nesh-hpc-cluster)
  for the batch setup.
- Collaborators — full-text
  [`fulltext-retrieval`](interfaces.md#stage-5--fulltext-retrieval-exists)
  of paywalled PDFs via their own institutional access. This is the one
  stage that cannot be centralised, because access is per-person; the
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

- Bulk, fetch-once, union-safe — the PDFs and TEI. A PDF is fetched once
  per work and reused everywhere; a TEI file is GROBID's deterministic
  output for one PDF. These are content-addressed by work and never
  rewritten in place, so they only ever accumulate. Sync them by **union
  in either direction** (rsync without `--delete`), and prefer to move
  them **once** (laptop or collaborator → NESH) rather than round-trip
  the bulk. Never `rsync --delete` these — a stale side would delete
  PDFs the other side fetched.
- Per-run-id, authoritative-side-wins — the gate stages
  ([`screening-abstracts`](interfaces.md#stage-3--screening-abstracts-exists),
  [`fulltext-eligibility`](interfaces.md#stage-7--fulltext-eligibility-exists),
  and [`extraction-codebook`](interfaces.md#stage-8--extraction-codebook-exists))
  each write whole-file outputs under a `<run-id>/` leaf — its
  `verdicts.jsonl` / `records.jsonl`, the meta, and (for 7 and 8) a
  `config.yaml` snapshot. A given run-id directory is produced whole by
  one machine; the machine that ran that stage is authoritative for that
  run-id. Sync the whole `<run-id>/` directory **from the authoritative
  side**, treating it as a unit, and **never `--delete`** — a wrong-way
  delete loses a run's verdicts irrecoverably. These are tiny JSONL plus
  meta and travel trivially, but they are whole-file outputs, not
  append-only logs: if two machines each ran the stage over part of the
  catalogue under the *same* run-id there is no merge tool today (see the
  caveat below). Different run-ids do not collide, so unioning distinct
  run directories across machines is safe; what is unsafe is two machines
  writing the **same** run-id and then overwriting each other.

The spine — the deduplicated catalogue and its siblings under
[`data/catalogue-dedup/`](../data) (`deduplicated.jsonl`, `dropped.jsonl`,
the meta) — is machine-independent, small, and has no run-id. Rsync it
once, early, and treat it as read-only on the downstream machines; every
later stage joins against `deduplicated.jsonl`, so it must be identical
everywhere. The stage-1 (`catalogue-fetch`) and stage-5
(`fulltext-retrieval`) record files are likewise run-id-less whole-file
outputs; sync them deliberately from the side that produced them, never
with `--delete`.

What makes a wrong-direction sync dangerous is the combination of
whole-file outputs and `--delete`: rsyncing a stale `data/` over a fresh
one with `--delete` removes outputs the fresh side produced. The rule is
blunt and safe: **do not use `rsync --delete` on `data/` subdirs.** Union
the bulk, and move per-run-id directories and the spine from their
authoritative side. For the PDF store specifically, the
[drop-zone rule](#drop-zone-rule-for-collaborator-pdfs) is the stronger
guard against exactly this — a single writer (A) means
`provenance.jsonl` is never synced from two diverging copies, because
collaborators hand back folders of PDFs that A integrates serially, not a
rewritten store.

## The run-id travels in the manifest

Stages 3, 7, and 8 write under a `<run-id>/` leaf, and a coherent run needs
the same run-id across all three, including across machines: if NESH runs
eligibility and the laptop later runs extraction, both must use the same one.
`data/manifest.json` carries it. `laglitsynth manifest-init` mints the run-id
once, and every run-id-aware stage adopts it when `--run-id` is omitted, so
the string travels with `data/` under rsync instead of living in operator
memory and shell history. An explicit `--run-id` still wins where a stage
needs to target a different leaf. See [run-manifest.md](run-manifest.md).

The manifest also records each stage's resolved inputs and output, so a
machine that receives `data/` receives the lineage that produced it and can
resolve its own inputs from it. Paths are stored relative to the project root
where they sit under it, which is what lets a manifest move between checkouts
at different absolute locations — one more reason to keep to the
run-from-root contract above.

## Syncing the manifest

`data/manifest.json` is the one file under `data/` that is neither a bulk
artifact nor per-run-id, and it is rewritten whole on every stage append. So
it takes neither of the rules above: **never** sync it bidirectionally, and
never union it. Treat it as authoritative-side-wins, the same as the
per-run-id gate outputs, and copy it in one direction only.

The consequence is worth stating plainly, because it is a real limitation. If
NESH appends an eligibility entry while the laptop appends an extraction entry
to the same review, whichever manifest is copied last replaces the other
wholesale, and the entries recorded on the losing side are gone. The stages
themselves are unaffected — their outputs are still on disk — but a stage that
relied on resolving an omitted flag will then report a missing upstream entry,
and the path has to be passed explicitly. Merging two machines' stage logs is
the separate append-only-log problem, not something the manifest does today.
