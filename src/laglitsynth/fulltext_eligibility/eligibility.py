"""LLM-based full-text eligibility assessment via Ollama."""

from __future__ import annotations

import argparse
import hashlib
import logging
import random
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from lxml import etree
from openai import OpenAI
from pydantic import ValidationError

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.fulltext_eligibility.models import (
    TOOL_NAME,
    EligibilityMeta,
    EligibilityVerdict,
    SourceBasis,
    _EligibilityPayload,
)
from laglitsynth.fulltext_eligibility.prompts import (
    SYSTEM_PROMPT,
    USER_TEMPLATE,
    build_user_message,
    render_abstract,
    render_fulltext,
)
from laglitsynth.fulltext_extraction.models import ExtractedDocument
from laglitsynth.io import (
    JsonlReadStats,
    append_jsonl,
    read_jsonl,
    read_works_jsonl,
    write_jsonl,
    write_meta,
)
from laglitsynth.models import _LlmMeta, _RunMeta

logger = logging.getLogger(__name__)

_TEMPERATURE = 0.8
_NUM_CTX = 32768


def classify_eligibility(
    work_id: str,
    prompt: str,
    source_basis: SourceBasis,
    *,
    model: str,
    client: OpenAI,
) -> EligibilityVerdict:
    """Call the LLM, validate the payload, compose the verdict.

    On JSON parse error or ``ValidationError`` returns a
    ``reason="llm-parse-failure"`` sentinel with ``eligible=None``,
    ``seed=None``, and the raw response attached for audit. Mirrors
    stage 8's ``extract_codebook`` shape and error handling.
    """
    seed = random.randint(0, 2**31 - 1)
    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        temperature=_TEMPERATURE,
        seed=seed,
        extra_body={"options": {"num_ctx": _NUM_CTX}},
    )
    content = response.choices[0].message.content or "{}"
    try:
        payload = _EligibilityPayload.model_validate_json(content)
    except (ValidationError, ValueError) as exc:
        logger.warning("LLM parse failure for %s: %s", work_id, exc)
        return EligibilityVerdict(
            work_id=work_id,
            eligible=None,
            source_basis=source_basis,
            reason="llm-parse-failure",
            seed=None,
            raw_response=content,
        )
    return EligibilityVerdict(
        work_id=work_id,
        eligible=payload.eligible,
        source_basis=source_basis,
        reason=payload.reason,
        seed=seed,
        raw_response=content,
    )


def _load_extractions(
    path: Path, stats: JsonlReadStats
) -> dict[str, ExtractedDocument]:
    return {rec.work_id: rec for rec in read_jsonl(path, ExtractedDocument, stats)}


def assess_works(
    catalogue_path: Path,
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    *,
    client: OpenAI,
    model: str,
    max_records: int | None,
    skip_ids: set[str] | None = None,
    stats: JsonlReadStats | None = None,
) -> Iterator[EligibilityVerdict]:
    skip_ids = skip_ids or set()
    processed = 0
    for work in read_works_jsonl(catalogue_path, stats):
        if work.id in skip_ids:
            continue
        if max_records is not None and processed >= max_records:
            return
        processed += 1
        yield _assess_one(work, extractions, extraction_output_dir, client, model)


def _assess_one(
    work: Work,
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    client: OpenAI,
    model: str,
) -> EligibilityVerdict:
    # Step 1: prefer full text when an extraction exists.
    extracted = extractions.get(work.id)
    if extracted is not None:
        try:
            tei = extracted.open_tei(extraction_output_dir)
            rendered = render_fulltext(tei)
        except etree.XMLSyntaxError:
            logger.warning(
                "Malformed TEI for %s; recording tei-parse-failure", work.id
            )
            return EligibilityVerdict(
                work_id=work.id,
                eligible=None,
                source_basis="full_text",
                reason="tei-parse-failure",
                seed=None,
            )
        if rendered:
            prompt = build_user_message("full_text", rendered)
            return classify_eligibility(
                work.id, prompt, "full_text", client=client, model=model
            )
        # Empty body (valid XML, no content): fall through to abstract.

    # Step 2: fall back to abstract when available.
    if work.abstract:
        rendered = render_abstract(work.abstract)
        prompt = build_user_message("abstract_only", rendered)
        return classify_eligibility(
            work.id, prompt, "abstract_only", client=client, model=model
        )

    # Step 3: no source at all.
    logger.warning("No source for %s; recording no-source", work.id)
    return EligibilityVerdict(
        work_id=work.id,
        eligible=None,
        source_basis="none",
        reason="no-source",
        seed=None,
    )


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
        "fulltext-eligibility",
        help="Assess full-text eligibility of included works using a local LLM.",
    )
    parser.add_argument(
        "--catalogue",
        type=Path,
        required=True,
        help="Included catalogue JSONL (data/screening-adjudication/included.jsonl)",
    )
    parser.add_argument(
        "--extractions",
        type=Path,
        required=True,
        help="Extraction JSONL (data/fulltext-extraction/extraction.jsonl)",
    )
    parser.add_argument(
        "--extraction-output-dir",
        type=Path,
        default=None,
        help=(
            "Directory that ExtractedDocument.tei_path is relative to. "
            "Defaults to the parent of --extractions."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/fulltext-eligibility"),
        help="Output directory (default: data/fulltext-eligibility/)",
    )
    parser.add_argument(
        "--model",
        default="gemma3:4b",
        help="Ollama model name (default: gemma3:4b)",
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
        help="Process only the first N catalogue works",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip works that already have a verdict in verdicts.jsonl",
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
    eligible_path = output_dir / "eligible.jsonl"
    meta_path = output_dir / "eligibility-meta.json"

    extraction_output_dir: Path = (
        args.extraction_output_dir
        if args.extraction_output_dir is not None
        else args.extractions.parent
    )

    prompt_sha256 = hashlib.sha256(
        (SYSTEM_PROMPT + "\n" + USER_TEMPLATE + "\n" + str(_NUM_CTX)).encode("utf-8")
    ).hexdigest()

    stats = JsonlReadStats()
    extractions = _load_extractions(args.extractions, stats)

    total = sum(1 for _ in read_works_jsonl(args.catalogue, stats))

    skip_ids: set[str] = set()
    prior_verdicts: list[EligibilityVerdict] = []
    if args.skip_existing and verdicts_path.exists():
        prior_verdicts = list(
            read_jsonl(verdicts_path, EligibilityVerdict, stats)
        )
        skip_ids = {v.work_id for v in prior_verdicts}
        print(
            f"Skipping {len(skip_ids)} already-assessed works.",
            file=sys.stderr,
        )

    print(f"Assessing {total} works with model {args.model}", file=sys.stderr)
    if not args.dry_run:
        print(f"Output dir: {output_dir}", file=sys.stderr)

    # Start-of-run truncation: non-resuming runs rewrite verdicts.jsonl.
    if not args.dry_run and not args.skip_existing and verdicts_path.exists():
        verdicts_path.unlink()

    client = OpenAI(base_url=f"{args.base_url}/v1", api_key="ollama")

    t0 = time.monotonic()
    index = 0
    new_verdicts: list[EligibilityVerdict] = []

    for verdict in assess_works(
        args.catalogue,
        extractions,
        extraction_output_dir,
        client=client,
        model=args.model,
        max_records=args.max_records,
        skip_ids=skip_ids,
        stats=stats,
    ):
        index += 1
        new_verdicts.append(verdict)
        if not args.dry_run:
            append_jsonl(verdict, verdicts_path)

        if verdict.eligible is True:
            print(
                f"  [{index}/{total}] eligible ({verdict.source_basis})"
                f" — {verdict.work_id[-12:]}",
                file=sys.stderr,
            )
        elif verdict.eligible is False:
            print(
                f"  [{index}/{total}] excluded ({verdict.source_basis})"
                f" — {verdict.work_id[-12:]}",
                file=sys.stderr,
            )
        else:
            print(
                f"  [{index}/{total}] skipped ({verdict.reason})"
                f" — {verdict.work_id[-12:]}",
                file=sys.stderr,
            )

    elapsed = time.monotonic() - t0

    all_verdicts: list[EligibilityVerdict] = prior_verdicts + new_verdicts

    eligible_count = sum(1 for v in all_verdicts if v.eligible is True)
    excluded_count = sum(1 for v in all_verdicts if v.eligible is False)
    no_source_count = sum(1 for v in all_verdicts if v.reason == "no-source")
    tei_parse_failure_count = sum(
        1 for v in all_verdicts if v.reason == "tei-parse-failure"
    )
    llm_parse_failure_count = sum(
        1 for v in all_verdicts if v.reason == "llm-parse-failure"
    )

    by_source_basis: dict[str, int] = {}
    for v in all_verdicts:
        by_source_basis[v.source_basis] = by_source_basis.get(v.source_basis, 0) + 1

    skipped_total = no_source_count + tei_parse_failure_count + llm_parse_failure_count
    print(
        f"\nDone in {elapsed:.1f}s: {eligible_count} eligible, "
        f"{excluded_count} excluded, {skipped_total} skipped "
        f"({no_source_count} no-source, {tei_parse_failure_count} tei-parse-failure, "
        f"{llm_parse_failure_count} llm-parse-failure).",
        file=sys.stderr,
    )

    if args.dry_run:
        return

    # Rebuild eligible.jsonl from the verdict sidecar + catalogue join.
    eligible_ids = {v.work_id for v in all_verdicts if v.eligible is True}
    eligible_works = [
        w for w in read_works_jsonl(args.catalogue) if w.id in eligible_ids
    ]
    write_jsonl(eligible_works, eligible_path)

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
        EligibilityMeta(
            run=run_meta,
            llm=llm_meta,
            input_catalogue=str(args.catalogue),
            input_extractions=str(args.extractions),
            input_count=total,
            eligible_count=eligible_count,
            excluded_count=excluded_count,
            no_source_count=no_source_count,
            tei_parse_failure_count=tei_parse_failure_count,
            llm_parse_failure_count=llm_parse_failure_count,
            by_source_basis=by_source_basis,
        ),
    )
