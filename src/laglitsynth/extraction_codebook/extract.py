"""LLM-based codebook extraction via Ollama.

The LLM-fillable payload model and per-work record class are built at
``run()`` time from the codebook YAML pointed to by ``--codebook``. The
system prompt is rendered the same way — see
[codebook.py](codebook.py) for the loader and renderers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import sys
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path

from typing import cast

from lxml import etree
from openai import APIConnectionError, APITimeoutError, OpenAI
from pydantic import BaseModel, ValidationError

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.config import register_config_arg, resolve_yaml_arg, save_resolved_config
from laglitsynth.extraction_codebook.codebook import (
    CodebookContext,
    ExtractionRecordProto,
)
from laglitsynth.extraction_codebook.models import (
    TOOL_NAME,
    ExtractionCodebookMeta,
    SourceBasis,
)
from laglitsynth.extraction_codebook.prompts import (
    CHAR_BUDGET,
    USER_TEMPLATE,
    build_user_message,
    render_fulltext,
)
from laglitsynth.fulltext_eligibility.models import EligibilityVerdict
from laglitsynth.fulltext_extraction.models import ExtractedDocument
from laglitsynth.ids import generate_run_id
from laglitsynth.io import (
    JsonlReadStats,
    append_jsonl,
    read_jsonl,
    write_meta,
)
from laglitsynth.models import LlmMeta, RunMeta

logger = logging.getLogger(__name__)

STAGE_SUBDIR = "extraction-codebook"
DEFAULT_CODEBOOK = Path("examples/codebooks/lagrangian-oceanography.yaml")

_TEMPERATURE = 0.8
_NUM_CTX = 32768
_LLM_TIMEOUT_SECONDS = 600
_LLM_MAX_RETRIES = 3


def _sentinel_record(
    work_id: str,
    *,
    source_basis: SourceBasis,
    reason: str,
    ctx: CodebookContext,
    raw_response: str | None = None,
) -> ExtractionRecordProto:
    """Build a sentinel record: identification set, every content field None.

    ``raw_response`` is passed through on ``llm-parse-failure`` so an
    operator can see what the LLM actually said; left ``None`` on
    sentinels emitted without an LLM call (``no-source``,
    ``tei-parse-failure``).
    """
    payload_fields = {name: None for name in ctx.payload_field_names}
    record = ctx.record_model(
        work_id=work_id,
        source_basis=source_basis,
        reason=reason,
        seed=None,
        truncated=False,
        raw_response=raw_response,
        **payload_fields,
    )
    return cast(ExtractionRecordProto, record)


def extract_codebook(
    work_id: str,
    source_basis: SourceBasis,
    user_text: str,
    *,
    client: OpenAI,
    model: str,
    truncated: bool,
    ctx: CodebookContext,
) -> ExtractionRecordProto:
    """Call the LLM, validate the payload, compose the full record.

    On JSON parse error or ``ValidationError`` returns a
    ``reason="llm-parse-failure"`` sentinel with the called-branch
    ``source_basis``, ``seed=None``, ``truncated=False``, every content
    field ``None``.
    """
    seed = random.randint(0, 2**31 - 1)
    prompt = build_user_message(source_basis, user_text)
    try:
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": ctx.system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            seed=seed,
            extra_body={"options": {"num_ctx": _NUM_CTX}},
        )
    except (APITimeoutError, APIConnectionError) as exc:
        logger.warning("LLM timeout for %s: %s", work_id, exc)
        return _sentinel_record(
            work_id,
            source_basis=source_basis,
            reason="llm-timeout",
            ctx=ctx,
            raw_response=None,
        )
    content = response.choices[0].message.content or "{}"
    try:
        payload = ctx.payload_model.model_validate_json(content)
    except (ValidationError, ValueError) as exc:
        logger.warning("LLM parse failure for %s: %s", work_id, exc)
        return _sentinel_record(
            work_id,
            source_basis=source_basis,
            reason="llm-parse-failure",
            ctx=ctx,
            raw_response=content,
        )

    record = ctx.record_model(
        work_id=work_id,
        source_basis=source_basis,
        reason=None,
        seed=seed,
        truncated=truncated,
        raw_response=content,
        **payload.model_dump(),
    )
    return cast(ExtractionRecordProto, record)


def _active_eligible_works(
    catalogue_path: Path,
    eligibility_verdicts_path: Path,
    stats: JsonlReadStats | None = None,
) -> Iterator[Work]:
    """Yield Works that have an explicit ``eligible=True`` verdict.

    Joins the eligibility verdict sidecar to the catalogue.  Works with
    ``eligible=False`` or ``eligible=None`` (sentinel skips) are excluded.
    No screening threshold is re-applied here — stage 7 already gated by
    screening, so the eligibility verdict is the only gate needed.

    The catalogue is required (rather than just the verdict file) because
    stage 8 falls back to ``Work.abstract`` when TEI is missing.

    Raises ``KeyError`` if a verdict's ``work_id`` is absent from the
    catalogue — this signals a data inconsistency (stage 7 wrote a verdict
    for a work the catalogue does not contain).
    """
    ev = {
        v.work_id: v
        for v in read_jsonl(eligibility_verdicts_path, EligibilityVerdict, stats)
    }
    catalogue = {
        w.id: w for w in read_jsonl(catalogue_path, Work, stats)
    }
    for v in ev.values():
        if v.eligible is not True:
            continue
        if v.work_id not in catalogue:
            raise KeyError(
                f"work_id {v.work_id!r} found in eligibility verdicts but absent "
                f"from catalogue {catalogue_path}; this is a data inconsistency"
            )
        yield catalogue[v.work_id]


def _load_extractions(
    path: Path, stats: JsonlReadStats
) -> dict[str, ExtractedDocument]:
    return {rec.work_id: rec for rec in read_jsonl(path, ExtractedDocument, stats)}


def extract_works(
    works: Iterable[Work],
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    *,
    client: OpenAI,
    model: str,
    max_records: int | None,
    ctx: CodebookContext,
    skip_ids: set[str] | None = None,
) -> Iterator[ExtractionRecordProto]:
    skip_ids = skip_ids or set()
    processed = 0
    for work in works:
        if work.id in skip_ids:
            continue
        if max_records is not None and processed >= max_records:
            return
        processed += 1
        yield _extract_one(
            work, extractions, extraction_output_dir, client, model, ctx
        )


def _extract_one(
    work: Work,
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    client: OpenAI,
    model: str,
    ctx: CodebookContext,
) -> ExtractionRecordProto:
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
                work.id,
                source_basis="full_text",
                reason="tei-parse-failure",
                ctx=ctx,
            )
        if rendered:
            return extract_codebook(
                work.id,
                "full_text",
                rendered,
                client=client,
                model=model,
                truncated=truncated,
                ctx=ctx,
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
            ctx=ctx,
        )

    # Step 3: no source at all.
    logger.warning("No source for %s; recording no-source", work.id)
    return _sentinel_record(
        work.id,
        source_basis="none",
        reason="no-source",
        ctx=ctx,
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
        "extraction-codebook",
        help="Extract codebook records for eligible works using a local LLM.",
    )
    parser.add_argument(
        "--catalogue",
        type=Path,
        required=True,
        help=(
            "Deduplicated catalogue JSONL (data/catalogue-dedup/deduplicated.jsonl). "
            "Required for the abstract fallback path: when TEI is missing, stage 8 "
            "falls back to Work.abstract, so the full Work record is needed — not "
            "just the work_id from the eligibility verdict."
        ),
    )
    parser.add_argument(
        "--eligibility-verdicts",
        type=Path,
        required=True,
        help="Eligibility verdicts JSONL (data/fulltext-eligibility/<run-id>/verdicts.jsonl)",
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
        "--codebook",
        default=str(DEFAULT_CODEBOOK),
        help=(
            f"Codebook YAML — path to a CodebookSpec YAML on the CLI; on "
            f"reload from a saved snapshot, the inlined mapping is consumed "
            f"directly (default: {DEFAULT_CODEBOOK})."
        ),
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
    register_config_arg(parser)
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    _preflight(args)

    if args.run_id is None:
        args.run_id = generate_run_id()
    output_dir: Path = Path(args.data_dir) / STAGE_SUBDIR / args.run_id
    records_path = output_dir / "records.jsonl"
    meta_path = output_dir / "extraction-codebook-meta.json"

    extraction_output_dir: Path = (
        Path(args.extraction_output_dir)
        if args.extraction_output_dir is not None
        else Path(args.extractions).parent
    )

    ctx = CodebookContext.from_spec(resolve_yaml_arg(args.codebook))

    prompt_sha256 = hashlib.sha256(
        (
            ctx.system_prompt
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
            recorded = json.loads(meta_path.read_text())
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"failed to parse {meta_path} ({exc}); refusing --skip-existing "
                f"on a corrupted meta file"
            )
        recorded_sha = recorded.get("llm", {}).get("prompt_sha256")
        if recorded_sha is not None and recorded_sha != prompt_sha256:
            raise SystemExit(
                f"recorded prompt_sha256 in {meta_path} differs from current; "
                f"refusing --skip-existing to avoid mixing prompt versions in "
                f"{records_path}"
            )

    stats = JsonlReadStats()
    extractions = _load_extractions(args.extractions, stats)

    eligible_works = list(
        _active_eligible_works(args.catalogue, args.eligibility_verdicts, stats)
    )
    total = len(eligible_works)

    skip_ids: set[str] = set()
    prior_records: list[ExtractionRecordProto] = []
    if args.skip_existing and records_path.exists():
        prior_records = [
            cast(ExtractionRecordProto, r)
            for r in read_jsonl(records_path, ctx.record_model, stats)
        ]
        skip_ids = {r.work_id for r in prior_records}
        print(
            f"Skipping {len(skip_ids)} already-extracted works.",
            file=sys.stderr,
        )

    print(f"Extracting {total} works with model {args.model}", file=sys.stderr)
    if not args.dry_run:
        print(f"Output dir: {output_dir}", file=sys.stderr)

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        save_resolved_config(args, output_dir, inlines=["codebook"])

    # Start-of-run truncation: non-resuming runs rewrite records.jsonl.
    if not args.dry_run and not args.skip_existing and records_path.exists():
        records_path.unlink()

    client = OpenAI(
        base_url=f"{args.base_url}/v1",
        api_key="ollama",
        timeout=_LLM_TIMEOUT_SECONDS,
        max_retries=_LLM_MAX_RETRIES,
    )

    t0 = time.monotonic()
    index = 0
    new_records: list[ExtractionRecordProto] = []

    for record in extract_works(
        eligible_works,
        extractions,
        extraction_output_dir,
        client=client,
        model=args.model,
        max_records=args.max_records,
        ctx=ctx,
        skip_ids=skip_ids,
    ):
        index += 1
        new_records.append(record)
        if not args.dry_run:
            append_jsonl(cast(BaseModel, record), records_path)

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

    all_records: list[ExtractionRecordProto] = prior_records + new_records

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
    llm_timeout_count = sum(1 for r in all_records if r.reason == "llm-timeout")
    truncated_count = sum(1 for r in all_records if r.truncated)

    by_source_basis: dict[str, int] = {}
    for r in all_records:
        by_source_basis[r.source_basis] = by_source_basis.get(r.source_basis, 0) + 1

    print(
        f"\nDone in {elapsed:.1f}s: {full_text_count} full-text, "
        f"{abstract_only_count} abstract-only, {skipped_count} skipped, "
        f"{llm_parse_failure_count} llm-parse-failure, "
        f"{llm_timeout_count} llm-timeout, "
        f"{truncated_count} truncated.",
        file=sys.stderr,
    )

    if args.dry_run:
        return

    run_meta = RunMeta(
        tool=TOOL_NAME,
        run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        validation_skipped=stats.skipped,
    )
    llm_meta = LlmMeta(
        model=args.model,
        temperature=_TEMPERATURE,
        prompt_sha256=prompt_sha256,
    )
    write_meta(
        meta_path,
        ExtractionCodebookMeta(
            run=run_meta,
            llm=llm_meta,
            input_catalogue=str(args.catalogue),
            input_eligibility_verdicts=str(args.eligibility_verdicts),
            input_extractions=str(args.extractions),
            input_count=total,
            full_text_count=full_text_count,
            abstract_only_count=abstract_only_count,
            skipped_count=skipped_count,
            llm_parse_failure_count=llm_parse_failure_count,
            llm_timeout_count=llm_timeout_count,
            truncated_count=truncated_count,
            by_source_basis=by_source_basis,
        ),
    )
    print(f"Output dir: {output_dir}", file=sys.stderr)
