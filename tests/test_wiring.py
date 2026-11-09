import asyncio

from terminal.__main__ import build_feeds
from terminal.bus import Bus
from terminal.config import Config
from terminal.prices import Prices
from terminal.store import Store


async def statuses(config):
    bus = Bus()
    sub = bus.subscribe()
    feeds = build_feeds(config, Store(":memory:"), bus, Prices())
    await feeds[0]()  # the announcer
    return {s.feed: s for s in [sub.queue.get_nowait() for _ in range(sub.queue.qsize())]}, feeds


async def test_missing_finnhub_key_is_announced_as_disabled():
    out, _ = await statuses(Config("", "", ""))
    assert out["stocks"].state == "disabled" and "FINNHUB_KEY" in out["stocks"].detail


async def test_key_present_adds_the_stock_stream():
    out, with_key = await statuses(Config("k", "", ""))
    _, without = await statuses(Config("", "", ""))
    assert "stocks" not in out and len(with_key) == len(without) + 1
