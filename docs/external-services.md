# External services setup

Runbook for the external services the pipeline depends on: the
OpenAlex and Unpaywall APIs (stages 1 and 5), an OpenAI-compatible LLM
endpoint (stages 3, 7 and 8) and GROBID (stage 6). Each stage doc opens
with a Prerequisites block naming what that stage needs.

## OpenAlex and Unpaywall

`catalogue-fetch` needs a free OpenAlex API key, registered at
<https://openalex.org/settings/api>; `fulltext-retrieval` needs a contact
email for the Unpaywall API. Each takes its value as a flag (`--api-key`,
`--email`) or, when the flag is absent, reads `OPENALEX_API_KEY` or
`UNPAYWALL_EMAIL` from `.env` in the working directory (see
[.env.example](../.env.example)) and says so on stderr. An exported
variable of that name is not read by the stage itself; the runner exports
`.env` and passes both as flags.

## LLM endpoint

Every LLM stage talks to one OpenAI-compatible endpoint, set per review.
The stages take the same flags:

- `--base-url` — the OpenAI-compatible base URL, including `/v1`. Required.
- `--model` — the model name. Required.
- `--api-key` — the key. Without it a stage reads `LLM_API_KEY` from the
  environment, then from `.env` (see [.env.example](../.env.example)), and
  exits naming `LLM_API_KEY` when none is set. The key is never written to
  a run's `config.yaml`, and the runner never passes it as a flag.
- `--decisions-url` (stages 3 and 7) — the decisions endpoint, default
  `<base-url>/decisions`, for a provider that serves it elsewhere.

Stages 3 and 7 send OpenAI's Decisions request shape to the decisions
URL; stage 8 sends JSON-mode chat completions to the base URL, with no
temperature or seed. A wrong URL or key shows up on the first request: HTTP
401, 402, 403 and 404 make the stage exit naming the URL. See
[screening-abstracts.md](screening-abstracts.md#errors-and-retries) for the
sentinel each other error gives.

## GROBID

### Install

GROBID runs as a Docker container. The pipeline uses the CRF-only image
`grobid/grobid:0.9.0-crf`, which is multi-arch (amd64 and arm64) and so
runs natively on Apple Silicon without Rosetta:

```bash
docker run --rm -p 8070:8070 grobid/grobid:0.9.0-crf
```

The JVM and models load at startup. Poll the health endpoint before
running stage 6:

```bash
curl http://localhost:8070/api/isalive
```

### Memory tuning

Docker Desktop's default VM allocation is 2 GB. GROBID's JVM needs more
than that once it processes PDFs (observed with `lfoppiano/grobid:0.8.0`
and `grobid/grobid:0.9.0-crf`; see
[explorations/run-cost-and-throughput.md](explorations/run-cost-and-throughput.md)).
Set Docker Desktop's memory limit to at least **6–8 GB** (Preferences →
Resources → Memory) before running stage 6 on a real corpus.
Under-allocation causes silent JVM crashes mid-corpus without a helpful
error from the container.

### macOS `localhost:8070` workaround

On some macOS Docker Desktop versions, host calls to `localhost:8070`
are silently blocked by network sandboxing even when the container is
running and healthy (seen in the litrag prototype with 0.8.0; host calls
to `grobid/grobid:0.9.0-crf` worked directly on Docker Desktop 4.99.0
for macOS). The workaround is to issue the GROBID request from
a helper container joined to GROBID's network namespace:

```bash
docker run --rm --network container:<grobid-container-name> \
  -v "$PWD/pdfs":/pdfs curlimages/curl \
  -s -S -f -o - \
  -F input=@"/pdfs/sample.pdf" \
  http://localhost:8070/api/processFulltextDocument
```

The `fulltext-extraction` stage makes the same HTTP call, posting the PDF
to `/api/processFulltextDocument` with `httpx`. If stage 6 fails with connection errors on
macOS despite the container being alive, try this network-namespace
approach. Full context in [grobid-lessons.md](grobid-lessons.md).

### Citation consolidation and crash recovery

Stage 6 leaves GROBID's `consolidateCitations` off by default, so GROBID
needs no internet access while it processes PDFs; the
`--consolidate-citations` flag and the reasoning are in
[fulltext-extraction.md](fulltext-extraction.md#operational-details). If
GROBID or its container crashes mid-corpus, restart it and re-run stage 6
with `--skip-existing`, which sends only the PDFs without a current
extraction (see
[fulltext-extraction.md](fulltext-extraction.md#recovery-after-a-grobid-crash)).
