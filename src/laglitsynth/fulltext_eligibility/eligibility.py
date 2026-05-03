"""LLM-based full-text eligibility assessment via Ollama."""

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

from lxml import etree
from openai import APIConnectionError, APITimeoutError, OpenAI
from pydantic import ValidationError

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.concurrency import map_concurrent
from laglitsynth.config import register_config_arg, resolve_yaml_arg, save_resolved_config
from laglitsynth.fulltext_eligibility.models import (
    TOOL_NAME,
    EligibilityMeta,
    EligibilityVerdict,
    SourceBasis,
    _EligibilityPayload,
)
from laglitsynth.fulltext_eligibility.prompts import (
    USER_TEMPLATE,
    build_user_message,
    load_system_prompt,
    render_fulltext,
)
from laglitsynth.fulltext_extraction.models import ExtractedDocument
from laglitsynth.ids import generate_run_id
from laglitsynth.io import (
    JsonlReadStats,
    append_jsonl,
    read_jsonl,
    write_meta,
)
from laglitsynth.models import LlmMeta, RunMeta
from laglitsynth.ollama import preflight
from laglitsynth.screening_abstracts.models import ScreeningVerdict

logger = logging.getLogger(__name__)

STAGE_SUBDIR = "fulltext-eligibility"
DEFAULT_ELIGIBILITY_CRITERIA = Path(
    "examples/eligibility-criteria/lagrangian-oceanography.yaml"
)

_TEMPERATURE = 0.8
_DEFAULT_NUM_CTX = 32768
_LLM_TIMEOUT_SECONDS = 300
_LLM_MAX_RETRIES = 3


def _active_works(
    catalogue_path: Path,
    screening_verdicts_path: Path,
    screening_threshold: float,
    stats: JsonlReadStats | None = None,
) -> Iterator[Work]:
    """Yield Works that pass the screening threshold gate.

    Works whose ``ScreeningVerdict.relevance_score`` is None (sentinels:
    ``no-abstract``, ``llm-parse-failure``, ``llm-timeout``) are never
    filtered — absence of a score is not evidence of irrelevance.  Only an
    explicit numeric score strictly below ``screening_threshold`` excludes.

    Works present in the catalogue but absent from the verdicts file are also
    excluded (they were never screened).
    """
    verdicts = {
        v.work_id: v
        for v in read_jsonl(screening_verdicts_path, ScreeningVerdict, stats)
    }
    for w in read_jsonl(catalogue_path, Work, stats):
        sv = verdicts.get(w.id)
        if sv is None:
            continue
        if sv.relevance_score is not None and sv.relevance_score < screening_threshold:
            continue
        yield w


def classify_eligibility(
    work_id: str,
    prompt: str,
    source_basis: SourceBasis,
    *,
    model: str,
    client: OpenAI,
    system_prompt: str,
    num_ctx: int = _DEFAULT_NUM_CTX,
) -> EligibilityVerdict:
    """Call the LLM, validate the payload, compose the verdict.

    On JSON parse error or ``ValidationError`` returns a
    ``reason="llm-parse-failure"`` sentinel with ``eligible=None``,
    ``seed=None``, and the raw response attached for audit. Mirrors
    stage 8's ``extract_codebook`` shape and error handling.
    """
    seed = random.randint(0, 2**31 - 1)
    try:
        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=_TEMPERATURE,
            seed=seed,
            extra_body={"options": {"num_ctx": num_ctx}},
        )
    except (APITimeoutError, APIConnectionError) as exc:
        logger.warning("LLM timeout for %s: %s", work_id, exc)
        return EligibilityVerdict(
            work_id=work_id,
            eligible=None,
            source_basis=source_basis,
            reason="llm-timeout",
            seed=None,
            raw_response=None,
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
    works: Iterable[Work],
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    *,
    client: OpenAI,
    model: str,
    num_ctx: int = _DEFAULT_NUM_CTX,
    max_records: int | None,
    system_prompt: str,
    skip_ids: set[str] | None = None,
    concurrency: int = 1,
) -> Iterator[EligibilityVerdict]:
    skip_ids = skip_ids or set()
    eligible: list[Work] = []
    for work in works:
        if work.id in skip_ids:
            continue
        if max_records is not None and len(eligible) >= max_records:
            break
        eligible.append(work)

    def _call_one(work: Work) -> EligibilityVerdict:
        return _assess_one(
            work, extractions, extraction_output_dir, client, model, system_prompt, num_ctx
        )

    yield from map_concurrent(_call_one, eligible, max_workers=concurrency)


def _assess_one(
    work: Work,
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    client: OpenAI,
    model: str,
    system_prompt: str,
    num_ctx: int = _DEFAULT_NUM_CTX,
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
                work.id,
                prompt,
                "full_text",
                client=client,
                model=model,
                system_prompt=system_prompt,
                num_ctx=num_ctx,
            )
        # Empty body (valid XML, no content): fall through to abstract.

    # Step 2: fall back to abstract when available.
    if work.abstract:
        prompt = build_user_message("abstract_only", work.abstract)
        return classify_eligibility(
            work.id,
            prompt,
            "abstract_only",
            client=client,
            model=model,
            system_prompt=system_prompt,
            num_ctx=num_ctx,
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
        help="Deduplicated catalogue JSONL (data/catalogue-dedup/deduplicated.jsonl)",
    )
    parser.add_argument(
        "--screening-verdicts",
        type=Path,
        required=True,
        help="Stage 3 verdicts JSONL (data/screening-abstracts/<run-id>/verdicts.jsonl)",
    )
    parser.add_argument(
        "--screening-threshold",
        type=float,
        default=50.0,
        help="Relevance score cutoff, 0-100 (default: 50)",
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
        "--eligibility-criteria",
        default=str(DEFAULT_ELIGIBILITY_CRITERIA),
        help=(
            f"Eligibility-criteria YAML — path to a spec on the CLI; on "
            f"reload from a saved snapshot, the inlined mapping is "
            f"consumed directly (default: {DEFAULT_ELIGIBILITY_CRITERIA})."
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
        "--num-ctx",
        type=int,
        default=_DEFAULT_NUM_CTX,
        dest="num_ctx",
        help=(
            "Ollama context-window hint passed via extra_body (default: 32768). "
            "Only fully reliable when the model is Modelfile-baked with the same value."
        ),
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
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help=(
            "In-flight LLM requests (default: 1). Stage 7 benefits more from "
            "concurrency than stage 8 because its prompts are less prefill-heavy. "
            "Must not exceed the Ollama server's OLLAMA_NUM_PARALLEL for actual "
            "parallelism. See docs/llm-concurrency.md."
        ),
    )
    register_config_arg(parser)
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    preflight(base_url=args.base_url, model=args.model)

    if args.run_id is None:
        args.run_id = generate_run_id()
    output_dir: Path = Path(args.data_dir) / STAGE_SUBDIR / args.run_id
    verdicts_path = output_dir / "verdicts.jsonl"
    meta_path = output_dir / "eligibility-meta.json"

    extraction_output_dir: Path = (
        Path(args.extraction_output_dir)
        if args.extraction_output_dir is not None
        else Path(args.extractions).parent
    )

    criteria_spec = resolve_yaml_arg(args.eligibility_criteria)
    system_prompt = load_system_prompt(criteria_spec)

    prompt_sha256 = hashlib.sha256(
        (system_prompt + "\n" + USER_TEMPLATE + "\n" + str(args.num_ctx)).encode("utf-8")
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
                f"{verdicts_path}"
            )

    stats = JsonlReadStats()
    extractions = _load_extractions(args.extractions, stats)

    active: list[Work] = list(
        _active_works(
            args.catalogue,
            args.screening_verdicts,
            args.screening_threshold,
            stats,
        )
    )
    total = len(active)

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

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        save_resolved_config(args, output_dir, inlines=["eligibility_criteria"])

    # Start-of-run truncation: non-resuming runs rewrite verdicts.jsonl.
    if not args.dry_run and not args.skip_existing and verdicts_path.exists():
        verdicts_path.unlink()

    client = OpenAI(
        base_url=f"{args.base_url}/v1",
        api_key="ollama",
        timeout=_LLM_TIMEOUT_SECONDS,
        max_retries=_LLM_MAX_RETRIES,
    )

    t0 = time.monotonic()
    index = 0
    new_verdicts: list[EligibilityVerdict] = []

    for verdict in assess_works(
        active,
        extractions,
        extraction_output_dir,
        client=client,
        model=args.model,
        num_ctx=args.num_ctx,
        max_records=args.max_records,
        system_prompt=system_prompt,
        skip_ids=skip_ids,
        concurrency=args.concurrency,
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
    llm_timeout_count = sum(
        1 for v in all_verdicts if v.reason == "llm-timeout"
    )

    by_source_basis: dict[str, int] = {}
    for v in all_verdicts:
        by_source_basis[v.source_basis] = by_source_basis.get(v.source_basis, 0) + 1

    skipped_total = (
        no_source_count
        + tei_parse_failure_count
        + llm_parse_failure_count
        + llm_timeout_count
    )
    print(
        f"\nDone in {elapsed:.1f}s: {eligible_count} eligible, "
        f"{excluded_count} excluded, {skipped_total} skipped "
        f"({no_source_count} no-source, {tei_parse_failure_count} tei-parse-failure, "
        f"{llm_parse_failure_count} llm-parse-failure, "
        f"{llm_timeout_count} llm-timeout).",
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
        EligibilityMeta(
            run=run_meta,
            llm=llm_meta,
            input_catalogue=str(args.catalogue),
            input_screening_verdicts=str(args.screening_verdicts),
            input_extractions=str(args.extractions),
            input_count=total,
            eligible_count=eligible_count,
            excluded_count=excluded_count,
            no_source_count=no_source_count,
            tei_parse_failure_count=tei_parse_failure_count,
            llm_parse_failure_count=llm_parse_failure_count,
            llm_timeout_count=llm_timeout_count,
            by_source_basis=by_source_basis,
        ),
    )
    print(f"Run dir: {output_dir}", file=sys.stderr)
