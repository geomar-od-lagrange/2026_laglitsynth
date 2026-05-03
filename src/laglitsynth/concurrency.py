"""Shared concurrency helper for LLM-driven pipeline stages."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


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

    Exceptions raised by *fn* are **not** swallowed; they propagate to the
    caller on the ``yield`` that would have returned the failed result.
    """
    if max_workers == 1:
        for item in items:
            yield fn(item)
        return

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures: list[Future[R]] = [pool.submit(fn, item) for item in items]
        for fut in as_completed(futures):
            yield fut.result()
