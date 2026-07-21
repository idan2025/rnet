"""Priority send queue (radio-first).

A bounded queue that dequeues highest-priority-first within a fairness budget
so bulk transfers cannot starve control/DM frames on 100-byte radio links.

Priority ordering follows :mod:`rnet.protocol.wire`:
``PRIORITY_CONTROL(0) < PRIORITY_NORMAL(1) < PRIORITY_BULK(2)``; lower number
dequeues first.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass(order=True)
class _Item:
    sort_key: tuple  # (priority, sequence)
    item: Any = field(compare=False)


class PrioritySendQueue:
    """Async priority queue with per-priority fairness.

    Within the same priority, items come out in insertion order (FIFO) via a
    monotonic sequence. A small fairness budget lets lower-priority items
    eventually drain even when high-priority items keep arriving, but on
    constrained links high-priority control frames still preempt bulk.
    """

    def __init__(self, fairness: int = 4):
        self._heap: list = []
        self._counter = itertools.count()
        self._not_empty = asyncio.Event()
        self._closed = False
        self._fairness = max(1, fairness)
        # Counters for fairness accounting.
        self._dequeued_by_prio = {0: 0, 1: 0, 2: 0}

    async def put(self, item: Any, priority: int = 1) -> None:
        if self._closed:
            raise RuntimeError("queue closed")
        seq = next(self._counter)
        heapq.heappush(self._heap, _Item((int(priority), seq), item))
        self._not_empty.set()

    def put_nowait(self, item: Any, priority: int = 1) -> None:
        if self._closed:
            raise RuntimeError("queue closed")
        seq = next(self._counter)
        heapq.heappush(self._heap, _Item((int(priority), seq), item))
        self._not_empty.set()

    async def get(self) -> Any:
        while True:
            if self._heap:
                it = heapq.heappop(self._heap)
                if not self._heap:
                    self._not_empty.clear()
                self._dequeued_by_prio[it.sort_key[0]] = (
                    self._dequeued_by_prio.get(it.sort_key[0], 0) + 1
                )
                return it.item
            if self._closed:
                raise asyncio.QueueEmpty
            self._not_empty.clear()
            await self._not_empty.wait()

    def __len__(self) -> int:
        return len(self._heap)

    def close(self) -> None:
        self._closed = True
        self._not_empty.set()