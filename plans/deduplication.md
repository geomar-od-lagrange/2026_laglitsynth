# Deduplication (stage 2)

Remove duplicate records from the retrieved catalogue before screening
effort is spent. Duplicates arise when multiple keyword searches return
overlapping result sets, or when the same work appears under different
OpenAlex IDs.

## Prototype scope

Exact matching only. No fuzzy logic, no preprint-vs-published
reconciliation. The goal is to wire stage 2 into the pipeline with correct
input/output contracts. Tune the matching when we see how much duplication
actually exists and what kinds of duplicates the naive approach misses.

## Matching rules

Apply in order. If any rule matches, the later record is dropped.

### 1. OpenAlex ID

Exact match on `work.id`. This catches the trivial case where the same
search returns the same record twice.

### 2. DOI

Exact match on normalised DOI (lowercase, strip `https://doi.org/`
prefix). This catches the most common cross-search duplicates. Works
without a DOI are never matched by this rule.

### 3. Normalised title + first author + year

For works without a DOI, match on all three:
- Title: lowercase, strip punctuation, collapse whitespace.
- First author: `authorships[0].author.display_name`, lowercased.
- Year: `publication_year`.

All three must match. This is conservative — it will miss duplicates with
slightly different titles (e.g. preprint vs. published) but will not
produce false merges.

## What gets kept

When duplicates are found, keep the record with the most complete metadata
(prefer the one with a DOI, prefer the one with more `authorships`
entries, break ties by keeping the first encountered). Log dropped records
with the reason and the ID of the record they were merged into.

## Data model

No new models needed. The output is a JSONL of `Work` records — same
schema as the input, just fewer of them.

### DeduplicationMeta

```python
class DeduplicationMeta(_Base):
    tool: str = "laglitsynth.dedup.deduplicate"
    tool_version: str = "alpha"
    deduplicated_at: str
    input_count: int
    output_count: int
    duplicates_removed: int
    by_rule: dict[str, int]       # counts per matching rule
```

## Storage layout

```
data/dedup/
  deduplicated.jsonl      # Work records after deduplication
  dedup-meta.json         # DeduplicationMeta
  dropped.jsonl           # dropped records with merge reason
```

## CLI interface

```
laglitsynth deduplicate \
    --input data/openalex/combined.jsonl \
    --output-dir data/dedup/
```

If multiple search result files need to be combined first, concatenate
them before deduplication. The dedup stage does not handle multi-file
input — that is a trivial `cat` operation.

## What to defer

- Fuzzy title matching (Levenshtein, n-gram similarity).
- Preprint-vs-published reconciliation (matching arXiv IDs to DOIs).
- OpenAlex entity merging (querying OpenAlex for canonical work IDs).
- Human review of ambiguous matches.

All of these can be added when the duplicate rate and miss rate on the
actual corpus justify it.
