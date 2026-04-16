"""LLM-based abstract filtering via Ollama."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from openai import OpenAI

from laglitsynth.io import read_works_jsonl, write_meta
from laglitsynth.screening_abstracts.models import FilterMeta, FilterVerdict
from laglitsynth.catalogue_fetch.models import Work

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a relevance classifier for academic paper abstracts.
The user will provide an abstract and a relevance criterion.
You must return a JSON object with exactly two fields:
- "relevance_score": an integer from 0 to 100 indicating how relevant the abstract is to the criterion (0 = not relevant at all, 100 = perfectly relevant)
- "reason": a short string (one sentence) explaining your score

Return ONLY the JSON object, nothing else."""


class ClassifyError(Exception):
    """Raised when the LLM response cannot be parsed into a FilterVerdict."""


def classify_abstract(
    work_id: str,
    abstract: str,
    prompt: str,
    *,
    model: str,
    base_url: str,
) -> FilterVerdict:
    client = OpenAI(base_url=f"{base_url}/v1", api_key="ollama")
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
    )
    content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
        score = parsed["relevance_score"]
        reason = parsed["reason"]
        return FilterVerdict(
            work_id=work_id,
            relevance_score=int(score),
            accepted=False,  # caller sets this based on threshold
            reason=str(reason),
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
        raise ClassifyError(
            f"Failed to parse LLM response for {work_id}: {exc}"
        ) from exc


def filter_works(
    input_path: Path,
    prompt: str,
    *,
    model: str,
    base_url: str,
    threshold: int,
    max_records: int | None,
) -> Iterator[tuple[Work, FilterVerdict]]:
    processed = 0
    for work in read_works_jsonl(input_path):
        if max_records is not None and processed >= max_records:
            return
        processed += 1
        if work.abstract is None:
            logger.warning("Skipping work %s: no abstract", work.id)
            yield work, FilterVerdict(work_id=work.id)
            continue
        try:
            verdict = classify_abstract(
                work.id, work.abstract, prompt, model=model, base_url=base_url
            )
            verdict = verdict.model_copy(
                update={
                    "accepted": verdict.relevance_score is not None
                    and verdict.relevance_score >= threshold
                }
            )
        except ClassifyError:
            logger.warning("LLM parse failure for %s, recording as invalid", work.id)
            verdict = FilterVerdict(work_id=work.id)
        yield work, verdict


def _preflight(args: argparse.Namespace) -> None:
    try:
        client = OpenAI(base_url=f"{args.base_url}/v1", api_key="ollama")
        client.models.retrieve(args.model)
    except Exception:
        raise SystemExit(
            f"Cannot reach Ollama at {args.base_url}. Is `ollama serve` running?"
        )


def _default_output_path(input_path: Path) -> Path:
    ts = datetime.now(UTC).isoformat(timespec="microseconds").replace(":", "-")
    return Path("data/screening-abstracts") / f"{input_path.stem}_screened_{ts}.jsonl"


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "screening-abstracts",
        help="Filter JSONL works by abstract relevance using a local LLM.",
    )
    parser.add_argument("input", type=Path, help="Input JSONL file path")
    parser.add_argument("prompt", help="Relevance filter prompt string")
    parser.add_argument("-o", "--output", type=Path, help="Output JSONL path")
    parser.add_argument(
        "--model", default="gemma3:4b", help="Ollama model name (default: gemma3:4b)"
    )
    parser.add_argument(
        "--threshold",
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
        "--reject-file",
        type=Path,
        help="Optional path to write rejected works",
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

    output: Path = args.output or _default_output_path(args.input)
    verdicts_path = output.with_suffix(".verdicts.jsonl")
    meta_path = output.with_suffix(".meta.json")

    total = sum(1 for _ in read_works_jsonl(args.input))

    print(f"Filtering {total} works with model {args.model}", file=sys.stderr)
    print(f"Threshold: {args.threshold}, Prompt: {args.prompt!r}", file=sys.stderr)
    if not args.dry_run:
        print(f"Output: {output}", file=sys.stderr)

    t0 = time.monotonic()
    accepted_count = 0
    rejected_count = 0
    skipped_count = 0
    index = 0

    output_file = None
    verdicts_file = None
    reject_file = None

    if not args.dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        output_file = open(output, "x")
        verdicts_file = open(verdicts_path, "x")
        if args.reject_file:
            args.reject_file.parent.mkdir(parents=True, exist_ok=True)
            reject_file = open(args.reject_file, "x")

    try:
        for work, verdict in filter_works(
            args.input,
            args.prompt,
            model=args.model,
            base_url=args.base_url,
            threshold=args.threshold,
            max_records=args.max_records,
        ):
            index += 1
            title_trunc = (work.title or "")[:40]
            work_id_trunc = work.id[-12:]

            if verdict.accepted is None:
                # No abstract or LLM parse failure
                skipped_count += 1
                print(
                    f"  [{index}/{total}] skipped"
                    f" — {work_id_trunc} \"{title_trunc}\"",
                    file=sys.stderr,
                )
            elif verdict.accepted:
                accepted_count += 1
                print(
                    f"  [{index}/{total}] accepted"
                    f" (score: {verdict.relevance_score})"
                    f" — {work_id_trunc} \"{title_trunc}\"",
                    file=sys.stderr,
                )
                if output_file is not None:
                    output_file.write(work.model_dump_json() + "\n")
            else:
                rejected_count += 1
                print(
                    f"  [{index}/{total}] rejected"
                    f" (score: {verdict.relevance_score})"
                    f" — {work_id_trunc} \"{title_trunc}\"",
                    file=sys.stderr,
                )
                if reject_file is not None:
                    reject_file.write(work.model_dump_json() + "\n")

            if verdicts_file is not None:
                verdicts_file.write(verdict.model_dump_json() + "\n")

    finally:
        if output_file is not None:
            output_file.close()
        if verdicts_file is not None:
            verdicts_file.close()
        if reject_file is not None:
            reject_file.close()

    elapsed = time.monotonic() - t0

    print(
        f"\nDone in {elapsed:.1f}s: {accepted_count} accepted, "
        f"{rejected_count} rejected, {skipped_count} skipped.",
        file=sys.stderr,
    )

    if not args.dry_run:
        write_meta(
            meta_path,
            FilterMeta(
                prompt=args.prompt,
                model=args.model,
                threshold=args.threshold,
                filtered_at=datetime.now(UTC).isoformat(timespec="microseconds"),
                accepted_count=accepted_count,
                rejected_count=rejected_count,
                skipped_count=skipped_count,
            ),
        )
