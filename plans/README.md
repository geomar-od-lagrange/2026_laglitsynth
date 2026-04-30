# Plan shape

Plans are prescriptive specs consumed once during implementation.
Voice and length rules live in [AGENTS.md](../AGENTS.md) under
"Plans are specs, not templates." This file lists the sections each
plan uses and what each one is for.

## Sections

In order. Sections 1, 3, 5, 8 are always present; 2, 4, 6, 7 are
dropped when empty.

1. **Goal.** One short paragraph: what the plan exists to produce
   and why now.
2. **Non-goals.** What is explicitly out of scope. Often a single
   prose paragraph; bullets only when items genuinely diverge.
3. **Target state.** The shape of the repo after the plan lands:
   new files and paths, data models, CLI, schemas. Most of the plan
   lives here. Decisions that are obvious in context (e.g. "reuse
   the existing `RunMeta`") don't need to be named.
4. **Design decisions.** Choices worth naming because a reviewer
   might challenge them. Fold into Target state when light — one
   paragraph is usually enough. No heading per decision.
5. **Implementation sequence.** Numbered commits, each with its
   tests named inline. Not nested sections; one short paragraph per
   commit. `pixi run typecheck` and `pixi run test` pass between
   each.
6. **Follow-ups.** Things deliberately deferred, with a one-line
   reason each.
7. **Risks.** Things that could go wrong and the chosen mitigation.
   Drop entries that only restate a non-goal or that amount to
   "nothing happens."
8. **Critical files.** Relative-markdown links to existing code and
   docs the plan touches. Helps a reviewer open the surrounding
   context quickly.

## Naming and lifecycle

- File name: short kebab-case describing the outcome
  (`screening-abstracts-csv-export.md`, not
  `implement-csv-export-stage.md`).
- Active plans live in [`plans/`](.). Completed plans move to
  [`plans/done/`](done/) when their implementation lands.
- The [roadmap](roadmap.md) indexes active and queued plans; update
  it when a plan is written, implemented, or archived.
