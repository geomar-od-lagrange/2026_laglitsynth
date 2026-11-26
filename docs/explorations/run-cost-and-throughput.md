# Run cost and throughput before the first restructured run

Measurements taken on 26 November 2026, before the first full run of the
restructured pipeline. The machine was an Apple M3 Pro laptop (12 cores,
36 GB) with Docker Desktop at 12 CPUs and 16 GB. The LLM endpoint was the
GEOMAR LiteLLM proxy serving `gpt-6-luna`.

## GROBID (stage 6)

Each image processed the same 48 random store PDFs (seed 0) with 3 requests
in flight, sending stage 6's request (`processFulltextDocument`,
`consolidateCitations=0`).

| Image | Architecture | Wall time | Effective | Median per request |
|---|---|---|---|---|
| `lfoppiano/grobid:0.8.0` (the pipeline's) | amd64, emulated | 97 s | 2.0 s/PDF | 3.5 s |
| `grobid/grobid:0.9.0-crf` | arm64, native | 48 s | 1.0 s/PDF | 1.9 s |

Both failed on the same PDF (`W2953172649`, HTTP 500). The 0.8.0 container
used about 5 GB of memory. At these rates the whole store (about 5,900
PDFs) takes about 3.3 h emulated or 1.6 h native. Stage 6 sent one request
at a time when this was measured, so these rates need concurrency in stage 6.
Switching to 0.9.0 needs a check that the TEI parser handles its output.

The benchmark script is `data/scratch-retrieval/grobid-bench/bench.py`
(local, unversioned).

## LLM pricing

`gpt-6-luna` standard rates as listed in November 2026: $0.10 per million
input tokens and $0.50 per million output tokens, with reasoning tokens
billed as output. OpenRouter's model page also showed $0.05/$0.25, which
looks like a batch tier.
Sources: [OpenRouter](https://openrouter.ai/openai/gpt-6-luna),
[pricepertoken.com](https://pricepertoken.com/pricing-page/model/openai-gpt-6-luna).

## Screening (stage 3)

Not run; estimated from the catalogue and from test calls. Probes of the
decisions endpoint billed about 175–190 input tokens for a one-line
question and 0 output tokens. The deduplicated catalogue has 9,149 works,
about 7,500 of them with an abstract, and about 12.5 million characters of
title and abstract. With the screening criteria (about 670 characters) per
call, that comes to about 5–6 million input tokens, or about $0.50–0.60 for
the whole catalogue.

## Codebook extraction (stage 8)

Three papers from earlier extraction runs, picked at the quartiles of body
text length, were sent through stage 8's request: the same system prompt and
user message, `response_format` JSON, `reasoning_effort=high`.

| Work | Text | Input tokens | Output tokens (reasoning) | Fields filled | Cost |
|---|---|---|---|---|---|
| W2011744542 | 14k chars | 3,827 | 3,226 (2,337) | 10 | $0.0020 |
| W3202318950 | 28k chars | 6,753 | 2,641 (2,048) | 6 | $0.0020 |
| W3203533909 | 43k chars | 10,346 | 3,007 (2,323) | 9 | $0.0025 |

The mean was about 7,000 input and 3,000 output tokens per paper, or $2.18
per 1,000 papers. Output, mostly reasoning, was about two thirds of the cost
and did not grow with paper length. Input came to about 4.2 characters per
token. Three papers is a small sample. The script is
`data/scratch-retrieval/codebook-cost/estimate.py` (local, unversioned).

## Eligibility (stage 7)

Not measured. It sends the same rendered full text to the decisions
endpoint. Using the input sizes above and no output tokens, it should cost
about $0.70 per 1,000 papers.
