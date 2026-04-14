# Documentation

`laglitsynth` is a pipeline for structured literature reviews. It fetches
publication metadata from OpenAlex, filters by relevance using a local LLM,
and produces structured outputs for downstream analysis.

## Pipeline components

1. **[Fetch publications](fetch-publications.md)** -- search OpenAlex by
   keyword and store results as JSONL.
2. **[Filter abstracts](filter-abstracts.md)** -- send abstracts to a local
   Ollama-hosted LLM and keep only the relevant ones.

## Reference

- [Data layout](data-layout.md) -- directory structure and file conventions.
- [Data model](data-model.md) -- Pydantic models shared across the pipeline.
