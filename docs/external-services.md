# External services setup

Runbook for the external services the pipeline depends on: an
**OpenAI-compatible LLM endpoint** (stages 3, 7, 8) and **GROBID** (PDF
extraction for stage 6). Read this before running any LLM or full-text
stage. The [NESH HPC cluster](#nesh-hpc-cluster) section at the bottom
covers batch operation on Kiel's cluster.

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

GROBID runs as a Docker container. The image is amd64-only; on Apple
Silicon it runs under Rosetta emulation (functional but slower):

```bash
# Apple Silicon: install Rosetta first if prompted
softwareupdate --install-rosetta

docker run --rm -p 8070:8070 lfoppiano/grobid:0.8.0
```

Startup takes ~30–60 seconds while the JVM and models load (up to ~90s
on a cold NESH node — see the NESH section below). Poll the health
endpoint before running stage 6:

```bash
curl http://localhost:8070/api/isalive
```

### Memory tuning

Docker Desktop's default VM allocation is 2 GB. GROBID's JVM heap alone
needs roughly 2 GB under normal load; spikes during batch processing can
exceed that. Set Docker Desktop's memory limit to at least **6–8 GB**
(Preferences → Resources → Memory) before running stage 6 on a real
corpus. Under-allocation causes silent JVM crashes mid-corpus without
a helpful error from the container.

See [explorations/nesh-grobid.md](explorations/nesh-grobid.md) for
memory observations from the NESH setup (32 GB Slurm allocation; GROBID
ran without issue).

### macOS `localhost:8070` workaround

On some macOS Docker Desktop versions, host calls to `localhost:8070`
are silently blocked by network sandboxing even when the container is
running and healthy. The workaround is to issue the GROBID request from
a helper container joined to GROBID's network namespace:

```bash
docker run --rm --network container:<grobid-container-name> \
  -v "$PWD/pdfs":/pdfs curlimages/curl \
  -s -S -f -o - \
  -F input=@"/pdfs/sample.pdf" \
  http://localhost:8070/api/processFulltextDocument
```

The `fulltext-extraction` stage uses the Python GROBID client, which
makes the same HTTP call. If stage 6 fails with connection errors on
macOS despite the container being alive, try this network-namespace
approach. Full context in [grobid-lessons.md](grobid-lessons.md).

### `consolidateCitations`

Stage 6 sends `consolidateCitations=0` by default, exposed via the
`--consolidate-citations {0,1}` flag. Setting it to `1` instructs GROBID
to call Crossref and Semantic Scholar to enrich extracted references with
DOIs. That lookup requires internet access during processing and noticeably
slows extraction. The pipeline keeps OpenAlex as the authoritative metadata
source, so citation enrichment adds no value at the default. See
[grobid-lessons.md](grobid-lessons.md) for the original rationale.

### Mid-corpus crash recovery

If GROBID or the Docker container crashes during a long stage-6 run,
restart the container and re-run with `--skip-existing`:

```bash
docker run --rm -p 8070:8070 lfoppiano/grobid:0.8.0 &
# wait for /api/isalive ...
laglitsynth fulltext-extraction --skip-existing \
    --corpus-dir data/corpus/ --output-dir data/fulltext-extraction/ ...
```

Stage 6 takes `--corpus-dir`, `--output-dir`, `--grobid-url`, `--timeout`,
and `--skip-existing`; it has no `--run-id` flag. `--skip-existing`
loads any prior `extraction.jsonl` from the output directory and skips
works whose latest record was made from the current corpus PDF (same
`pdf_sha256`). Only new, changed or previously failed PDFs are submitted
to GROBID.

## NESH HPC cluster

NESH (Kiel University HPC) uses Singularity rather than Docker and
requires a proxy for internet access from compute nodes. The production
batch wrapper is `scripts/nesh-pipeline.sbatch`: it runs on the `base`
partition, starts GROBID, and reaches the LLM endpoint through the proxy;
it runs no LLM server. This section documents the key invocations for users
writing their own sbatch scripts.

### GROBID on NESH (Singularity)

Pull the SIF once and cache it on `$WORK`:

```bash
module load gcc12-env/12.3.0
module load singularity

GROBID_SIF="data/nesh/grobid-0.8.0.sif"
[[ -f "$GROBID_SIF" ]] || \
    singularity pull "$GROBID_SIF" docker://lfoppiano/grobid:0.8.0
```

Singularity ignores the Dockerfile `WORKDIR`, so `--pwd /opt/grobid` is
mandatory. GROBID writes to two paths inside the read-only image layer
(`grobid-home/tmp` and `logs`); replace `--writable-tmpfs` with explicit
bind mounts:

```bash
GROBID_WORK="data/nesh/grobid-work"
mkdir -p "$GROBID_WORK/tmp" "$GROBID_WORK/logs"

singularity run --pwd /opt/grobid \
    --bind "$PWD/$GROBID_WORK/tmp:/opt/grobid/grobid-home/tmp" \
    --bind "$PWD/$GROBID_WORK/logs:/opt/grobid/logs" \
    "$GROBID_SIF" >"logs/grobid-${SLURM_JOB_ID}.log" 2>&1 &
```

The bind mounts survive the job; `grobid-work/logs/grobid-service.log`
is available for post-mortem inspection.

GROBID needs 30–90 seconds to start on a cold node. Poll
`http://localhost:8070/api/isalive` before submitting PDFs.

See [explorations/nesh-grobid.md](explorations/nesh-grobid.md) for the
full debugging log and the explanation of each flag.
