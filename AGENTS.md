# Literature Review Pipeline

Tools for conducting structured literature reviews — from keyword search to
citation analysis to structured synthesis.

## Goal

Build a pipeline that supports the full arc of a literature review: discover
publications, collect metadata, trace citation networks, filter and cluster by
topic and affiliation, export to standard formats, and eventually support
structured assessment and synthesis.

## Principles

- **Plan before code.** Every new component starts with a plan in
  [`plans/`](plans/). Write the plan, get it reviewed, then implement.
  The plan is the spec — it records the design decisions and scope so
  they're visible in the repo, not buried in chat history.
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
- **Link, don't just name.** When markdown references a file path, make it a
  relative markdown link, not a bare backtick path.
- **Plain markdown headings over bold-text formatting.** Use the heading
  hierarchy (`##`, `###`, `####`, `#####`) for document structure. Never
  simulate headings with `**Bold:**` paragraphs or `- **Bold.** text` list
  items when a heading at the appropriate level would do. Bold-text headings
  break TOC generation, linking, and navigation. If you need a sub-section
  under an H3, use H4; if you need named items under that, use H5.
- **None means None.** If upstream data is missing or null, store `None` —
  don't invent defaults. Science data is messy; downstream consumers must
  handle incomplete information explicitly.
- **AI-tool agnostic.** Project configuration, principles, and documentation
  live in the repo (AGENTS.md, docs/, plans/), not in tool-specific memory
  or config. Any AI coding assistant should be able to pick up the project
  from repo contents alone.
- **Green field, no backwards compatibility.** This pipeline has no external
  consumers. Every user is a developer on the project. We break internal APIs
  freely when it makes the code better — no deprecation cycles, no shims, no
  legacy support. If a model or interface needs to change, change it everywhere
  in one go.

## Environment

pixi for environment and package management. Project metadata and dependencies
in [`pyproject.toml`](pyproject.toml).

## Project structure

- [`plans/`](plans/) — implementation plans (written before code);
  completed plans move to [`plans/done/`](plans/done/)
- [`docs/`](docs/) — documentation of what's actually implemented
- Source code in [`src/laglitsynth/`](src/laglitsynth/) (subpackages per
  component, e.g. [`src/laglitsynth/openalex/`](src/laglitsynth/openalex/))
