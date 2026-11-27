# Catalogue search widening

Ad-hoc measurements of 27 November 2026 on how much in-scope work the
catalogue misses, and which OpenAlex queries would find it. They feed
[plans/catalogue-search-widening.md](../../plans/catalogue-search-widening.md).
The candidate query list with per-query figures is in
[catalogue-search-widening-queries.tsv](catalogue-search-widening-queries.tsv).

## Method

The catalogue of October 2026 came from six OpenAlex `search=` queries with
2,000 records each, 9,149 works after deduplication (see
[first-full-run-2026-11.md](first-full-run-2026-11.md)).

Each probe fetches one relevance-ranked slice of a query from OpenAlex and
keeps the works the catalogue does not hold. It samples 20–30 of those that
have an abstract and screens them.

The luna key of the GEOMAR LiteLLM proxy had reached its budget, so Claude
Haiku subagents did the screening. Each was given the stage-3 question from
[the screening criteria](../../examples/screening-criteria/lagrangian-oceanography.yaml),
with notes on scope, and returned two judgements per work:

- `score`: probability (0–100) that the work tracks particles, tracers or
  objects in an ocean flow, judged from the abstract. "Relevant" means a
  score of at least 50.
- `keep`: true unless the abstract makes clear the work does not track or
  simulate anything in an ocean flow. This is the recall-oriented rule:
  oil-spill and search-and-rescue papers count when they simulate drift, and
  an abstract that is silent on method is kept for the full text to decide.

Every screening batch also held 120 catalogue works that luna had scored:
60 at 90 or more, and 60 at 10 or less. Agreement with luna:

| Round | `score` agrees with luna | Luna ≥ 90 kept | Luna ≤ 10 kept |
|---|---|---|---|
| 1 | 117 / 120 | | |
| 2 | 117 / 120 | 57 / 60 | 2 / 60 |
| 3 | 114 / 120 | 58 / 60 | 4 / 60 |

Each per-query share rests on 12–30 abstracts, so treat it as about ±15–20
points.

### Search syntax

These observations come from the OpenAlex API in November 2026:

- `search=` is stemmed and ranked by relevance over title, abstract and
  full text.
- Upper-case `AND` / `OR` / `NOT`, parentheses and quoted phrases work.
- Wildcards are rejected ("Wildcards (* or ?) require exact (no-stem)
  search").
- Plain multi-word queries are loose. "oil spill trajectory" matches
  32,728 works and `"oil spill" AND trajectory` matches 15,693.
- Because of the full-text matching, a query's total count says nothing
  about relevance.

## The current queries were truncated

Five of the six queries hit the 2,000 cap. The share of works scoring at
least 50 with luna and the share found eligible at full text, by rank
within each query's own results:

| Query | Ranks 0–500 | 1,500–2,000 | Eligible at 1,500–2,000 |
|---|---|---|---|
| Lagrangian oceanography | 51 % | 47 % | 49 % |
| Lagrangian connectivity ocean | 67 % | 36 % | 48 % |
| Lagrangian ocean modelling | 54 % | 33 % | 32 % |
| ocean particle tracking | 41 % | 18 % | 16 % |
| Lagrangian particle tracking | 16 % | 18 % | 30 % |
| virtual drifter trajectories (1,144 hits) | 67 % | 3 % (ranks 1,000–1,144) | 7 % |

Paging deeper adds little. At ranks 2,000–2,200, between 48 and 71 of 200
works are new, because other queries already found the rest. Of those new
works, the share Haiku scored relevant was:

| Query | Ranks 2,000–2,200 | 4,000–4,200 | 8,000–8,200 |
|---|---|---|---|
| Lagrangian oceanography | 63 % | 18 % | 8 % |
| Lagrangian connectivity ocean | 22 % | 0 % | (fewer hits) |
| Lagrangian ocean modelling | 21 % | 15 % | 33 % (9 of 27) |
| ocean particle tracking | 4 % | 15 % | 4 % |

## Citation chaining

The 2,135 eligible papers reference 48,195 distinct works in OpenAlex
(`referenced_works`). Of these, 2,189 are in the catalogue and 46,006 are
not. Relevant share among the missing ones, by how many eligible papers cite
them:

| Cited by | Works | Relevant | Kept |
|---|---|---|---|
| 5 or more | 2,635 | 25 % | 33 % |
| 2–4 | 10,528 | 10 % | 19 % |
| 1 | 32,843 | 0 of 25 | 0 of 25 |

Most of these references are background, such as circulation models,
biology and statistics. Of the 2,635 works cited by five or more eligible
papers, 427 are also found by the probed queries.

Sonnet subagents read reference titles from the GROBID TEIs of eligible
papers and judged them by title alone:

- about 450 in scope among the 1,500 most-cited missing titles;
- about 120 in scope in a random 1,500 from the long tail;
- about 350 in scope among the 2,185 works cited five or more times that no
  probed query finds.

## Candidate queries

Three rounds probed 104 candidate queries.

- **Round 1** (17 plain multi-word queries) and **round 2** (17 boolean
  queries) probed the top 400 hits of each query.
- **Round 3** (70 boolean queries from three Sonnet agents) also probed
  ranks 800–1,200 when a query had more than 1,200 hits. The agents took
  three angles: repairing weak and current queries, the residual citation
  gap, and themes not yet covered.

Across rounds 1 and 2 the new works barely overlap. Each round's queries
found about 4,000–5,000 new works among them (3,708 and 4,785), and the
union of both rounds is 7,573.

Selection was greedy. A query was added in order of its marginal gain, which
is the number of its top-400 new works not already covered by selected
queries, times its keep share. A query needed a keep share of at least 30 %
and a marginal gain of at least 40 works.

- **Selected:** 56 queries from rounds 2 and 3, which together cover 12,234
  new works in their top-400 heads, about 7,000 of them kept.
- **Depth:** a query gets a cap of 2,000 when ranks 800–1,200 still keep at
  least 30 %, and 400 otherwise. Queries with fewer than 1,200 hits are
  fetched whole.

The TSV lists all 56 together with the four current queries kept as they
are, split into two tiers:

- **Tier A:** keep share of at least 60 %, or marginal gain of at least 150,
  plus the base queries.
- **Tier B:** the rest.

| Tier | Queries | Sum of caps | Kept new works in top-400 heads |
|---|---|---|---|
| A | 30 | 42,500 | ~4,100 |
| B | 30 | 24,800 | ~2,900 |

Notable results:

- **"Lagrangian particle tracking"** keeps 16 of 16 sampled new works in
  its top 400 and 85 % at ranks 800–1,200 once restricted with
  `AND (ocean OR oceanic OR sea OR marine OR coastal OR estuary OR shelf)`.
  Unrestricted, 16–18 % were relevant.
- **"ocean particle tracking"** reaches 85 % when rewritten as
  `"particle tracking" AND (ocean OR …) AND (current OR currents OR circulation OR velocity)`.
- **Rewrites that did worse:** the boolean versions of "Lagrangian
  oceanography" (33 %) and "Lagrangian ocean modelling" (29 %) did worse
  than the originals.
- **Strongest additions:**
  - OpenDrift/MEDSLIK (100 %);
  - larval biophysical and per-taxon larval models (92–95 %);
  - drifter physics, i.e. drogue, windage and slip (83 %);
  - iceberg drift (82 %, top 400 only);
  - MPA and reef connectivity (81 %);
  - sensitivity of tracking to resolution (79 %);
  - water-mass pathways (79 %);
  - Argo and RAFOS float trajectories (72 %).
- **Under 22 % kept:**
  - paleo and foraminifera provenance (0 %);
  - generic advection numerics (6 %);
  - subsurface oil (8 %);
  - megafauna and turtles;
  - stochastic schemes;
  - mine tailings and munitions;
  - HF radar;
  - eDNA;
  - pathogens;
  - harmful algal blooms;
  - overflow water;
  - offline NEMO/HYCOM tracking;
  - Ariane.

## Rough scale

These figures are extrapolations, not measurements:

- Today the catalogue holds 2,883 works that luna scores at least 50.
- Of those, 69 % reached a full-text verdict and 86 % of those were
  eligible.
- Kept works in the top-400 heads of the selected queries add about 7,000.
- The 2,000-record caps and the citation band "cited by five or more" add
  more, by an amount not measured.
