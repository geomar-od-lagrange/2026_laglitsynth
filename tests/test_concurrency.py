"""Tests for the shared concurrency helpers."""

from __future__ import annotations

import argparse
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import patch

import pytest

from laglitsynth.concurrency import map_concurrent, positive_int


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


class _Recorder:
    """Thread-safe ``fn`` that records which items were started."""

    def __init__(self, delay: float = 0.0) -> None:
        self.delay = delay
        self.started: list[int] = []
        self._lock = threading.Lock()

    def __call__(self, x: int) -> int:
        with self._lock:
            self.started.append(x)
        time.sleep(self.delay)
        return x


def test_map_concurrent_consumer_exception_leaves_rest_unprocessed() -> None:
    """A consumer raising after the first result stops the remaining items."""
    fn = _Recorder(delay=0.01)
    workers = 2

    with pytest.raises(RuntimeError, match="consumer"):
        for _ in map_concurrent(fn, range(100), max_workers=workers):
            raise RuntimeError("consumer")

    time.sleep(0.1)  # let any stray worker pick up queued items
    assert len(fn.started) <= 3 * workers


def test_map_concurrent_close_cancels_queued_items() -> None:
    """Closing the generator (GeneratorExit) cancels what is still queued."""
    fn = _Recorder(delay=0.01)
    workers = 2
    gen = map_concurrent(fn, range(100), max_workers=workers)

    next(gen)
    gen.close()

    time.sleep(0.1)
    assert len(fn.started) <= 3 * workers


def test_map_concurrent_stalled_consumer_bounds_submissions() -> None:
    """A consumer that stops iterating stops new submissions."""
    fn = _Recorder()
    workers = 2
    gen = map_concurrent(fn, range(100), max_workers=workers)

    next(gen)
    time.sleep(0.1)  # the consumer stalls; nothing new may be submitted

    assert len(fn.started) <= 4 * workers
    gen.close()


def test_map_concurrent_threaded_processes_every_item_once() -> None:
    """The submission window still covers every item exactly once."""
    fn = _Recorder()
    results = list(map_concurrent(fn, range(50), max_workers=3))
    assert sorted(results) == list(range(50))
    assert sorted(fn.started) == list(range(50))


def test_positive_int_accepts_one_and_up() -> None:
    assert positive_int("1") == 1
    assert positive_int("8") == 8


@pytest.mark.parametrize("value", ["0", "-3", "two", "1.5"])
def test_positive_int_rejects(value: str) -> None:
    with pytest.raises(argparse.ArgumentTypeError):
        positive_int(value)


@pytest.mark.parametrize(
    "command",
    [
        "screening-abstracts",
        "fulltext-extraction",
        "fulltext-eligibility",
        "extraction-codebook",
    ],
)
@pytest.mark.parametrize("value", ["0", "-1"])
def test_stage_concurrency_rejects_non_positive(
    command: str, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Stages 3, 6, 7 and 8 reject --concurrency below 1 as a usage error."""
    from laglitsynth.cli import main

    with pytest.raises(SystemExit) as exc_info:
        main([command, "--concurrency", value])
    assert exc_info.value.code == 2
    assert "--concurrency" in capsys.readouterr().err
