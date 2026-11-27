# First full run of the restructured pipeline

The run of 26 November 2026 on an Apple M3 Pro laptop (Docker Desktop at 12
CPUs and 16 GB), with `gpt-6-luna` through the GEOMAR LiteLLM proxy for all
LLM stages and `grobid/grobid:0.9.0-crf` (arm64, native) for stage 6. It
started from the deduplicated catalogue of October 2026 (9,149 works) and
the PDF store (5,936 works with a PDF). Cost and throughput estimates made
before the run are in [run-cost-and-throughput.md](run-cost-and-throughput.md).

## Stages

| Stage | Run | Input | Result | Wall time | Settings |
|---|---|---|---|---|---|
| 3 screening | `2026-11-26_luna` | 9,149 works | 7,669 scored, 1,480 without abstract (score `None`), 0 failures | 5.3 min | 8 in flight |
| 5 corpus-assemble | threshold 0 | 9,149 works | 9,149 in corpus, 5,936 with a PDF | | |
| 6 GROBID | | 5,936 PDFs | 5,911 extracted, 25 failed (HTTP 500) | about 30 min | 4, then 10 in flight |
| 7 eligibility | `2026-11-26T21-22-40_d7afa604d109` | 5,911 texts | 2,135 eligible, 3,751 excluded, 25 skipped (no TEI) | 7.2 min | 8 in flight |
| 8 codebook | same run-id | 2,135 eligible | 2,135 records, 0 failures | about 85 min | 8 in flight, `reasoning_effort=high` |

## Threshold choice

The corpus took every catalogue work (threshold 0), so full-text
eligibility judged every PDF. The abstract question asks whether the
abstract shows particle tracking; a paper whose abstract does not name its
method scores low although its full text may qualify. With eligibility
verdicts on every PDF, the share of full-text-eligible papers each abstract
threshold would have kept is:

| Abstract threshold | Eligible papers kept (of 2,135) |
|---|---|
| 1 | 2,119 (99.3%) |
| 5 | 2,094 (98.1%) |
| 10 | 2,024 (94.8%) |
| 20 | 1,930 (90.4%) |
| 50 | 1,852 (86.7%) |
| 80 | 1,784 (83.6%) |

131 of the eligible papers have no abstract in the catalogue. Scores are
`round(100 * probability)` from the decisions endpoint; their distribution
is bimodal (3,976 below 10, 2,440 at 90 or above). Treating full-text
eligibility as ground truth assumes stage 7 is right, which this run does
not check.

## GROBID throughput

At 4 requests in flight stage 6 extracted 114 PDFs per minute; at 10 (the
server-side `concurrency` limit in 0.9.0-crf's `grobid.yaml`) it extracted
216–225 per minute, with the container at about 660% CPU and 5.6 GB. No
HTTP 503 occurred at 10. The 25 failures are HTTP 500 on individual PDFs,
including `W2953172649`, which also failed in the benchmark.

## LLM cost

Spend was read from the proxy's `x-litellm-key-spend` response header,
which lags behind the calls by a minute or more.

| Stage | Papers | Cost | Per 1,000 papers | Estimate before the run |
|---|---|---|---|---|
| 3 screening | 7,669 abstracts | about $0.43 | $0.06 | $0.50–0.60 total |
| 7 eligibility | 5,911 texts | about $8.80 | about $1.49 | $0.70 |
| 8 codebook | 2,135 texts | about $5.85 | about $2.75 | $2.18 |

Eligibility cost twice the estimate; the estimate assumed the input sizes
of three codebook test papers and no output tokens. The whole run cost
about $15 of LLM calls.

The first key (budget $15) ran out 30 papers before the end of stage 8. The
proxy (LiteLLM 1.109.1) answered HTTP 422 `budget_exceeded`, which stage 8
records as an `llm-rejected` sentinel. `--skip-existing` skips every work
with a record, sentinels included, so the 12 budget sentinels were removed
from `records.jsonl` by hand before resuming with a second key.
[skip-existing-retry-sentinels.md](../../plans/done/skip-existing-retry-sentinels.md)
makes `--skip-existing` retry sentinel rows instead.

## Codebook sample

Of the first 20 records, 19 filled 6–15 fields; the notes separate output
intervals from integration time steps and observed from simulated
trajectories. One paper (`W2137378317`) filled one field because its TEI
held only the introduction.

## First look at the codebook

A throwaway script (`data/analysis-taste/taste.py`, local, unversioned)
groups the free-text fields of the 2,135 records by regex into families and
draws word clouds and rankings. The grouping is rough; the numbers below
show what a synthesis could look at, not results.

- Integration scheme, where named: fourth-order Runge–Kutta 526, Euler 105,
  second-order Runge–Kutta 72, analytical (TRACMASS/Ariane) 52, adaptive 23.
- Tracking software, where named: Parcels 218, Ariane 107, Ichthyop 102, CMS
  74, OpenDrift 64, TRACMASS 43, custom or in-house code 230, a long tail
  of 290. Parcels' share of papers grows from about 6% in 2015–2019 to about
  30% in 2023–2026.
- Velocity source families: NEMO-based products 377, ROMS/CROCO 298, HYCOM
  173, altimetry-derived currents 153.
- Reported particle time steps cluster at 1 hour and at 5–30 minutes.
- Share of papers that report an item, counting "not stated" answers as not
  reported: velocity source 78%, software 55%, time step 41%, integration
  scheme 39%, diffusion scheme 24%, spatial interpolation 21%, a link to
  the tracking code 14%, temporal interpolation 11%, experiment code 3%,
  analysis code 2%.

`config_available` and `rationale` read as reported in most papers because
the model describes whatever settings or reasons the text gives; the
codebook does not separate configuration published as files from settings
described in prose, nor a rationale for the numerical choices from other
reasons.
