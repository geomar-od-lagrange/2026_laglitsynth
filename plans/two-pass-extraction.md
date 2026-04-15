# Two-pass LLM extraction

A general pattern for extracting structured data from long documents with
LLMs. Validated in a prior prototype
([litrag](https://github.com/willirath/litrag)) and applicable to several
pipeline stages (eligibility assessment, data extraction, thematic
synthesis).

## The problem

Full scientific papers are 5k-15k words. Asking an LLM to read an entire
paper and fill a structured schema in one shot has two failure modes:

- **Context limits.** Smaller local models (e.g. llama3.2:3b with 4k context)
  simply cannot fit a full paper plus prompt plus output. litrag found that
  only ~3 chunks (~2700 tokens of paper text) fit alongside the prompt.
- **Attention dilution.** Even models with sufficient context windows perform
  worse on structured extraction from long inputs. The signal (e.g. "we used
  4th-order Runge-Kutta with a 6-hour time step") is a few sentences buried
  in thousands of words. One-shot extraction misses information, hallucinates
  values, or conflates fields.

## The pattern

Split extraction into two passes:

### Pass 1 — Locate relevant passages

Feed the document (or its chunks) to the LLM with a narrow instruction:
"Find the passages that describe [topic X]." The output is a small set of
relevant text spans, each tagged with its source location (section title,
chunk ID, or page number).

This pass is a retrieval task. It can also be done without an LLM — litrag
used hybrid BM25 + vector search with cross-encoder reranking to find the
top chunks for a query. Either approach works; the key property is that pass
1 reduces the input for pass 2 from thousands of words to hundreds.

### Pass 2 — Extract structured fields

Feed only the retrieved passages (plus the extraction schema) to the LLM.
The context is now small enough for reliable structured output. Validate the
output through a Pydantic model; on schema failure, retry once with the
validation error appended to the prompt.

## Why it works

- Pass 1 is a simpler task (relevance judgment, not structured extraction)
  and tolerates lower-quality models or non-LLM retrieval.
- Pass 2 operates on focused context, which improves both recall (less to
  miss) and precision (less to hallucinate from).
- The intermediate passages are inspectable — a human can verify what the
  LLM saw before trusting the extraction.

## Applicability in this pipeline

### Stage 7 (eligibility assessment)

Pass 1: locate the methods/approach section. Pass 2: determine whether the
paper describes computational Lagrangian methods (yes/no with evidence
quote).

### Stage 8 (data extraction)

Pass 1: locate passages describing numerical methods, time-stepping,
interpolation, software tools. Pass 2: extract codebook fields from those
passages.

### Stage 11 (thematic synthesis)

Pass 1: retrieve rationale passages across the corpus for a given numerical
choice. Pass 2: cluster or summarize the rationales.

## Validate-and-retry loop

The laglitsynth filter (stage 3) already uses JSON-mode structured output
with graceful handling of parse failures. The same pattern extends to
extraction:

1. Define extraction schema as a Pydantic model.
2. Request JSON-mode output from the LLM.
3. Validate through `Model.model_validate_json(response)`.
4. On `ValidationError`, retry once with the error message in the prompt.
5. On second failure, log and mark the record as `extraction_failed`.

litrag's gist generator used a fixed 4-section template
(Contributions / Methods / Results / Limitations) and this worked reliably.
More complex schemas (many optional fields, nested structures) will need
the retry mechanism.

## Context budget

litrag's experience with a 3b model (4k context):

- ~900 tokens per chunk, ~150 token overlap between chunks.
- Prompt + system message consumed ~500 tokens.
- Usable context for paper text: ~2700 tokens (~3 chunks).

Larger models relax this, but the two-pass pattern remains valuable even
with 128k context windows — it improves extraction quality, not just
feasibility.
