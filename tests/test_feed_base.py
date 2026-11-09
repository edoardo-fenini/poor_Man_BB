import asyncio

import pytest

from terminal.bus import Bus
from terminal.feeds.base import SKIPPED, run_feed, safe


async def test_run_feed_reports_down_and_backs_off():
    bus = Bus()
    sub = bus.subscribe()
    delays = []
    calls = 0

    async def work():
        nonlocal calls
        calls += 1
        if calls <= 3:
            raise RuntimeError("boom")
        raise asyncio.CancelledError

    async def fake_sleep(d):
        delays.append(d)

    with pytest.raises(asyncio.CancelledError):
        await run_feed("x", bus, work, base_delay=1, max_delay=3, sleep=fake_sleep)
    assert delays == [1, 2, 3]
    states = [sub.queue.get_nowait().state for _ in range(sub.queue.qsize())]
    assert states == ["down"] * 3  # never claims ok for work that dies at once


async def test_run_feed_restarts_work_that_returns():
    bus = Bus()
    calls = 0

    async def work():
        nonlocal calls
        calls += 1
        if calls == 3:
            raise asyncio.CancelledError

    async def fake_sleep(d):
        pass

    with pytest.raises(asyncio.CancelledError):
        await run_feed("x", bus, work, sleep=fake_sleep)
    assert calls == 3


async def test_run_feed_reports_ok_once_work_survives():
    bus = Bus()
    sub = bus.subscribe()

    async def work():
        await asyncio.sleep(3600)

    task = asyncio.create_task(run_feed("x", bus, work, grace=0.01))
    status = await asyncio.wait_for(anext(sub), 1)
    assert (status.feed, status.state) == ("x", "ok")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_safe_swallows_and_counts():
    SKIPPED.pop("f", None)
    assert safe(lambda r: 1 / 0, {}, "f") == [] and SKIPPED["f"] == 1
    assert safe(lambda r: [r], 5, "f") == [5]
