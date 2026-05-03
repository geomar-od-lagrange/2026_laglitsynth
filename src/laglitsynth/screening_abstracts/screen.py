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
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from openai import APIConnectionError, APITimeoutError, OpenAI

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.config import register_config_arg, save_resolved_config
from laglitsynth.ids import generate_run_id
from laglitsynth.io import (
    JsonlReadStats,
    append_jsonl,
    read_jsonl,
    write_meta,
)
from laglitsynth.models import LlmMeta, RunMeta
from laglitsynth.screening_abstracts.models import TOOL_NAME, ScreeningMeta, ScreeningVerdict

STAGE_SUBDIR = "screening-abstracts"

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a relevance classifier for academic paper abstracts.
The user will provide an abstract and a relevance criterion.
You must return a JSON object with exactly two fields:
- "relevance_score": an integer from 0 to 100 indicating how relevant the abstract is to the criterion (0 = not relevant at all, 100 = perfectly relevant)
- "reason": a short string (one sentence) explaining your score

Return ONLY the JSON object, nothing else."""

_TEMPERATURE = 0.8
_LLM_TIMEOUT_SECONDS = 60
_LLM_MAX_RETRIES = 3


def format_screening_input(work: Work) -> str:
    """Render the title/authors/year/abstract block fed to the LLM.

    Title and publication_year render as ``<unknown>`` when None;
    an empty author list renders as ``<unknown>``. The abstract is
    expected to be present — caller skips works with no abstract.
    """
    title = work.title if work.title is not None else "<unknown>"
    year = (
        str(work.publication_year)
        if work.publication_year is not None
        else "<unknown>"
    )
    authors_list = [a.author.display_name for a in work.authorships]
    authors = ", ".join(authors_list) if authors_list else "<unknown>"
    abstract = work.abstract if work.abstract is not None else ""
    return (
        f"Title: {title}\n"
        f"Authors: {authors}\n"
        f"Year: {year}\n"
        f"Abstract: {abstract}"
    )


def classify_abstract(
    work_id: str,
    formatted_input: str,
    prompt: str,
    *,
    model: str,
    base_url: str,
    client: OpenAI,
) -> ScreeningVerdict:
    """Call the LLM, validate the payload, compose the verdict.

    On JSON parse error returns a ``reason="llm-parse-failure"``
    sentinel with ``seed=None`` and the raw response attached so an
    operator can see what the LLM actually said. On
    ``APITimeoutError`` / ``APIConnectionError`` after all retries
    are exhausted returns a ``reason="llm-timeout"`` sentinel.
    """
    seed = random.randint(0, 2**31 - 1)
    try:
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Criterion: {prompt}\n\n{formatted_input}",
                },
            ],
            temperature=_TEMPERATURE,
            seed=seed,
        )
    except (APITimeoutError, APIConnectionError) as exc:
        logger.warning("LLM timeout for %s: %s", work_id, exc)
        return ScreeningVerdict(
            work_id=work_id,
            relevance_score=None,
            reason="llm-timeout",
            seed=None,
            raw_response=None,
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
            raw_response=content,
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        logger.warning("LLM parse failure for %s: %s", work_id, exc)
        return ScreeningVerdict(
            work_id=work_id,
            relevance_score=None,
            reason="llm-parse-failure",
            seed=None,
            raw_response=content,
        )


def _no_abstract_verdict(work_id: str) -> ScreeningVerdict:
    return ScreeningVerdict(
        work_id=work_id,
        relevance_score=None,
        reason="no-abstract",
        seed=None,
    )


def screen_works(
    input_path: Path,
    prompt: str,
    *,
    model: str,
    base_url: str,
    max_records: int | None,
    concurrency: int = 1,
) -> Iterator[ScreeningVerdict]:
    """Yield a verdict per input work.

    LLM calls are dispatched through a ``ThreadPoolExecutor`` of
    ``concurrency`` workers (default 1, giving ordered sequential dispatch).
    Works without an abstract yield a ``no-abstract`` sentinel immediately;
    abstract-backed verdicts are yielded in completion order.
    The server's ``OLLAMA_NUM_PARALLEL`` must be at least ``concurrency``
    for actual parallelism.
    """
    client = OpenAI(
        base_url=f"{base_url}/v1",
        api_key="ollama",
        timeout=_LLM_TIMEOUT_SECONDS,
        max_retries=_LLM_MAX_RETRIES,
    )

    works: list[Work] = []
    for idx, work in enumerate(read_jsonl(input_path, Work)):
        if max_records is not None and idx >= max_records:
            break
        works.append(work)

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures: dict[Future[ScreeningVerdict], str] = {}
        for work in works:
            if work.abstract is None:
                logger.warning("Skipping work %s: no abstract", work.id)
                yield _no_abstract_verdict(work.id)
                continue
            fut = pool.submit(
                classify_abstract,
                work.id,
                format_screening_input(work),
                prompt,
                model=model,
                base_url=base_url,
                client=client,
            )
            futures[fut] = work.id

        for fut in as_completed(futures):
            yield fut.result()


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
        "--data-dir",
        type=Path,
        default=Path("data"),
        help="Bucket root for stage outputs (default: data/)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Run identifier; default = generate_run_id() (<iso>_<12hex>).",
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
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help=(
            "In-flight LLM requests (default: 1). Must not exceed the "
            "Ollama server's OLLAMA_NUM_PARALLEL for actual parallelism. "
            "See docs/llm-concurrency.md."
        ),
    )
    register_config_arg(parser)
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    _preflight(args)

    if args.run_id is None:
        args.run_id = generate_run_id()
    output_dir: Path = Path(args.data_dir) / STAGE_SUBDIR / args.run_id
    verdicts_path = output_dir / "verdicts.jsonl"
    meta_path = output_dir / "screening-meta.json"
    threshold: int = args.screening_threshold

    # Compute prompt digest once for the meta record.
    user_prompt: str = args.prompt
    prompt_sha256 = hashlib.sha256(
        (SYSTEM_PROMPT + "\n" + user_prompt).encode("utf-8")
    ).hexdigest()

    stats = JsonlReadStats()
    total = sum(1 for _ in read_jsonl(args.input, Work, stats))

    print(f"Screening {total} works with model {args.model}", file=sys.stderr)
    print(f"Threshold: {threshold}, Prompt: {args.prompt!r}", file=sys.stderr)
    if not args.dry_run:
        print(f"Output dir: {output_dir}", file=sys.stderr)

    # Clean-rerun: truncate any prior verdicts file before streaming
    # append. Resume is explicitly not supported — see
    # docs/llm-concurrency.md.
    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        save_resolved_config(args, output_dir)
        verdicts_path.unlink(missing_ok=True)

    def _build_meta(
        *,
        above: int,
        below: int,
        skipped: int,
        parse_failures: int,
        timeouts: int,
    ) -> ScreeningMeta:
        return ScreeningMeta(
            run=RunMeta(
                tool=TOOL_NAME,
                run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
                validation_skipped=stats.skipped,
            ),
            llm=LlmMeta(
                model=args.model,
                temperature=_TEMPERATURE,
                prompt_sha256=prompt_sha256,
            ),
            threshold=threshold,
            input_path=str(args.input),
            input_count=total,
            above_threshold_count=above,
            below_threshold_count=below,
            skipped_count=skipped,
            llm_parse_failure_count=parse_failures,
            llm_timeout_count=timeouts,
            prompt=user_prompt,
        )

    # Write meta upfront so a mid-run reviewer-export sees prompt + LLM
    # fingerprint. Counts start at zero and get rewritten at the end.
    if not args.dry_run:
        write_meta(
            meta_path,
            _build_meta(above=0, below=0, skipped=0, parse_failures=0, timeouts=0),
        )

    t0 = time.monotonic()
    above_threshold_count = 0
    below_threshold_count = 0
    skipped_count = 0
    llm_parse_failure_count = 0
    llm_timeout_count = 0
    index = 0

    for verdict in screen_works(
        args.input,
        args.prompt,
        model=args.model,
        base_url=args.base_url,
        max_records=args.max_records,
        concurrency=args.concurrency,
    ):
        index += 1

        if verdict.reason in ("no-abstract", "llm-parse-failure", "llm-timeout"):
            skipped_count += 1
            if verdict.reason == "llm-parse-failure":
                llm_parse_failure_count += 1
            elif verdict.reason == "llm-timeout":
                llm_timeout_count += 1
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

        if not args.dry_run:
            append_jsonl(verdict, verdicts_path)

    elapsed = time.monotonic() - t0

    print(
        f"\nDone in {elapsed:.1f}s: {above_threshold_count} above threshold, "
        f"{below_threshold_count} below threshold, {skipped_count} skipped.",
        file=sys.stderr,
    )

    if not args.dry_run:
        write_meta(
            meta_path,
            _build_meta(
                above=above_threshold_count,
                below=below_threshold_count,
                skipped=skipped_count,
                parse_failures=llm_parse_failure_count,
                timeouts=llm_timeout_count,
            ),
        )
