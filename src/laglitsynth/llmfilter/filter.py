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

from laglitsynth.io import read_works_jsonl
from laglitsynth.llmfilter.models import FilterVerdict
from laglitsynth.openalex.models import Work

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a relevance classifier for academic paper abstracts.
The user will provide an abstract and a relevance criterion.
You must return a JSON object with exactly two fields:
- "relevance_score": an integer from 0 to 100 indicating how relevant the abstract is to the criterion (0 = not relevant at all, 100 = perfectly relevant)
- "reason": a short string (one sentence) explaining your score

Return ONLY the JSON object, nothing else."""


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
    parsed = json.loads(content)
    return FilterVerdict(
        work_id=work_id,
        relevance_score=int(parsed.get("relevance_score", 0)),
        accepted=False,  # caller sets this based on threshold
        reason=str(parsed.get("reason", "")),
    )


def filter_works(
    input_path: Path,
    prompt: str,
    *,
    model: str,
    base_url: str,
    threshold: int,
    limit: int | None,
) -> Iterator[tuple[Work, FilterVerdict]]:
    processed = 0
    for work in read_works_jsonl(input_path):
        if limit is not None and processed >= limit:
            return
        if work.abstract is None:
            logger.warning("Skipping work %s: no abstract", work.id)
            continue
        verdict = classify_abstract(
            work.id, work.abstract, prompt, model=model, base_url=base_url
        )
        verdict = verdict.model_copy(
            update={"accepted": verdict.relevance_score >= threshold}
        )
        yield work, verdict
        processed += 1


def _check_ollama(base_url: str, model: str) -> None:
    """Verify Ollama is reachable by making a lightweight request."""
    try:
        client = OpenAI(base_url=f"{base_url}/v1", api_key="ollama")
        client.models.retrieve(model)
    except Exception:
        print(
            f"Cannot reach Ollama at {base_url}. Is `ollama serve` running?",
            file=sys.stderr,
        )
        sys.exit(1)


def _default_output_path(input_path: Path) -> Path:
    ts = datetime.now(UTC).isoformat(timespec="microseconds").replace(":", "-")
    return Path("data/filtered") / f"{input_path.stem}_filtered_{ts}.jsonl"


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "filter-abstracts",
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
        "--limit",
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
    _check_ollama(args.base_url, args.model)

    output: Path = args.output or _default_output_path(args.input)
    verdicts_path = output.with_suffix(".verdicts.jsonl")
    meta_path = output.with_suffix(".meta.json")

    if not args.dry_run and output.exists():
        print(f"Error: output file already exists: {output}", file=sys.stderr)
        sys.exit(1)

    # Count total works for progress reporting
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
        for work in read_works_jsonl(args.input):
            if args.limit is not None and index >= args.limit:
                break

            if work.abstract is None:
                skipped_count += 1
                logger.warning("Skipping work %s: no abstract", work.id)
                continue

            index += 1
            verdict = classify_abstract(
                work.id,
                work.abstract,
                args.prompt,
                model=args.model,
                base_url=args.base_url,
            )
            verdict = verdict.model_copy(
                update={"accepted": verdict.relevance_score >= args.threshold}
            )

            status = "accepted" if verdict.accepted else "rejected"
            title_trunc = (work.title or "")[:40]
            work_id_trunc = work.id[-12:]
            print(
                f"  [{index}/{total}] {status} (score: {verdict.relevance_score})"
                f" — {work_id_trunc} \"{title_trunc}\"",
                file=sys.stderr,
            )

            if verdict.accepted:
                accepted_count += 1
                if output_file is not None:
                    output_file.write(work.model_dump_json())
                    output_file.write("\n")
            else:
                rejected_count += 1
                if reject_file is not None:
                    reject_file.write(work.model_dump_json())
                    reject_file.write("\n")

            if verdicts_file is not None:
                verdicts_file.write(verdict.model_dump_json())
                verdicts_file.write("\n")

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
        meta = {
            "tool": "laglitsynth.llmfilter.filter",
            "tool_version": "alpha",
            "prompt": args.prompt,
            "model": args.model,
            "threshold": args.threshold,
            "filtered_at": datetime.now(UTC).isoformat(timespec="microseconds"),
            "accepted_count": accepted_count,
            "rejected_count": rejected_count,
            "skipped_count": skipped_count,
        }
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)
            f.write("\n")
