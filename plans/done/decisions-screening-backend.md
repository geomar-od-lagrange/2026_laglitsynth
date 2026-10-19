# Decisions backend for abstract screening

## Goal

Let stage 3 score abstracts with a hosted decisions model (TypeSafe Jev
via OpenRouter's decisions endpoint) instead of a local chat model. A probe
on 12 abstracts showed Jev separating oceanographic from atmospheric,
engineering and laboratory particle tracking where gemma3:4b scored all of
them 90 to 95, at about 0.3 s and 0.00003 USD per abstract. The decision is
a calibrated probability, so there is no JSON to parse and no score the
model invents.

## Non-goals

The criteria wording stays as it is; the scope question (lakes, labs,
rivers) is issue #26 and is settled separately so that reruns compare like
with like. Stages 7 and 8 keep the chat backend: their outputs are free
text and structured records, which a decisions model does not produce.

## Target state

`screening-abstracts --backend {ollama,decisions}` (default `ollama`).
`--model` names the model for either backend. The decisions backend reads
`--api-key` (falling back to `OPENROUTER_API_KEY` in `.env`) and
`--decisions-url` (default `https://openrouter.ai/api/alpha/decisions`),
skips the Ollama preflight and ignores `--base-url`.

The criteria YAML gains an optional `decision` block beside
`system_prompt`:

```yaml
decision:
  instructions: Is this work relevant to Lagrangian particle tracking in oceanography?
  criteria:
    "true": The work tracks particles, tracers or objects in an ocean flow field, or develops methods for doing so.
    "false": The work is about something else, or applies Lagrangian tracking outside the ocean.
```

[prompts.py](../../src/laglitsynth/prompts.py) gets `load_decision_question`
returning a typed `DecisionQuestion`; the decisions backend exits naming the
criteria file when the block is missing. The example criteria file gains
the block with the present scope.

New module
[screening_abstracts/decisions.py](../../src/laglitsynth/screening_abstracts/decisions.py):
one `noul` question per work, `state` is the same title/authors/year/abstract
block the chat backend sends. The verdict stores
`relevance_score = round(100 * p)`, `reason = None` (the model gives no
justification), `seed = None`, and the raw answers JSON in `raw_response`.
An HTTP error or timeout after retries yields the `llm-timeout` sentinel; a
response without the expected answer yields `llm-parse-failure`. Works
without an abstract get `no-abstract` as before. Calls go through
`map_concurrent` like the chat backend.

`LlmMeta.temperature` becomes `float | None` (None for decisions);
`prompt_sha256` hashes the question's canonical JSON plus the user-render
marker, and `criterion` holds the rendered question so the reviewer export
shows what was asked. The run's `config.yaml` records `backend`.

The review config gains `backends: {screening: ollama|decisions}`, emitted
as `CFG_SCREENING_BACKEND`; the runner resolves `SCREENING_BACKEND` with
the usual env > config > default precedence, passes `--backend`, and when it
is `decisions` requires `OPENROUTER_API_KEY` and passes it as `--api-key`.

## Implementation sequence

1. `DecisionQuestion` + `load_decision_question`; `LlmMeta.temperature`
   optional; `decisions.py` with the client and `classify_decision`. Tests
   (httpx `MockTransport`): probability maps to the score, missing answer
   gives `llm-parse-failure`, HTTP 500 and a timeout give `llm-timeout`,
   missing `decision` block names the file.
2. `--backend`, `--api-key`, `--decisions-url` on `screening-abstracts`;
   meta and config recording. Tests: decisions run writes verdicts with
   `reason None` and the probability in `raw_response`, meta has
   `temperature null` and the question in `criterion`, no Ollama preflight,
   no key exits naming `OPENROUTER_API_KEY`.
3. Review config `backends`, runner wiring, example criteria `decision`
   block, docs ([screening-abstracts.md](../../docs/screening-abstracts.md),
   [configs.md](../../docs/configs.md), [interfaces.md](../../docs/interfaces.md),
   [external-services.md](../../docs/external-services.md)), roadmap.

## Critical files

- [src/laglitsynth/screening_abstracts/screen.py](../../src/laglitsynth/screening_abstracts/screen.py)
- [src/laglitsynth/prompts.py](../../src/laglitsynth/prompts.py)
- [src/laglitsynth/models.py](../../src/laglitsynth/models.py)
- [src/laglitsynth/review.py](../../src/laglitsynth/review.py)
- [scripts/run-pipeline.sh](../../scripts/run-pipeline.sh)
- [examples/screening-criteria/lagrangian-oceanography.yaml](../../examples/screening-criteria/lagrangian-oceanography.yaml)
