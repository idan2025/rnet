import asyncio

import pytest

from rnet.core.sendqueue import PrioritySendQueue
from rnet.protocol import PRIORITY_CONTROL, PRIORITY_NORMAL, PRIORITY_BULK


@pytest.mark.asyncio
async def test_priority_order():
    q = PrioritySendQueue()
    # Insert bulk first, then normal, then control.
    await q.put("bulk1", priority=PRIORITY_BULK)
    await q.put("normal1", priority=PRIORITY_NORMAL)
    await q.put("control1", priority=PRIORITY_CONTROL)
    order = []
    for _ in range(3):
        order.append(await q.get())
    # Control dequeues first despite being inserted last.
    assert order == ["control1", "normal1", "bulk1"]


@pytest.mark.asyncio
async def test_fifo_within_same_priority():
    q = PrioritySendQueue()
    await q.put("a", priority=PRIORITY_NORMAL)
    await q.put("b", priority=PRIORITY_NORMAL)
    await q.put("c", priority=PRIORITY_NORMAL)
    assert [await q.get() for _ in range(3)] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_get_waits_for_item():
    q = PrioritySendQueue()

    async def producer():
        await asyncio.sleep(0.01)
        await q.put("late", priority=PRIORITY_CONTROL)

    asyncio.ensure_future(producer())
    item = await asyncio.wait_for(q.get(), timeout=1.0)
    assert item == "late"


@pytest.mark.asyncio
async def test_close_unblocks():
    q = PrioritySendQueue()
    q.close()
    with pytest.raises(asyncio.QueueEmpty):
        await asyncio.wait_for(q.get(), timeout=0.5)