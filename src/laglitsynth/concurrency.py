"""Shared concurrency helpers for the stages that send one request per work.

Stages 3, 7 and 8 use :func:`map_concurrent` for their LLM requests and
stage 6 for its GROBID requests; each registers ``--concurrency`` with
:func:`positive_int` as its argparse type.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def positive_int(value: str) -> int:
    """Argparse type for ``--concurrency``: an integer of at least 1."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not an integer") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"must be at least 1, got {number}")
    return number


def map_concurrent(
    fn: Callable[[T], R],
    items: Iterable[T],
    *,
    max_workers: int,
) -> Iterator[R]:
    """Apply *fn* to each item in *items* via ``ThreadPoolExecutor``, yielding
    results in completion order.

    When ``max_workers=1`` the function falls back to sequential execution —
    no thread pool is created, so tracebacks are clean and there is no
    threading overhead.

    Items are submitted lazily: at most ``2 * max_workers`` are in the pool
    at a time (one running and one queued per worker), and a new one is
    submitted only when a result comes back. A consumer that stops
    iterating therefore stops new requests. When the generator is closed or
    an exception passes through it (including ``KeyboardInterrupt`` and an
    error raised by *fn*), the queued items are cancelled and only the
    running ones finish.

    Exceptions raised by *fn* are **not** swallowed; they propagate to the
    caller on the ``yield`` that would have returned the failed result.
    """
    if max_workers == 1:
        for item in items:
            yield fn(item)
        return

    window = 2 * max_workers
    pool = ThreadPoolExecutor(max_workers=max_workers)
    try:
        remaining = iter(items)
        pending: set[Future[R]] = set()
        for item in remaining:
            pending.add(pool.submit(fn, item))
            if len(pending) == window:
                break
        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            # Refill before yielding so the workers stay busy while the
            # caller handles the results.
            for item in remaining:
                pending.add(pool.submit(fn, item))
                if len(pending) == window:
                    break
            for fut in done:
                yield fut.result()
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
