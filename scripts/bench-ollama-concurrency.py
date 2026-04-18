#!/usr/bin/env python3
"""Time N synthetic LLM requests against an Ollama server at a given
client-side thread count. Prints one whitespace-separated result line:

    <concurrency> <n_calls> <wall_s> <throughput_cps>

Intended to be driven by scripts/bench-ollama-concurrency.sbatch across
the (OLLAMA_NUM_PARALLEL, threads) grid.
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI

SYSTEM_PROMPT = (
    "You are a relevance classifier. Return JSON with exactly one field: "
    '"score": integer 0-100.'
)
USER_PROMPT_TMPL = (
    "Criterion: oceanographic Lagrangian particle tracking. "
    "Abstract (synthetic call #{i}): Ocean currents in the North Atlantic "
    "were studied using drifters over 42 months. Passive particles were "
    "advected with a Runge-Kutta scheme."
)


def _call(client: OpenAI, model: str, i: int) -> None:
    client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TMPL.format(i=i)},
        ],
        temperature=0.1,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--n-calls", type=int, default=30)
    parser.add_argument("--concurrency", type=int, required=True)
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Warmup calls before timing (default 1).",
    )
    args = parser.parse_args()

    client = OpenAI(base_url=f"{args.base_url}/v1", api_key="ollama")

    for i in range(args.warmup):
        _call(client, args.model, i - args.warmup)

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(_call, client, args.model, i) for i in range(args.n_calls)
        ]
        for fut in as_completed(futures):
            fut.result()
    elapsed = time.monotonic() - t0

    throughput = args.n_calls / elapsed
    print(f"{args.concurrency} {args.n_calls} {elapsed:.2f} {throughput:.2f}")


if __name__ == "__main__":
    main()
