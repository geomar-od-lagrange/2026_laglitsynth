# Running GROBID under Singularity on NESH

Compact record of the three gotchas that turned a 1-day "start GROBID
in the sbatch" task into a multi-iteration debugging chain. The final
working invocation lives in [scripts/nesh-test-run.sbatch](../../scripts/nesh-test-run.sbatch);
this doc explains *why* each flag is needed. Pairs with
[docs/explorations/nesh-ollama.md](nesh-ollama.md), which covers the
Ollama half of the same sbatch.

## Why Singularity (not Docker)

NESH exposes a `singularity` module (unpinned) plus a `gcc12-env/12.3.0`
prereq. Docker is not available on compute nodes — typical for HPC —
and `singularity build` is not permitted either. What works:

- `singularity pull <repo-local.sif> docker://<image>` — converts a
  Docker Hub image into a SIF on disk. Works through the documented
  NESH proxy (`http_proxy=https_proxy=http://10.0.7.235:3128`).
- `singularity run <sif>` — executes the image's ENTRYPOINT.

So the deployment shape is: pull SIF once (cached at a workdir-local
path on `$WORK`), run that SIF in the background of the job, shell
out to the pipeline against `localhost:8070`.

## Gotcha 1: host cwd ≠ image `WORKDIR`

The `lfoppiano/grobid:0.8.0` image declares `WORKDIR /opt/grobid` and
uses a relative entrypoint path (`./grobid-service/bin/grobid-service`).
Singularity **ignores the Dockerfile `WORKDIR`** and keeps the host's
cwd, so the relative exec fails immediately:

```
[FATAL tini (3894789)] exec ./grobid-service/bin/grobid-service failed: No such file or directory
```

Fix:

```bash
singularity run --pwd /opt/grobid "$GROBID_SIF"
```

`--pwd` is Singularity's equivalent of Docker's `WORKDIR`. Must be
specified per run.

## Gotcha 2: `--writable-tmpfs` is not enough

After `--pwd` got the service alive (the `/api/isalive` endpoint
responded), every real `/api/processFulltextDocument` request returned
HTTP 500:

```
ERROR org.grobid.service.process.GrobidRestProcessFiles: An unexpected exception occurs.
! java.io.IOException: No such file or directory
! Causing: org.grobid.core.exceptions.GrobidResourceException:
  [GENERAL] Could not create temporary file, 'origin..pdf'
  under path '/opt/grobid/grobid-home/tmp'.
```

Plus a logback failure at startup:

```
ERROR ... Failed to create parent directories for [/opt/grobid/logs/grobid-service.log]
```

Both paths are **inside the read-only Singularity image layer**.
`--writable-tmpfs` provides a tmpfs overlay of the whole root but —
at least on the NESH Singularity — it did not materialize the
pre-existing `/opt/grobid/grobid-home/tmp` or `/opt/grobid/logs`
directory structure, so the `.createTempFile` calls failed.

Fix: replace `--writable-tmpfs` with explicit host-backed bind
mounts for the two paths GROBID actually writes to:

```bash
GROBID_WORK="data/nesh/grobid-work"
mkdir -p "$GROBID_WORK/tmp" "$GROBID_WORK/logs"
singularity run --pwd /opt/grobid \
    --bind "$PWD/$GROBID_WORK/tmp:/opt/grobid/grobid-home/tmp" \
    --bind "$PWD/$GROBID_WORK/logs:/opt/grobid/logs" \
    "$GROBID_SIF"
```

Side benefit: the host dirs persist after the job, so `grobid-work/logs/grobid-service.log`
is inspectable for diagnostics.

## Gotcha 3: sbatch `--output` parent dir must exist

sbatch's `--output=logs/...` fails silently if `logs/` doesn't exist
in the submission cwd — the job never starts, no error anywhere
except "job completed immediately". We moved sbatch's own
stdout/stderr to the submission cwd (`--output=nesh-test-run-%j.out`,
no subdir) to sidestep this, and `mkdir -p logs` inside the script
for the Ollama / GROBID per-run logs.

Not GROBID-specific but bit us in the same debugging chain.

## Final working sbatch block

Reference shape from [scripts/nesh-test-run.sbatch](../../scripts/nesh-test-run.sbatch):

```bash
module load gcc12-env/12.3.0
module load singularity

GROBID_SIF="data/nesh/grobid-0.8.0.sif"
GROBID_IMAGE="docker://lfoppiano/grobid:0.8.0"
GROBID_WORK="data/nesh/grobid-work"

mkdir -p "$(dirname "$GROBID_SIF")"
[[ -f "$GROBID_SIF" ]] || singularity pull "$GROBID_SIF" "$GROBID_IMAGE"

mkdir -p "$GROBID_WORK/tmp" "$GROBID_WORK/logs"
singularity run --pwd /opt/grobid \
    --bind "$PWD/$GROBID_WORK/tmp:/opt/grobid/grobid-home/tmp" \
    --bind "$PWD/$GROBID_WORK/logs:/opt/grobid/logs" \
    "$GROBID_SIF" >"logs/grobid-${SLURM_JOB_ID}.log" 2>&1 &
GROBID_PID=$!

# wait on http://localhost:8070/api/isalive (JVM + model load needs
# ~30-90s on a cold node) before pointing stage 6 at it.
```

## Caveats

- **Memory**: GROBID wants ~4–8 GB JVM heap. Our `--mem=32G` Slurm
  allocation is plenty.
- **CPU**: GROBID is CPU-bound. The current shape co-locates it on
  a GPU node with Ollama to simplify the job graph; at larger scale,
  a Slurm array of CPU-only GROBID nodes would be more efficient
  (50 instances × ~0.1 s/page scales to 100 k PDFs in hours).
- **Singularity pull through proxy**: works. Our `http_proxy /
  https_proxy` env vars propagate to the underlying `curl`-like
  fetcher. Image is several GB; cache the SIF at a known path on
  `$WORK` between jobs.
- **Persistent overlays**: `singularity overlay create` requires
  privileges we don't have. Bind mounts are the workable alternative
  for anything that needs to write inside the image.
