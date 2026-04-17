"""LLM-based abstract screening via Ollama."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from openai import OpenAI

from laglitsynth.io import JsonlReadStats, read_works_jsonl, write_jsonl, write_meta
from laglitsynth.models import _LlmMeta, _RunMeta
from laglitsynth.screening_abstracts.models import TOOL_NAME, ScreeningMeta, ScreeningVerdict

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a relevance classifier for academic paper abstracts.
The user will provide an abstract and a relevance criterion.
You must return a JSON object with exactly two fields:
- "relevance_score": an integer from 0 to 100 indicating how relevant the abstract is to the criterion (0 = not relevant at all, 100 = perfectly relevant)
- "reason": a short string (one sentence) explaining your score

Return ONLY the JSON object, nothing else."""

_TEMPERATURE = 0.8


class ClassifyError(Exception):
    """Raised when the LLM response cannot be parsed into a ScreeningVerdict."""


def classify_abstract(
    work_id: str,
    abstract: str,
    prompt: str,
    *,
    model: str,
    base_url: str,
    client: OpenAI,
) -> ScreeningVerdict:
    seed = random.randint(0, 2**31 - 1)
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Criterion: {prompt}\n\nAbstract: {abstract}",
            },
        ],
        temperature=_TEMPERATURE,
        seed=seed,
    )
    content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
        score = parsed["relevance_score"]
        reason = parsed["reason"]
        return ScreeningVerdict(
            work_id=work_id,
            relevance_score=int(score),
            reason=str(reason),
            seed=seed,
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        raise ClassifyError(
            f"Failed to parse LLM response for {work_id}: {exc}"
        ) from exc


def screen_works(
    input_path: Path,
    prompt: str,
    *,
    model: str,
    base_url: str,
    max_records: int | None,
) -> Iterator[ScreeningVerdict]:
    client = OpenAI(base_url=f"{base_url}/v1", api_key="ollama")
    processed = 0
    for work in read_works_jsonl(input_path):
        if max_records is not None and processed >= max_records:
            return
        processed += 1
        if work.abstract is None:
            logger.warning("Skipping work %s: no abstract", work.id)
            yield ScreeningVerdict(work_id=work.id, relevance_score=None, reason="no-abstract", seed=None)
            continue
        try:
            verdict = classify_abstract(
                work.id, work.abstract, prompt, model=model, base_url=base_url, client=client
            )
        except ClassifyError:
            logger.warning("LLM parse failure for %s, recording as invalid", work.id)
            yield ScreeningVerdict(work_id=work.id, relevance_score=None, reason="llm-parse-failure", seed=None)
            continue
        yield verdict


def _preflight(args: argparse.Namespace) -> None:
    try:
        client = OpenAI(base_url=f"{args.base_url}/v1", api_key="ollama")
        client.models.retrieve(args.model)
    except Exception:
        raise SystemExit(
            f"Cannot reach Ollama at {args.base_url}. Is `ollama serve` running?"
        )


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "screening-abstracts",
        help="Screen JSONL works by abstract relevance using a local LLM.",
    )
    parser.add_argument("input", type=Path, help="Input JSONL file path")
    parser.add_argument("prompt", help="Relevance screening prompt string")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/screening-abstracts"),
        help="Output directory (default: data/screening-abstracts/)",
    )
    parser.add_argument(
        "--model", default="gemma3:4b", help="Ollama model name (default: gemma3:4b)"
    )
    parser.add_argument(
        "--screening-threshold",
        type=int,
        default=50,
        help="Relevance score cutoff, 0-100 (default: 50)",
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:11434",
        help="Ollama API base URL (default: http://localhost:11434)",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Process only the first N works",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print verdicts to stderr without writing output",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    _preflight(args)

    output_dir: Path = args.output_dir
    verdicts_path = output_dir / "verdicts.jsonl"
    meta_path = output_dir / "screening-meta.json"
    threshold: int = args.screening_threshold

    # Compute prompt digest once for the meta record.
    user_prompt: str = args.prompt
    prompt_sha256 = hashlib.sha256(
        (SYSTEM_PROMPT + "\n" + user_prompt).encode("utf-8")
    ).hexdigest()

    stats = JsonlReadStats()
    total = sum(1 for _ in read_works_jsonl(args.input, stats))

    print(f"Screening {total} works with model {args.model}", file=sys.stderr)
    print(f"Threshold: {threshold}, Prompt: {args.prompt!r}", file=sys.stderr)
    if not args.dry_run:
        print(f"Output dir: {output_dir}", file=sys.stderr)

    t0 = time.monotonic()
    above_threshold_count = 0
    below_threshold_count = 0
    skipped_count = 0
    index = 0

    verdicts: list[ScreeningVerdict] = []

    for verdict in screen_works(
        args.input,
        args.prompt,
        model=args.model,
        base_url=args.base_url,
        max_records=args.max_records,
    ):
        index += 1
        verdicts.append(verdict)

        if verdict.reason in ("no-abstract", "llm-parse-failure"):
            skipped_count += 1
            print(
                f"  [{index}/{total}] skipped ({verdict.reason})"
                f" — {verdict.work_id[-12:]}",
                file=sys.stderr,
            )
        elif verdict.relevance_score is not None and verdict.relevance_score >= threshold:
            above_threshold_count += 1
            print(
                f"  [{index}/{total}] above threshold"
                f" (score: {verdict.relevance_score})"
                f" — {verdict.work_id[-12:]}",
                file=sys.stderr,
            )
        else:
            below_threshold_count += 1
            print(
                f"  [{index}/{total}] below threshold"
                f" (score: {verdict.relevance_score})"
                f" — {verdict.work_id[-12:]}",
                file=sys.stderr,
            )

    elapsed = time.monotonic() - t0

    print(
        f"\nDone in {elapsed:.1f}s: {above_threshold_count} above threshold, "
        f"{below_threshold_count} below threshold, {skipped_count} skipped.",
        file=sys.stderr,
    )

    if not args.dry_run:
        write_jsonl(verdicts, verdicts_path)
        run_meta = _RunMeta(
            tool=TOOL_NAME,
            run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
            validation_skipped=stats.skipped,
        )
        llm_meta = _LlmMeta(
            model=args.model,
            temperature=_TEMPERATURE,
            prompt_sha256=prompt_sha256,
        )
        write_meta(
            meta_path,
            ScreeningMeta(
                run=run_meta,
                llm=llm_meta,
                threshold=threshold,
                input_path=str(args.input),
                input_count=total,
                above_threshold_count=above_threshold_count,
                below_threshold_count=below_threshold_count,
                skipped_count=skipped_count,
            ),
        )
