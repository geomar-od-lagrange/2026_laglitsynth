#!/usr/bin/env python3
"""Time N synthetic LLM requests against an Ollama server at a given
client-side thread count. Prints one whitespace-separated result line:

    <concurrency> <n_calls> <wall_s> <throughput_cps>

Intended to be driven by docs/explorations/bench-ollama/bench-ollama.sbatch across
the (OLLAMA_NUM_PARALLEL, threads) grid.
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from openai import OpenAI

SHORT_SYSTEM_PROMPT = (
    "You are a relevance classifier. Return JSON with exactly one field: "
    '"score": integer 0-100.'
)
SHORT_USER_TMPL = (
    "Criterion: oceanographic Lagrangian particle tracking. "
    "Abstract (synthetic call #{i}): Ocean currents in the North Atlantic "
    "were studied using drifters over 42 months. Passive particles were "
    "advected with a Runge-Kutta scheme."
)

# Roughly two printed pages of plausible methods-section text (~4.5k
# chars, ~1.3k tokens) plus a structured-extraction question. Models
# the stage-8 prompt shape: long input, short JSON output. Needs
# num_ctx >= ~2048; set OLLAMA_CONTEXT_LENGTH accordingly on the
# server side.
LONG_PASSAGE = """\
## 2. Materials and Methods

### 2.1 Ocean model and forcing

We use the Nucleus for European Modelling of the Ocean (NEMO) v4.2 on the
ORCA025 tripolar grid (1/4 degree horizontal resolution, 75 vertical levels
with partial steps at the bottom). The configuration was spun up for 30
years from a climatological rest state, forced with ERA5 reanalysis
(1980-2010 climatology), and then integrated with inter-annually varying
ERA5 forcing from 1993 to 2022. Daily-mean three-dimensional velocity and
temperature-salinity fields were written to disk for use in the offline
Lagrangian calculations. Bottom topography is from ETOPO1 and bathymetry
is partial-step. The sea-ice component is SI3 with elastic-viscous-plastic
rheology. Ocean-atmosphere heat and freshwater fluxes use the CORE-II bulk
formulae. River discharge is climatological from Dai & Trenberth (2002).

### 2.2 Particle tracking scheme

Virtual particles are advected using a fourth-order Runge-Kutta integration
scheme implemented in OceanParcels v2.4.2 (Delandmeter and van Sebille,
2019). Each trajectory is integrated with an adaptive sub-step of 5 minutes,
nested within the 1-day NEMO output cadence. Spatial interpolation between
grid cells uses a tri-linear scheme in the horizontal and nearest-neighbour
in the vertical to preserve water-mass properties. Velocity fields are
linearly interpolated in time between daily snapshots. Particle advection
accounts only for the resolved velocity field; no stochastic sub-grid
parameterisation of turbulent diffusion is applied in the baseline
experiment. Sensitivity runs with an added random-walk term (K_H = 100
m^2/s horizontal, K_V = 1e-5 m^2/s vertical) are reported in Section 3.4.

### 2.3 Release configuration

Particles are released seasonally, on 15 February, 15 May, 15 August and
15 November each year, at the surface between 45N-60N and 50W-10W on a
0.25 degree grid. Each release consists of 86,400 particles, yielding
345,600 particles per year and approximately 10.4 million particles across
the 30-year analysis period. Each particle is advected forward for five
years from its release date, producing a rolling 5-year trajectory record.
Trajectories are output every 5 days in double precision (longitude,
latitude, depth) together with ambient temperature and salinity sampled
from the ocean state at each output step.

### 2.4 Diagnostic framework

Downstream connectivity is assessed by binning end-points on a regular
1 x 1 degree mesh and constructing season-stratified transport matrices.
Dominant pathways are identified by eigenvector decomposition of the
transport matrix, retaining modes above the 95th percentile of singular
value mass. Mean residence times within each mesh cell are computed by
a Markov-chain analysis using the first-passage formulation so that
revisits do not inflate the estimate. Integrated upwelling along each
trajectory is estimated as the cumulative vertical velocity sampled at
particle depth.

### 2.5 Code and data availability

The OceanParcels configuration files, the NEMO ORCA025 velocity fields,
and the post-processing Jupyter notebooks are archived at Zenodo. Numerical
parameters (RK4 step, output cadence, forcing window) are listed in
Table 1; diagnostic scripts are released under an MIT licence in the
accompanying GitHub repository.
"""

LONG_SYSTEM_PROMPT = (
    "You extract structured metadata from oceanographic methods sections. "
    "Return JSON with exactly these fields: "
    '"integration_scheme" (string), "time_step_value" (string), '
    '"interpolation_spatial" (string), "ocean_model" (string), '
    '"code_available" (boolean).'
)
LONG_USER_TMPL = (
    "Paper excerpt (synthetic call #{i}):\n\n" + LONG_PASSAGE + "\n\n"
    "Extract the requested fields."
)


def _call(
    client: OpenAI,
    model: str,
    i: int,
    system_prompt: str,
    user_tmpl: str,
) -> None:
    client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_tmpl.format(i=i)},
        ],
        temperature=0.1,
    )


def _load_tei_prompt(tei_path: Path) -> tuple[str, str]:
    """Return (system_prompt, user_tmpl) from a real TEI file, shaped
    exactly as stage 8 (`extraction-codebook`) would send it to the LLM.

    Import locally so the short/long modes keep working without an
    editable-install of laglitsynth.
    """
    from laglitsynth.extraction_codebook.prompts import (
        CHAR_BUDGET,
        SYSTEM_PROMPT,
        build_user_message,
        render_fulltext,
    )
    from laglitsynth.fulltext_extraction.tei import TeiDocument

    tei = TeiDocument(tei_path)
    text, _truncated = render_fulltext(tei, char_budget=CHAR_BUDGET)
    user_msg = build_user_message("full_text", text)
    # {i} in the template is only used by synthetic modes; include a
    # harmless trailing marker so every warmup/timed call has a unique
    # suffix and Ollama doesn't cache a response across threads.
    user_tmpl = user_msg + "\n\n(bench call #{i})"
    return SYSTEM_PROMPT, user_tmpl


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--base-url",
        required=True,
        help=(
            "Ollama URL, or comma-separated list of URLs. With multiple, "
            "client threads round-robin across them (static assignment by "
            "task index)."
        ),
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--n-calls", type=int, default=30)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument(
        "--prompt-kind",
        choices=("short", "long", "tei"),
        default="short",
        help=(
            "short: ~300-token screening-style prompt (default). "
            "long: ~1300-token synthetic methods-section prompt. "
            "tei: render a real TEI via stage 8's prompt helpers (needs "
            "--tei-path; ~15k tokens, real-paper methods content). "
            "long/tei need OLLAMA_CONTEXT_LENGTH set on the server."
        ),
    )
    parser.add_argument(
        "--tei-path",
        type=Path,
        default=None,
        help="Path to a TEI XML file (required for --prompt-kind tei).",
    )
    args = parser.parse_args()

    if args.prompt_kind == "tei":
        if args.tei_path is None:
            parser.error("--prompt-kind tei requires --tei-path")
        system_prompt, user_tmpl = _load_tei_prompt(args.tei_path)
    elif args.prompt_kind == "long":
        system_prompt, user_tmpl = LONG_SYSTEM_PROMPT, LONG_USER_TMPL
    else:
        system_prompt, user_tmpl = SHORT_SYSTEM_PROMPT, SHORT_USER_TMPL

    urls = [u.strip() for u in args.base_url.split(",") if u.strip()]
    clients = [OpenAI(base_url=f"{u}/v1", api_key="ollama") for u in urls]

    # One warmup call per backend so model-load isn't in the timed window.
    for idx, c in enumerate(clients):
        _call(c, args.model, -idx - 1, system_prompt, user_tmpl)

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                _call,
                clients[i % len(clients)],
                args.model,
                i,
                system_prompt,
                user_tmpl,
            )
            for i in range(args.n_calls)
        ]
        for fut in as_completed(futures):
            fut.result()
    elapsed = time.monotonic() - t0

    throughput = args.n_calls / elapsed
    print(f"{args.concurrency} {args.n_calls} {elapsed:.2f} {throughput:.2f}")


if __name__ == "__main__":
    main()
