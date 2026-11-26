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
Switching to 0.9.0 needed a check that the TEI parser handles its output;
see the next section.

The benchmark script is `data/scratch-retrieval/grobid-bench/bench.py`
(local, unversioned).

## GROBID 0.9.0 TEI check

All 39 papers of an earlier 0.8.0 run (`data/run/fulltext-extraction/tei/`)
were sent from the PDF store to `grobid/grobid:0.9.0-crf` (arm64, native)
with stage 6's request, one at a time. All 39 succeeded; median 1.5 s per
request, 74 s in total including a 9 s first request. Both TEI sets were
parsed with `TeiDocument` and rendered with the stage 7 and stage 8
`render_fulltext`.

| Measure (sum over 39 papers) | 0.8.0 | 0.9.0 |
|---|---|---|
| Parse failures | 0 | 0 |
| Rendered body text (chars) | 1,151,038 | 1,210,644 |
| Body sections (all top level, none nested) | 426 | 430 |
| Papers with an `<abstract>` | 33 | 35 |
| Figures and tables (`<figure>` in body) | 388 | 373 |
| Text in `<figDesc>` (chars) | 116,592 | 103,953 |
| In-text citations (`ref[@type='bibr']`) | 1,892 | 1,915 |
| Bibliography entries / with DOI | 1,331 / 511 | 1,342 / 500 |

The TEI namespace and the set of element names were identical; no element
appeared in only one version. Titles in `titleStmt` were identical for all
39 papers. Section heads were identical for 12 papers; the rest differ by
split or merged sections and head text. Rendered body text changed by less
than 10% for 30 papers, grew by more than 10% for 7 and shrank for 2.

The gains are text that 0.8.0 misplaced: W1439505426 went from 885 to
11,663 chars of body, W4390284427 (Russian) from 6,555 to 15,613 because
0.8.0 had put most of it into `<figDesc>`. The main loss is W2476309498
(a conference paper, 20,307 to 13,663 chars): 0.9.0 put the sections
"Synthetic pressure field" and "Experimental pressure field" into
`<back><div type="annex">`, which `sections()` does not read, and a
repeated page header into figure labels. W3204761479 lost about 680 chars
of body to a `<figDesc>`.

0.9.0 splits more back matter out of `annex` into new `<div>` types
`conflict` and `contribution`. The parser reads no back matter other than
`<listBibl>`, so this changes nothing downstream. 0.9.0 typed fewer
`<figure>` elements as `type="table"`; figures and tables never reach the
rendered text. Both versions leave `<formula>` and `<note>` elements
directly under a section `<div>` out of the rendered text (about 18,000
and 17,000 chars over the 39 papers).

The one incompatibility was `/api/version`: 0.9.0 answers with JSON
(`{"version":"0.9.0","revision":"0.9.0"}`) where 0.8.0 sent plain text,
so stage 6 recorded the JSON string as `grobid_version` until
`_grobid_version` learned to read the `version` field.

The 0.9.0 container used about 3.1 GB right after startup and 4.2 GB
after the 39 PDFs, on Docker Desktop 4.99.0 with 16 GB. A restart of the
container answered `/api/isalive` after 10 s.

The scripts are in the session scratch area (unversioned).

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
