"""Full-text eligibility assessment on the decisions endpoint.

Each work's rendered full text is sent as the ``input`` of one decision
whose predicate question comes from the eligibility-criteria YAML's
``decision`` block. The answer's probability is stored on the verdict and
cut at 0.5 into ``eligible``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
import time
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from lxml import etree

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.concurrency import map_concurrent
from laglitsynth.config import register_config_arg, save_resolved_config
from laglitsynth.fulltext_eligibility.models import (
    TOOL_NAME,
    EligibilityMeta,
    EligibilityVerdict,
)
from laglitsynth.fulltext_eligibility.prompts import USER_TEMPLATE, render_fulltext
from laglitsynth.fulltext_extraction.models import ExtractedDocument
from laglitsynth.io import (
    JsonlReadStats,
    append_jsonl,
    read_jsonl,
    write_meta,
)
from laglitsynth.layout import (
    ELIGIBILITY_SUBDIR,
    extractions_path,
    require_input,
    resolve_screening_inputs,
)
from laglitsynth.llm import (
    LlmEndpoint,
    add_endpoint_args,
    decide,
    decisions_client,
    resolve_endpoint,
)
from laglitsynth.manifest import resolve_run_id
from laglitsynth.models import RunMeta
from laglitsynth.prompts import DecisionQuestion, load_decision_question
from laglitsynth.screening_abstracts.models import ScreeningVerdict

logger = logging.getLogger(__name__)

DEFAULT_ELIGIBILITY_CRITERIA = Path(
    "examples/eligibility-criteria/lagrangian-oceanography.yaml"
)

_LLM_TIMEOUT_SECONDS = 300.0

# Answer name of the eligibility predicate question.
QUESTION_NAME = "eligible"

# Probability at or above which a work is eligible. Any other cut is made
# ad hoc on verdicts.jsonl, which keeps the probability.
ELIGIBLE_AT = 0.5


def question_sha256(question: DecisionQuestion) -> str:
    """Prompt hash of a decisions stage: question JSON + input marker."""
    return hashlib.sha256(
        (question.canonical_json() + "\n" + USER_TEMPLATE).encode("utf-8")
    ).hexdigest()


def criteria_sha256(spec: str | Path | dict[str, Any]) -> str:
    """The ``prompt_sha256`` stage 7 records for an eligibility-criteria spec.

    Raises ``ValueError`` naming the spec when it has no ``decision`` block.
    """
    return question_sha256(load_decision_question(spec))


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
    text: str,
    *,
    endpoint: LlmEndpoint,
    client: httpx.Client,
    question: DecisionQuestion,
) -> EligibilityVerdict:
    """Decide one work on its rendered full text and compose the verdict.

    ``eligible`` is ``probability >= 0.5``. A sentinel from ``decide``
    (``llm-timeout``, ``llm-rejected``, ``llm-parse-failure``) leaves both
    ``None`` and lands in ``reason``.
    """
    decision = decide(client, endpoint, input=text, name=QUESTION_NAME, question=question)
    probability = decision.probability
    return EligibilityVerdict(
        work_id=work_id,
        probability=probability,
        eligible=None if probability is None else probability >= ELIGIBLE_AT,
        reason=decision.reason,
        raw_response=decision.raw_response,
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
    client: httpx.Client,
    endpoint: LlmEndpoint,
    max_records: int | None,
    question: DecisionQuestion,
    skip_ids: set[str] | None = None,
    concurrency: int = 1,
) -> Iterator[EligibilityVerdict]:
    skip_ids = skip_ids or set()
    eligible: list[Work] = []
    for work in works:
        if work.id in skip_ids:
            continue
        # Full-text-only: a work without an extraction never enters the
        # loop — no verdict row, no count. The gap is already visible
        # upstream (stage 5/6 provenance), so we don't flag it here.
        if work.id not in extractions:
            continue
        if max_records is not None and len(eligible) >= max_records:
            break
        eligible.append(work)

    def _call_one(work: Work) -> EligibilityVerdict:
        return _assess_one(
            work, extractions, extraction_output_dir, client, endpoint, question
        )

    yield from map_concurrent(_call_one, eligible, max_workers=concurrency)


def _assess_one(
    work: Work,
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    client: httpx.Client,
    endpoint: LlmEndpoint,
    question: DecisionQuestion,
) -> EligibilityVerdict:
    # Full text or nothing: the active set is already gated on extraction
    # presence, so ``extractions[work.id]`` is always set here.
    extracted = extractions[work.id]
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
            reason="tei-parse-failure",
        )
    if not rendered:
        # Valid XML but empty body: broken extraction on a paper we do
        # have a PDF for — worth surfacing, like malformed TEI.
        logger.warning(
            "Empty TEI body for %s; recording tei-parse-failure", work.id
        )
        return EligibilityVerdict(
            work_id=work.id,
            eligible=None,
            reason="tei-parse-failure",
        )
    return classify_eligibility(
        work.id,
        rendered,
        endpoint=endpoint,
        client=client,
        question=question,
    )


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "fulltext-eligibility",
        help="Assess full-text eligibility of included works on the decisions endpoint.",
    )
    parser.add_argument(
        "--catalogue",
        type=Path,
        default=None,
        help="Deduplicated catalogue JSONL (default: <data-dir>/catalogue-dedup/deduplicated.jsonl).",
    )
    parser.add_argument(
        "--screening-verdicts",
        type=Path,
        default=None,
        help="Stage 3 verdicts JSONL (default: <data-dir>/screening-abstracts/<run-id>/verdicts.jsonl).",
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
        default=None,
        help="Extraction JSONL (default: <data-dir>/fulltext-extraction/extraction.jsonl).",
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
        help=(
            "Run identifier, shared with the screening run whose verdicts are "
            "read (default: the run_id in <data-dir>/manifest.json)."
        ),
    )
    parser.add_argument(
        "--eligibility-criteria",
        default=str(DEFAULT_ELIGIBILITY_CRITERIA),
        help=(
            f"Eligibility-criteria YAML carrying a 'decision' block — path to "
            f"a spec on the CLI; on reload from a saved snapshot, the inlined "
            f"mapping is consumed directly (default: {DEFAULT_ELIGIBILITY_CRITERIA})."
        ),
    )
    add_endpoint_args(parser, decisions=True)
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
    register_config_arg(parser)
    parser.set_defaults(run=run)
    return parser


def run(args: argparse.Namespace) -> None:
    data_dir: Path = Path(args.data_dir)
    args.run_id = resolve_run_id(data_dir, args.run_id)
    args.catalogue, args.screening_verdicts = resolve_screening_inputs(
        data_dir, args.catalogue, args.screening_verdicts, args.run_id
    )
    args.extractions = require_input(
        args.extractions if args.extractions is not None else extractions_path(data_dir),
        flag="--extractions",
        upstream="fulltext-extraction",
    )
    endpoint = resolve_endpoint(args)

    output_dir: Path = data_dir / ELIGIBILITY_SUBDIR / args.run_id
    verdicts_path = output_dir / "verdicts.jsonl"
    meta_path = output_dir / "eligibility-meta.json"

    extraction_output_dir: Path = (
        Path(args.extraction_output_dir)
        if args.extraction_output_dir is not None
        else Path(args.extractions).parent
    )

    # Pass the un-resolved spec so a missing block names the file.
    try:
        question = load_decision_question(args.eligibility_criteria)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    prompt_sha256 = question_sha256(question)

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

    # Full-text-only: gate on extraction presence so works without a PDF
    # never enter the assessed set, get no verdict row, and are not
    # counted. The gap is already visible in upstream provenance.
    active: list[Work] = [
        work
        for work in _active_works(
            args.catalogue,
            args.screening_verdicts,
            args.screening_threshold,
            stats,
        )
        if work.id in extractions
    ]
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

    print(f"Assessing {total} works with model {endpoint.model}", file=sys.stderr)
    if not args.dry_run:
        print(f"Output dir: {output_dir}", file=sys.stderr)

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        save_resolved_config(args, output_dir, inlines=["eligibility_criteria"])

    # Start-of-run truncation: non-resuming runs rewrite verdicts.jsonl.
    if not args.dry_run and not args.skip_existing and verdicts_path.exists():
        verdicts_path.unlink()

    client = decisions_client(endpoint, _LLM_TIMEOUT_SECONDS)

    t0 = time.monotonic()
    index = 0
    new_verdicts: list[EligibilityVerdict] = []

    try:
        for verdict in assess_works(
            active,
            extractions,
            extraction_output_dir,
            client=client,
            endpoint=endpoint,
            max_records=args.max_records,
            question=question,
            skip_ids=skip_ids,
            concurrency=args.concurrency,
        ):
            index += 1
            new_verdicts.append(verdict)
            if not args.dry_run:
                append_jsonl(verdict, verdicts_path)

            if verdict.eligible is None:
                print(
                    f"  [{index}/{total}] skipped ({verdict.reason})"
                    f" — {verdict.work_id[-12:]}",
                    file=sys.stderr,
                )
            else:
                label = "eligible" if verdict.eligible else "excluded"
                print(
                    f"  [{index}/{total}] {label} (p={verdict.probability:.2f})"
                    f" — {verdict.work_id[-12:]}",
                    file=sys.stderr,
                )
    finally:
        client.close()

    elapsed = time.monotonic() - t0

    all_verdicts: list[EligibilityVerdict] = prior_verdicts + new_verdicts

    eligible_count = sum(1 for v in all_verdicts if v.eligible is True)
    excluded_count = sum(1 for v in all_verdicts if v.eligible is False)
    tei_parse_failure_count = sum(
        1 for v in all_verdicts if v.reason == "tei-parse-failure"
    )
    llm_parse_failure_count = sum(
        1 for v in all_verdicts if v.reason == "llm-parse-failure"
    )
    llm_timeout_count = sum(
        1 for v in all_verdicts if v.reason == "llm-timeout"
    )
    llm_rejected_count = sum(
        1 for v in all_verdicts if v.reason == "llm-rejected"
    )

    skipped_total = (
        tei_parse_failure_count
        + llm_parse_failure_count
        + llm_timeout_count
        + llm_rejected_count
    )
    print(
        f"\nDone in {elapsed:.1f}s: {eligible_count} eligible, "
        f"{excluded_count} excluded, {skipped_total} skipped "
        f"({tei_parse_failure_count} tei-parse-failure, "
        f"{llm_parse_failure_count} llm-parse-failure, "
        f"{llm_timeout_count} llm-timeout, "
        f"{llm_rejected_count} llm-rejected).",
        file=sys.stderr,
    )

    if args.dry_run:
        return

    run_meta = RunMeta(
        tool=TOOL_NAME,
        run_at=datetime.now(UTC).isoformat(timespec="microseconds"),
        validation_skipped=stats.skipped,
    )
    llm_meta = endpoint.llm_meta(prompt_sha256)
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
            tei_parse_failure_count=tei_parse_failure_count,
            llm_parse_failure_count=llm_parse_failure_count,
            llm_timeout_count=llm_timeout_count,
            llm_rejected_count=llm_rejected_count,
            criterion=question.render(),
        ),
    )
    print(f"Run dir: {output_dir}", file=sys.stderr)
