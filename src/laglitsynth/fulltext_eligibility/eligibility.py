"""Full-text eligibility assessment on the decisions endpoint.

Reads the works of ``data/corpus/`` and their stage 6 extractions. Each
work's rendered full text is sent as the ``input`` of one decision whose
predicate question comes from the eligibility-criteria YAML's ``decision``
block. The answer's probability is stored on the verdict and cut at 0.5
into ``eligible``.
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
from laglitsynth.corpus.load import (
    load_corpus_meta,
    read_corpus_pdfs,
    read_corpus_works,
    read_current_extractions,
)
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
    drop_jsonl_rows,
    read_jsonl,
    write_meta,
)
from laglitsynth.layout import (
    ELIGIBILITY_SUBDIR,
    corpus_dir,
    extractions_path,
    require_input,
    resolve_analysis_run_id,
)
from laglitsynth.llm import (
    LlmEndpoint,
    add_endpoint_args,
    decide,
    decisions_client,
    resolve_endpoint,
)
from laglitsynth.models import RunMeta
from laglitsynth.prompts import DecisionQuestion, load_decision_question

logger = logging.getLogger(__name__)

DEFAULT_ELIGIBILITY_CRITERIA = Path(
    "examples/eligibility-criteria/lagrangian-oceanography.yaml"
)

META_FILENAME = "eligibility-meta.json"

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


def assess_works(
    works: Iterable[Work],
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    *,
    client: httpx.Client,
    endpoint: LlmEndpoint,
    question: DecisionQuestion,
    concurrency: int = 1,
) -> Iterator[EligibilityVerdict]:
    eligible: list[Work] = []
    for work in works:
        # Full-text-only: a work without a current extraction never enters
        # the loop — no verdict row, no count. The gap is visible in the
        # corpus provenance and the stage 6 meta, so we don't flag it here.
        if work.id not in extractions:
            continue
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
        "--corpus-dir",
        type=Path,
        default=None,
        help="Corpus written by corpus-assemble (default: <data-dir>/corpus/).",
    )
    parser.add_argument(
        "--extractions",
        type=Path,
        default=None,
        help=(
            "Extraction JSONL, with extraction-meta.json beside it "
            "(default: <data-dir>/fulltext-extraction/extraction.jsonl)."
        ),
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
            "Run identifier, shared with the extraction-codebook run that reads "
            "these verdicts (default: analysis_run_id in <corpus-dir>/corpus-meta.json)."
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
        help="Process only the first N corpus works",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help=(
            "Skip works that already have a decided verdict in verdicts.jsonl; "
            "works whose verdict is a sentinel are assessed again"
        ),
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
    args.corpus_dir = (
        Path(args.corpus_dir) if args.corpus_dir is not None else corpus_dir(data_dir)
    )
    corpus_meta = load_corpus_meta(args.corpus_dir)
    args.run_id = resolve_analysis_run_id(args.corpus_dir, args.run_id)
    args.extractions = require_input(
        args.extractions if args.extractions is not None else extractions_path(data_dir),
        flag="--extractions",
        upstream="fulltext-extraction",
    )
    endpoint = resolve_endpoint(args)

    output_dir: Path = data_dir / ELIGIBILITY_SUBDIR / args.run_id
    verdicts_path = output_dir / "verdicts.jsonl"
    meta_path = output_dir / META_FILENAME

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

    # Resume guards: refuse --skip-existing when the recorded prompt hash
    # or corpus differs, so verdicts.jsonl never mixes either.
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
        if recorded.get("corpus_id") != corpus_meta.corpus_id:
            raise SystemExit(
                f"{meta_path} was made on corpus {recorded.get('corpus_id')}, but "
                f"the corpus is now {corpus_meta.corpus_id}; refusing "
                f"--skip-existing (pass another --run-id for a fresh run)"
            )

    stats = JsonlReadStats()
    extractions = read_current_extractions(
        args.extractions, corpus_meta, read_corpus_pdfs(args.corpus_dir), stats
    )

    # Full-text-only: gate on a current extraction so works without a PDF
    # never enter the assessed set, get no verdict row, and are not counted.
    active: list[Work] = [
        work for work in read_corpus_works(args.corpus_dir) if work.id in extractions
    ]
    total = len(active)

    prior_verdicts: list[EligibilityVerdict] = []
    if args.skip_existing and verdicts_path.exists():
        prior_verdicts = list(read_jsonl(verdicts_path, EligibilityVerdict, stats))
    # Works to send: no decided verdict yet, capped by --max-records. A work
    # whose verdict is a sentinel is sent again; its old row is dropped from
    # the file before any call, every other row stays.
    done_ids = {v.work_id for v in prior_verdicts if v.reason is None}
    to_send = [work for work in active if work.id not in done_ids][: args.max_records]
    send_ids = {work.id for work in to_send}

    def _replaced(verdict: EligibilityVerdict) -> bool:
        return verdict.reason is not None and verdict.work_id in send_ids

    if args.skip_existing:
        retried = len({v.work_id for v in prior_verdicts if _replaced(v)})
        print(
            f"Skipping {len(done_ids)} already-assessed works; "
            f"retrying {retried} works with a sentinel verdict.",
            file=sys.stderr,
        )
    prior_verdicts = [v for v in prior_verdicts if not _replaced(v)]

    print(f"Assessing {total} works with model {endpoint.model}", file=sys.stderr)
    if not args.dry_run:
        print(f"Output dir: {output_dir}", file=sys.stderr)

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        save_resolved_config(args, output_dir, inlines=["eligibility_criteria"])

    # Start-of-run truncation: non-resuming runs rewrite verdicts.jsonl.
    if not args.dry_run and not args.skip_existing and verdicts_path.exists():
        verdicts_path.unlink()
    if not args.dry_run and args.skip_existing and verdicts_path.exists():
        drop_jsonl_rows(verdicts_path, EligibilityVerdict, _replaced)

    client = decisions_client(endpoint, _LLM_TIMEOUT_SECONDS)

    t0 = time.monotonic()
    index = 0
    new_verdicts: list[EligibilityVerdict] = []

    try:
        for verdict in assess_works(
            to_send,
            extractions,
            extraction_output_dir,
            client=client,
            endpoint=endpoint,
            question=question,
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
            corpus_id=corpus_meta.corpus_id,
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
