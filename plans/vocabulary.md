# Vocabulary-based keyword screening

A concept-tagged term list for deterministic keyword matching against
abstracts. Complements LLM screening by providing a fast, auditable,
independent relevance signal.

## Vocabulary format

A YAML file mapping terms (surface forms) to lists of tags. Each term is a
string expected to appear in abstracts. Tags indicate which concepts a term
belongs to. A term can carry multiple tags.

```yaml
Runge-Kutta: [integration_scheme]
OceanParcels: [software, lagrangian_framing]
Sargassum: [sub_discipline, object, tracer]
```

The full type is `dict[str, list[str]]` — no nesting, no wrapper keys.

## Building the vocabulary

##### Domain knowledge

The project team brainstorms terms for each concept group. The codebook
categories (integration scheme, time-step strategy, interpolation method,
sub-discipline) provide the tag vocabulary. This covers the core terms.

##### Term extraction from the existing corpus

Run term-frequency analysis over screened abstracts. High-frequency
domain-specific terms not yet in the vocabulary are candidates for addition.

##### Existing controlled vocabularies

AGU/EGU index terms, OceanParcels documentation glossary, numerical-methods
textbook indices. These fill gaps in the long tail.

The vocabulary grows iteratively. Start with domain knowledge, expand as
adjudication reveals gaps.

## Matching

- Case-insensitive.
- Word-boundary aware (`\b` regex boundaries) — "RK4" must not match
  "PARK4".
- Multi-word terms match as phrases — "particle tracking" matches the
  phrase, not "particle" and "tracking" independently.
- No stemming. List surface forms explicitly ("interpolation" and
  "interpolated" as separate entries if both are wanted). More predictable
  than algorithmic stemming.

## Merging vocabularies

Merging two vocabulary files is a dict merge where tag lists are unioned:

```python
def merge_vocabs(*vocabs: dict[str, set[str]]) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for vocab in vocabs:
        for term, tags in vocab.items():
            if term in merged:
                merged[term] |= tags
            else:
                merged[term] = set(tags)
    return merged
```

Terms appearing in only one side pass through unchanged. Tag order resolved
by sorting alphabetically.
