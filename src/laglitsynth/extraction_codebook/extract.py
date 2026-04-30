"""LLM-based codebook extraction via Ollama."""

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

from lxml import etree
from openai import OpenAI
from pydantic import ValidationError

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.extraction_codebook.models import (
    TOOL_NAME,
    ExtractionCodebookMeta,
    ExtractionRecord,
    SourceBasis,
    _ExtractionPayload,
)
from laglitsynth.extraction_codebook.prompts import (
    CHAR_BUDGET,
    SYSTEM_PROMPT,
    USER_TEMPLATE,
    build_user_message,
    render_fulltext,
)
from laglitsynth.fulltext_extraction.models import ExtractedDocument
from laglitsynth.io import (
    JsonlReadStats,
    append_jsonl,
    read_jsonl,
    read_works_jsonl,
    write_meta,
)
from laglitsynth.models import _LlmMeta, _RunMeta

logger = logging.getLogger(__name__)

_TEMPERATURE = 0.8
_NUM_CTX = 32768


def _sentinel_record(
    work_id: str,
    *,
    source_basis: SourceBasis,
    reason: str,
    raw_response: str | None = None,
) -> ExtractionRecord:
    """Build a sentinel record: identification set, every content field None.

    ``raw_response`` is passed through on ``llm-parse-failure`` so an
    operator can see what the LLM actually said; left ``None`` on
    sentinels emitted without an LLM call (``no-source``,
    ``tei-parse-failure``).
    """
    payload_fields = {name: None for name in _ExtractionPayload.model_fields}
    return ExtractionRecord(
        work_id=work_id,
        source_basis=source_basis,
        reason=reason,
        seed=None,
        truncated=False,
        raw_response=raw_response,
        **payload_fields,
    )


def extract_codebook(
    work_id: str,
    source_basis: SourceBasis,
    user_text: str,
    *,
    client: OpenAI,
    model: str,
    truncated: bool,
) -> ExtractionRecord:
    """Call the LLM, validate the payload, compose the full record.

    On JSON parse error or ``ValidationError`` returns a
    ``reason="llm-parse-failure"`` sentinel with the called-branch
    ``source_basis``, ``seed=None``, ``truncated=False``, every content
    field ``None``.
    """
    seed = random.randint(0, 2**31 - 1)
    prompt = build_user_message(source_basis, user_text)
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
        payload = _ExtractionPayload.model_validate_json(content)
    except (ValidationError, ValueError) as exc:
        logger.warning("LLM parse failure for %s: %s", work_id, exc)
        return _sentinel_record(
            work_id,
            source_basis=source_basis,
            reason="llm-parse-failure",
            raw_response=content,
        )

    return ExtractionRecord(
        work_id=work_id,
        source_basis=source_basis,
        reason=None,
        seed=seed,
        truncated=truncated,
        raw_response=content,
        **payload.model_dump(),
    )


def _load_extractions(
    path: Path, stats: JsonlReadStats
) -> dict[str, ExtractedDocument]:
    return {rec.work_id: rec for rec in read_jsonl(path, ExtractedDocument, stats)}


def extract_works(
    catalogue_path: Path,
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    *,
    client: OpenAI,
    model: str,
    max_records: int | None,
    skip_ids: set[str] | None = None,
    stats: JsonlReadStats | None = None,
) -> Iterator[ExtractionRecord]:
    skip_ids = skip_ids or set()
    processed = 0
    for work in read_works_jsonl(catalogue_path, stats):
        if work.id in skip_ids:
            continue
        if max_records is not None and processed >= max_records:
            return
        processed += 1
        yield _extract_one(work, extractions, extraction_output_dir, client, model)


def _extract_one(
    work: Work,
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    client: OpenAI,
    model: str,
) -> ExtractionRecord:
    # Step 1: prefer full text when an extraction exists.
    extracted = extractions.get(work.id)
    if extracted is not None:
        try:
            tei = extracted.open_tei(extraction_output_dir)
            rendered, truncated = render_fulltext(tei, char_budget=CHAR_BUDGET)
        except etree.XMLSyntaxError:
            logger.warning(
                "Malformed TEI for %s; recording tei-parse-failure", work.id
            )
            return _sentinel_record(
                work.id, source_basis="full_text", reason="tei-parse-failure"
            )
        if rendered:
            return extract_codebook(
                work.id,
                "full_text",
                rendered,
                client=client,
                model=model,
                truncated=truncated,
            )
        # Empty body (valid XML, no content): fall through to abstract.

    # Step 2: fall back to abstract when available.
    if work.abstract:
        return extract_codebook(
            work.id,
            "abstract_only",
            work.abstract,
            client=client,
            model=model,
            truncated=False,
        )

    # Step 3: no source at all.
    logger.warning("No source for %s; recording no-source", work.id)
    return _sentinel_record(work.id, source_basis="none", reason="no-source")


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
        "extraction-codebook",
        help="Extract codebook records for eligible works using a local LLM.",
    )
    parser.add_argument(
        "--eligible",
        type=Path,
        required=True,
        help="Eligible catalogue JSONL (data/fulltext-eligibility/eligible.jsonl)",
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
        default=Path("data/extraction-codebook"),
        help="Output directory (default: data/extraction-codebook/)",
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
        help="Process only the first N eligible works",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip works that already have a record in records.jsonl",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print summaries to stderr without writing output",
    )
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    _preflight(args)

    output_dir: Path = args.output_dir
    records_path = output_dir / "records.jsonl"
    meta_path = output_dir / "extraction-codebook-meta.json"

    extraction_output_dir: Path = (
        args.extraction_output_dir
        if args.extraction_output_dir is not None
        else args.extractions.parent
    )

    prompt_sha256 = hashlib.sha256(
        (
            SYSTEM_PROMPT
            + "\n"
            + USER_TEMPLATE
            + "\n"
            + str(_NUM_CTX)
            + "\n"
            + str(CHAR_BUDGET)
        ).encode("utf-8")
    ).hexdigest()

    # Prompt-hash guard: refuse --skip-existing when the recorded hash differs.
    if args.skip_existing and meta_path.exists():
        try:
            recorded_sha = json.loads(meta_path.read_text())["llm"]["prompt_sha256"]
            if recorded_sha != prompt_sha256:
                raise SystemExit(
                    f"recorded prompt_sha256 in {meta_path} differs from current; "
                    f"refusing --skip-existing to avoid mixing prompt versions in "
                    f"{records_path}"
                )
        except (KeyError, json.JSONDecodeError):
            pass  # malformed or missing key: let the run proceed

    stats = JsonlReadStats()
    extractions = _load_extractions(args.extractions, stats)

    total = sum(1 for _ in read_works_jsonl(args.eligible, stats))

    skip_ids: set[str] = set()
    prior_records: list[ExtractionRecord] = []
    if args.skip_existing and records_path.exists():
        prior_records = list(read_jsonl(records_path, ExtractionRecord, stats))
        skip_ids = {r.work_id for r in prior_records}
        print(
            f"Skipping {len(skip_ids)} already-extracted works.",
            file=sys.stderr,
        )

    print(f"Extracting {total} works with model {args.model}", file=sys.stderr)
    if not args.dry_run:
        print(f"Output dir: {output_dir}", file=sys.stderr)

    # Start-of-run truncation: non-resuming runs rewrite records.jsonl.
    if not args.dry_run and not args.skip_existing and records_path.exists():
        records_path.unlink()

    client = OpenAI(base_url=f"{args.base_url}/v1", api_key="ollama")

    t0 = time.monotonic()
    index = 0
    new_records: list[ExtractionRecord] = []

    for record in extract_works(
        args.eligible,
        extractions,
        extraction_output_dir,
        client=client,
        model=args.model,
        max_records=args.max_records,
        skip_ids=skip_ids,
        stats=stats,
    ):
        index += 1
        new_records.append(record)
        if not args.dry_run:
            append_jsonl(record, records_path)

        if record.reason is None:
            marker = "truncated" if record.truncated else "ok"
            print(
                f"  [{index}/{total}] extracted ({record.source_basis}, {marker})"
                f" — {record.work_id[-12:]}",
                file=sys.stderr,
            )
        else:
            print(
                f"  [{index}/{total}] skipped ({record.reason})"
                f" — {record.work_id[-12:]}",
                file=sys.stderr,
            )

    elapsed = time.monotonic() - t0

    all_records: list[ExtractionRecord] = prior_records + new_records

    full_text_count = sum(
        1
        for r in all_records
        if r.source_basis == "full_text" and r.reason is None
    )
    abstract_only_count = sum(
        1
        for r in all_records
        if r.source_basis == "abstract_only" and r.reason is None
    )
    skipped_count = sum(
        1 for r in all_records if r.reason in ("no-source", "tei-parse-failure")
    )
    llm_parse_failure_count = sum(
        1 for r in all_records if r.reason == "llm-parse-failure"
    )
    truncated_count = sum(1 for r in all_records if r.truncated)

    by_source_basis: dict[str, int] = {}
    for r in all_records:
        by_source_basis[r.source_basis] = by_source_basis.get(r.source_basis, 0) + 1

    print(
        f"\nDone in {elapsed:.1f}s: {full_text_count} full-text, "
        f"{abstract_only_count} abstract-only, {skipped_count} skipped, "
        f"{llm_parse_failure_count} llm-parse-failure, "
        f"{truncated_count} truncated.",
        file=sys.stderr,
    )

    if args.dry_run:
        return

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
        ExtractionCodebookMeta(
            run=run_meta,
            llm=llm_meta,
            input_catalogue=str(args.eligible),
            input_extractions=str(args.extractions),
            input_count=total,
            full_text_count=full_text_count,
            abstract_only_count=abstract_only_count,
            skipped_count=skipped_count,
            llm_parse_failure_count=llm_parse_failure_count,
            truncated_count=truncated_count,
            by_source_basis=by_source_basis,
        ),
    )
