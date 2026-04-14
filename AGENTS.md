# Literature Review Pipeline

Tools for conducting structured literature reviews — from keyword search to
citation analysis to structured synthesis.

## Goal

Build a pipeline that supports the full arc of a literature review: discover
publications, collect metadata, trace citation networks, filter and cluster by
topic and affiliation, export to standard formats, and eventually support
structured assessment and synthesis.

## Principles

- **Use what exists.** Don't reimplement what a mature library already does.
  Prefer established packages over hand-rolled alternatives.
- **Typed data, not dicts.** Pydantic models are the contract between
  components. Every record read from disk is validated. No raw dicts flowing
  through the pipeline.
- **Plain text over clever encodings.** If an upstream API returns data in an
  inconvenient format, convert it to something human-readable at ingest time.
  Downstream tools should never have to deal with API quirks.
- **Keep it practical.** This is a research tool, not a product. Robustness
  matters (retries, resumability for long fetches), over-engineering doesn't.
- **Separate concerns.** Each tool does one thing. They communicate through
  shared data formats and pydantic models.
- **Typed Python throughout.** All code uses type annotations. Mypy with
  strict mode is the bar — run `pixi run typecheck` before merging.
- **Green field, no backwards compatibility.** This pipeline has no external
  consumers. Every user is a developer on the project. We break internal APIs
  freely when it makes the code better — no deprecation cycles, no shims, no
  legacy support. If a model or interface needs to change, change it everywhere
  in one go.

## Environment

pixi for environment and package management. Project metadata and dependencies
in `pyproject.toml`.

## Project structure

- `plans/` — implementation plans (written before code)
- `docs/` — documentation of what's actually implemented
- Source code in `src/laglitsynth/` (subpackages per component, e.g.
  `src/laglitsynth/openalex/`)
