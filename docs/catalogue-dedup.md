# Catalogue deduplication

Stage 2 removes duplicate records from the retrieved catalogue before
screening effort is spent. Duplicates arise when multiple keyword searches
return overlapping result sets, or when the same work appears under different
OpenAlex IDs.

## Matching rules

Three rules are applied in order. The first matching rule wins.

### 1. OpenAlex ID

Exact match on `work.id`. Catches the trivial case where the same search
returns the same record twice.

### 2. Normalised DOI

Exact match on normalised DOI (lowercase, strip `https://doi.org/` prefix).
Catches the most common cross-search duplicates. Works without a DOI are
never matched by this rule.

### 3. Normalised title + first author + year

For works without a DOI, match on all three:
- Title: lowercase, strip punctuation, collapse whitespace.
- First author: `authorships[0].author.display_name`, lowercased.
- Year: `publication_year`.

All three must match. This is conservative — it will miss duplicates with
slightly different titles (e.g. preprint vs. published) but avoids false
merges.

## Tiebreak: completeness

When two records match, the one with more complete metadata survives:
prefer the record with a DOI over one without, then prefer the one with more
`authorships` entries, then keep the first encountered. The dropped record's
`work_id`, the surviving record's `work_id`, and the matching rule are
written to `dropped.jsonl` as a [`DroppedRecord`](../src/laglitsynth/catalogue_dedup/models.py).

## Data models

### DroppedRecord

```python
class DroppedRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dropped_work_id: str
    survived_work_id: str
    rule: str    # "openalex_id" | "doi" | "title_author_year"
```

### DeduplicationMeta

```python
class DeduplicationMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run: RunMeta      # tool, tool_version, run_at, validation_skipped
    input_count: int
    output_count: int
    duplicates_removed: int
    by_rule: dict[str, int]       # counts per matching rule
```

## Storage layout

```
data/catalogue-dedup/
  deduplicated.jsonl      # Work records after deduplication
  dedup-meta.json         # DeduplicationMeta
  dropped.jsonl           # one DroppedRecord per dropped duplicate
```

## CLI interface

`--input` accepts one or more paths or glob patterns. Multiple files are
concatenated and deduplicated in a single pass.

```
laglitsynth catalogue-dedup \
    --input "data/catalogue-fetch/*.jsonl" \
    --output-dir data/catalogue-dedup/
```

Multiple explicit paths also work:

```
laglitsynth catalogue-dedup \
    --input data/catalogue-fetch/search_a.jsonl \
            data/catalogue-fetch/search_b.jsonl \
    --output-dir data/catalogue-dedup/
```

## What is deferred

- Fuzzy title matching (Levenshtein, n-gram similarity).
- Preprint-vs-published reconciliation (matching arXiv IDs to DOIs).
- OpenAlex entity merging (querying OpenAlex for canonical work IDs).
- Human review of ambiguous matches.

These can be added when the duplicate rate and miss rate on the actual
corpus justify it.
