"""LLM-based codebook extraction.

The LLM-fillable payload model and per-work record class are built at
``run()`` time from the codebook YAML pointed to by ``--codebook``. The
system prompt is rendered the same way — see
[codebook.py](codebook.py) for the loader and renderers.

The stage runs on the corpus works with a positive eligibility verdict. It
refuses verdicts made on another corpus or with other eligibility criteria
than ``--eligibility-criteria``, and uses only extractions of the current
corpus PDFs.
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
from typing import Any, cast

from lxml import etree
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from laglitsynth.catalogue_fetch.models import Work
from laglitsynth.concurrency import map_concurrent
from laglitsynth.config import register_config_arg, resolve_yaml_arg, save_resolved_config
from laglitsynth.corpus.load import (
    load_corpus_meta,
    load_upstream_meta,
    read_corpus_pdfs,
    read_corpus_works,
    read_current_extractions,
    require_corpus,
)
from laglitsynth.extraction_codebook.codebook import (
    CodebookContext,
    ExtractionRecordProto,
)
from laglitsynth.extraction_codebook.models import (
    TOOL_NAME,
    ExtractionCodebookMeta,
)
from laglitsynth.extraction_codebook.prompts import (
    USER_TEMPLATE,
    build_user_message,
    render_fulltext,
)
from laglitsynth.fulltext_eligibility.eligibility import (
    DEFAULT_ELIGIBILITY_CRITERIA,
    criteria_sha256,
)
from laglitsynth.fulltext_eligibility.eligibility import (
    META_FILENAME as ELIGIBILITY_META_FILENAME,
)
from laglitsynth.fulltext_eligibility.models import EligibilityMeta, EligibilityVerdict
from laglitsynth.fulltext_extraction.models import ExtractedDocument
from laglitsynth.io import (
    JsonlReadStats,
    append_jsonl,
    read_jsonl,
    write_meta,
)
from laglitsynth.layout import (
    EXTRACTION_CODEBOOK_SUBDIR,
    corpus_dir,
    eligibility_verdicts_path,
    extractions_path,
    require_input,
    resolve_analysis_run_id,
)
from laglitsynth.llm import (
    REASONING_EFFORTS,
    LlmEndpoint,
    LlmFailure,
    add_endpoint_args,
    chat_client,
    chat_json,
    resolve_endpoint,
)
from laglitsynth.models import RunMeta

logger = logging.getLogger(__name__)

DEFAULT_CODEBOOK = Path("examples/codebooks/lagrangian-oceanography.yaml")

_LLM_TIMEOUT_SECONDS = 600


def _sentinel_record(
    work_id: str,
    *,
    reason: str,
    ctx: CodebookContext,
    raw_response: str | None = None,
) -> ExtractionRecordProto:
    """Build a sentinel record: identification set, every content field None.

    ``raw_response`` is passed through on ``llm-parse-failure`` and
    ``llm-rejected`` so an operator can see what the endpoint said; left
    ``None`` on sentinels emitted without a response (``tei-parse-failure``,
    ``llm-timeout``).
    """
    payload_fields = {name: None for name in ctx.payload_field_names}
    record = ctx.record_model(
        work_id=work_id,
        reason=reason,
        raw_response=raw_response,
        **payload_fields,
    )
    return cast(ExtractionRecordProto, record)


def extract_codebook(
    work_id: str,
    user_text: str,
    *,
    client: OpenAI,
    endpoint: LlmEndpoint,
    ctx: CodebookContext,
) -> ExtractionRecordProto:
    """Call the LLM, validate the payload, compose the full record.

    A per-work endpoint failure gives the ``llm-timeout`` or
    ``llm-rejected`` sentinel; an empty or unfinished answer (see
    ``chat_json``), a JSON parse error or a ``ValidationError`` gives
    ``llm-parse-failure``. Every sentinel has all content fields
    ``None``.
    """
    prompt = build_user_message(user_text)
    try:
        content = chat_json(
            client, endpoint, work_id=work_id, system=ctx.system_prompt, user=prompt
        )
    except LlmFailure as exc:
        return _sentinel_record(
            work_id, reason=exc.reason, ctx=ctx, raw_response=exc.raw_response
        )
    try:
        payload = ctx.payload_model.model_validate_json(content)
    except (ValidationError, ValueError) as exc:
        logger.warning("LLM parse failure for %s: %s", work_id, exc)
        return _sentinel_record(
            work_id,
            reason="llm-parse-failure",
            ctx=ctx,
            raw_response=content,
        )

    record = ctx.record_model(
        work_id=work_id,
        reason=None,
        raw_response=content,
        **payload.model_dump(),
    )
    return cast(ExtractionRecordProto, record)


def _active_eligible_works(
    works: list[Work],
    eligibility_verdicts_path: Path,
    stats: JsonlReadStats | None = None,
) -> Iterator[Work]:
    """Yield the corpus works that have an explicit ``eligible=True`` verdict.

    Works with ``eligible=False`` or ``eligible=None`` (sentinel skips) are
    excluded. Exits when a verdict names a work the corpus does not
    contain: the verdicts and the corpus then do not belong together.
    """
    corpus = {w.id: w for w in works}
    for v in read_jsonl(eligibility_verdicts_path, EligibilityVerdict, stats):
        if v.eligible is not True:
            continue
        if v.work_id not in corpus:
            raise SystemExit(
                f"work_id {v.work_id!r} has an eligibility verdict in "
                f"{eligibility_verdicts_path} but is not in the corpus; re-run "
                f"fulltext-eligibility"
            )
        yield corpus[v.work_id]


def _check_eligibility_meta(
    verdicts_path: Path, corpus_id: str, eligibility_criteria: str | Path | dict[str, Any]
) -> str:
    """Return the eligibility prompt hash; exit when the verdicts do not fit.

    The meta beside the verdicts must exist, name the current corpus, and
    record the ``prompt_sha256`` of ``--eligibility-criteria``.
    """
    meta_path = verdicts_path.parent / ELIGIBILITY_META_FILENAME
    meta = load_upstream_meta(
        meta_path,
        EligibilityMeta,
        flag="--eligibility-verdicts",
        upstream="fulltext-eligibility",
    )
    require_corpus(meta.corpus_id, corpus_id, meta_path, rerun="fulltext-eligibility")
    try:
        expected = criteria_sha256(eligibility_criteria)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if meta.llm.prompt_sha256 != expected:
        raise SystemExit(
            f"{meta_path} records prompt_sha256 {meta.llm.prompt_sha256}, but "
            f"--eligibility-criteria hashes to {expected}; pass the criteria the "
            f"verdicts were made with or re-run fulltext-eligibility"
        )
    return expected


def extract_works(
    works: Iterable[Work],
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    *,
    client: OpenAI,
    endpoint: LlmEndpoint,
    max_records: int | None,
    ctx: CodebookContext,
    skip_ids: set[str] | None = None,
    concurrency: int = 1,
) -> Iterator[ExtractionRecordProto]:
    skip_ids = skip_ids or set()
    eligible: list[Work] = []
    for work in works:
        if work.id in skip_ids:
            continue
        # Full-text-only: a work without a current extraction never enters
        # the loop — no record row, no count. The gap is visible in the
        # corpus provenance and the stage 6 meta, so we don't flag it here.
        if work.id not in extractions:
            continue
        if max_records is not None and len(eligible) >= max_records:
            break
        eligible.append(work)

    def _call_one(work: Work) -> ExtractionRecordProto:
        return _extract_one(
            work, extractions, extraction_output_dir, client, endpoint, ctx
        )

    yield from map_concurrent(_call_one, eligible, max_workers=concurrency)


def _extract_one(
    work: Work,
    extractions: dict[str, ExtractedDocument],
    extraction_output_dir: Path,
    client: OpenAI,
    endpoint: LlmEndpoint,
    ctx: CodebookContext,
) -> ExtractionRecordProto:
    # Full text or nothing: the eligible set is already gated on extraction
    # presence, so ``extractions[work.id]`` is always set here.
    extracted = extractions[work.id]
    try:
        tei = extracted.open_tei(extraction_output_dir)
        rendered = render_fulltext(tei)
    except etree.XMLSyntaxError:
        logger.warning(
            "Malformed TEI for %s; recording tei-parse-failure", work.id
        )
        return _sentinel_record(
            work.id,
            reason="tei-parse-failure",
            ctx=ctx,
        )
    if not rendered:
        # Valid XML but empty body: broken extraction on a paper we do
        # have a PDF for — worth surfacing, like malformed TEI.
        logger.warning(
            "Empty TEI body for %s; recording tei-parse-failure", work.id
        )
        return _sentinel_record(
            work.id,
            reason="tei-parse-failure",
            ctx=ctx,
        )
    return extract_codebook(
        work.id,
        rendered,
        client=client,
        endpoint=endpoint,
        ctx=ctx,
    )


def build_subparser(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "extraction-codebook",
        help="Extract codebook records for eligible works with an LLM.",
    )
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        default=None,
        help=(
            "Corpus written by corpus-assemble; every eligibility verdict must "
            "name one of its works (default: <data-dir>/corpus/)."
        ),
    )
    parser.add_argument(
        "--eligibility-verdicts",
        type=Path,
        default=None,
        help=(
            "Eligibility verdicts JSONL, with eligibility-meta.json beside it "
            "(default: <data-dir>/fulltext-eligibility/<run-id>/verdicts.jsonl)."
        ),
    )
    parser.add_argument(
        "--eligibility-criteria",
        default=str(DEFAULT_ELIGIBILITY_CRITERIA),
        help=(
            "Eligibility-criteria YAML the verdicts were made with; its hash must "
            "match prompt_sha256 in eligibility-meta.json. On reload from a saved "
            f"snapshot, the inlined mapping is consumed directly (default: "
            f"{DEFAULT_ELIGIBILITY_CRITERIA})."
        ),
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
            "Run identifier, shared with the eligibility run whose verdicts are "
            "read (default: analysis_run_id in <corpus-dir>/corpus-meta.json)."
        ),
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
    add_endpoint_args(parser, decisions=False)
    parser.add_argument(
        "--reasoning-effort",
        choices=REASONING_EFFORTS,
        default=None,
        help="Reasoning effort sent with each request (default: none sent, the provider's default).",
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
    data_dir: Path = Path(args.data_dir)
    args.corpus_dir = (
        Path(args.corpus_dir) if args.corpus_dir is not None else corpus_dir(data_dir)
    )
    corpus_meta = load_corpus_meta(args.corpus_dir)
    args.run_id = resolve_analysis_run_id(args.corpus_dir, args.run_id)
    args.eligibility_verdicts = require_input(
        args.eligibility_verdicts
        if args.eligibility_verdicts is not None
        else eligibility_verdicts_path(data_dir, args.run_id),
        flag="--eligibility-verdicts",
        upstream="fulltext-eligibility",
    )
    args.extractions = require_input(
        args.extractions if args.extractions is not None else extractions_path(data_dir),
        flag="--extractions",
        upstream="fulltext-extraction",
    )
    eligibility_prompt_sha256 = _check_eligibility_meta(
        args.eligibility_verdicts, corpus_meta.corpus_id, args.eligibility_criteria
    )
    endpoint = resolve_endpoint(args)

    output_dir: Path = data_dir / EXTRACTION_CODEBOOK_SUBDIR / args.run_id
    records_path = output_dir / "records.jsonl"
    meta_path = output_dir / "extraction-codebook-meta.json"

    extraction_output_dir: Path = (
        Path(args.extraction_output_dir)
        if args.extraction_output_dir is not None
        else Path(args.extractions).parent
    )

    ctx = CodebookContext.from_spec(resolve_yaml_arg(args.codebook))

    prompt_sha256 = hashlib.sha256(
        (ctx.system_prompt + "\n" + USER_TEMPLATE).encode("utf-8")
    ).hexdigest()

    # Resume guards: refuse --skip-existing when the recorded prompt hash,
    # eligibility prompt hash or corpus differs, so records.jsonl never
    # mixes any of them.
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
        recorded_eligibility_sha = recorded.get("eligibility_prompt_sha256")
        if (
            recorded_eligibility_sha is not None
            and recorded_eligibility_sha != eligibility_prompt_sha256
        ):
            raise SystemExit(
                f"recorded eligibility_prompt_sha256 in {meta_path} differs from "
                f"the eligibility verdicts read now; refusing --skip-existing to "
                f"avoid mixing eligibility prompt versions in {records_path}"
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

    # Full-text-only: gate on a current extraction so eligible works without
    # a PDF never enter the extracted set, get no record row, and are not
    # counted.
    eligible_works = [
        work
        for work in _active_eligible_works(
            read_corpus_works(args.corpus_dir), args.eligibility_verdicts, stats
        )
        if work.id in extractions
    ]
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

    print(f"Extracting {total} works with model {endpoint.model}", file=sys.stderr)
    if not args.dry_run:
        print(f"Output dir: {output_dir}", file=sys.stderr)

    if not args.dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)
        save_resolved_config(
            args, output_dir, inlines=["codebook", "eligibility_criteria"]
        )

    # Start-of-run truncation: non-resuming runs rewrite records.jsonl.
    if not args.dry_run and not args.skip_existing and records_path.exists():
        records_path.unlink()

    client = chat_client(endpoint, _LLM_TIMEOUT_SECONDS)

    t0 = time.monotonic()
    index = 0
    new_records: list[ExtractionRecordProto] = []

    for record in extract_works(
        eligible_works,
        extractions,
        extraction_output_dir,
        client=client,
        endpoint=endpoint,
        max_records=args.max_records,
        ctx=ctx,
        skip_ids=skip_ids,
        concurrency=args.concurrency,
    ):
        index += 1
        new_records.append(record)
        if not args.dry_run:
            append_jsonl(cast(BaseModel, record), records_path)

        if record.reason is None:
            print(
                f"  [{index}/{total}] extracted"
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
        1 for r in all_records if r.reason is None
    )
    skipped_count = sum(
        1 for r in all_records if r.reason == "tei-parse-failure"
    )
    llm_parse_failure_count = sum(
        1 for r in all_records if r.reason == "llm-parse-failure"
    )
    llm_timeout_count = sum(1 for r in all_records if r.reason == "llm-timeout")
    llm_rejected_count = sum(1 for r in all_records if r.reason == "llm-rejected")

    print(
        f"\nDone in {elapsed:.1f}s: {full_text_count} extracted, "
        f"{skipped_count} skipped, "
        f"{llm_parse_failure_count} llm-parse-failure, "
        f"{llm_timeout_count} llm-timeout, "
        f"{llm_rejected_count} llm-rejected.",
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
        ExtractionCodebookMeta(
            run=run_meta,
            llm=llm_meta,
            corpus_id=corpus_meta.corpus_id,
            eligibility_prompt_sha256=eligibility_prompt_sha256,
            input_eligibility_verdicts=str(args.eligibility_verdicts),
            input_extractions=str(args.extractions),
            input_count=total,
            full_text_count=full_text_count,
            skipped_count=skipped_count,
            llm_parse_failure_count=llm_parse_failure_count,
            llm_timeout_count=llm_timeout_count,
            llm_rejected_count=llm_rejected_count,
        ),
    )
    print(f"Run dir: {output_dir}", file=sys.stderr)
