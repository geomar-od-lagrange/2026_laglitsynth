# bake-model

`laglitsynth bake-model` creates a new Ollama model tag by writing a
two-line Modelfile (`FROM <base>` + `PARAMETER num_ctx <N>`) to a
temporary file and then running `ollama create <tag> -f <Modelfile>`.
The resulting tag has `num_ctx` hard-coded at the OS level, which is
more reliable than passing `num_ctx` via `extra_body` at request time:
the OpenAI-compatible endpoint does not guarantee that `extra_body`
options are forwarded to the underlying model, whereas a baked
Modelfile always takes effect. This is the same mechanism the NESH
batch wrapper uses for the `laglit-screen`, `laglit-eligibility`, and
`laglit-extract` tags; `bake-model` gives local users the same
capability without requiring shell scripting.

## CLI usage

```bash
laglitsynth bake-model \
    --tag laglit-eligibility \
    --base llama3.1:8b \
    --num-ctx 32768
```

The subcommand prints the new tag to stdout on success, so it can be
captured in a script:

```bash
TAG=$(laglitsynth bake-model --tag laglit-eligibility --base llama3.1:8b --num-ctx 32768)
laglitsynth fulltext-eligibility --model "$TAG" ...
```

## When to use it

Use `bake-model` whenever you want a guaranteed context window for
stages 7 ([fulltext-eligibility](eligibility.md)) or 8
([extraction-codebook](extraction-codebook.md)). The `--num-ctx` flag
on those stages sets `num_ctx` via `extra_body`, but Ollama does not
always honour that path. Baking the Modelfile beforehand removes the
ambiguity: pass the baked tag as `--model` and `--num-ctx` will be
ignored (or can be omitted entirely once the context is already fixed
in the tag).

The default `--num-ctx` value on stages 7 and 8 is `32768`. Override
it only if your base model supports a larger window and your hardware
can accommodate it.

## See also

- [LLM-stage concurrency](llm-concurrency.md) — parallelism across the
  LLM stages.
- [NESH Ollama exploration](explorations/nesh-ollama.md) — background on
  why baked Modelfiles are more reliable than `extra_body` on the NESH
  cluster setup.
