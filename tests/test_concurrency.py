"""Tests for the shared map_concurrent helper."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch

import pytest

from laglitsynth.concurrency import map_concurrent


def test_map_concurrent_preserves_order() -> None:
    """Results from max_workers=1 come out in input order."""
    results = list(map_concurrent(lambda x: x * 2, range(5), max_workers=1))
    assert results == [0, 2, 4, 6, 8]


def test_map_concurrent_propagates_exceptions() -> None:
    """An exception raised by fn propagates to the caller."""

    def boom(x: int) -> int:
        if x == 2:
            raise ValueError("bang")
        return x

    items = list(range(3))

    with pytest.raises(ValueError, match="bang"):
        list(map_concurrent(boom, items, max_workers=1))


def test_map_concurrent_propagates_exceptions_threaded() -> None:
    """Exceptions also propagate when max_workers > 1."""

    def boom(x: int) -> int:
        if x == 1:
            raise RuntimeError("threaded bang")
        return x

    items = list(range(3))

    with pytest.raises(RuntimeError, match="threaded bang"):
        list(map_concurrent(boom, items, max_workers=2))


def test_map_concurrent_max_workers_one_runs_sequentially() -> None:
    """max_workers=1 does not create a ThreadPoolExecutor."""
    calls: list[int] = []

    def record(x: int) -> int:
        calls.append(x)
        return x

    with patch(
        "laglitsynth.concurrency.ThreadPoolExecutor",
        wraps=ThreadPoolExecutor,
    ) as mock_tpe:
        list(map_concurrent(record, range(3), max_workers=1))

    mock_tpe.assert_not_called()
    assert calls == [0, 1, 2]


def test_map_concurrent_max_workers_n_runs_in_parallel() -> None:
    """With max_workers>1 wall time is less than n × per-item cost."""
    delay = 0.05  # seconds per item
    n = 4

    def slow(x: int) -> int:
        time.sleep(delay)
        return x

    t0 = time.monotonic()
    results = list(map_concurrent(slow, list(range(n)), max_workers=n))
    elapsed = time.monotonic() - t0

    # All items processed
    assert set(results) == set(range(n))
    # Wall time substantially shorter than sequential (n × delay)
    assert elapsed < n * delay * 0.8


def test_map_concurrent_empty_input() -> None:
    """Empty input yields no results."""
    results = list(map_concurrent(lambda x: x, [], max_workers=2))
    assert results == []


def test_map_concurrent_single_item() -> None:
    """Single-item input works with any max_workers value."""
    for workers in (1, 2, 4):
        results = list(map_concurrent(lambda x: x + 10, [5], max_workers=workers))
        assert results == [15]
