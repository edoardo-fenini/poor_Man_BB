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


def names(feeds):
    return {f.args[0] for f in feeds[1:]}


async def test_key_present_adds_the_stock_stream():
    out, with_key = await statuses(Config("k", "", ""))
    _, without = await statuses(Config("", "", ""))
    assert "stocks" not in out
    assert names(with_key) - names(without) == {"stocks", "finnhub-news"}
    assert {"crypto", "quotes", "rss"} <= names(without)


async def test_missing_sec_contact_disables_sec_feeds():
    out, _ = await statuses(Config("", "", ""))
    assert out["sec-8k"].state == "disabled" and "SEC_USER_AGENT" in out["sec-8k"].detail
    assert out["finnhub-news"].state == "disabled"
    out, _ = await statuses(Config("k", "", "Jane Doe jane@example.com"))
    assert "sec-8k" not in out and "finnhub-news" not in out


async def test_onchain_feeds_follow_the_etherscan_key():
    out, without = await statuses(Config("", "", ""))
    assert out["eth-onchain"].state == "disabled" and "ETHERSCAN_KEY" in out["eth-onchain"].detail
    assert "btc-onchain" in names(without) and "eth-onchain" not in names(without)
    _, with_key = await statuses(Config("", "e", ""))
    assert "eth-onchain" in names(with_key)


async def test_stock_whale_feeds():
    out, without = await statuses(Config("", "", ""))
    assert out["sec-filings"].state == "disabled" and "volume" in names(without)
    _, with_contact = await statuses(Config("", "", "Jane Doe jane@example.com"))
    assert "sec-filings" in names(with_contact)


async def test_ships_need_a_key_and_a_fleet():
    from terminal.feeds.ships import Fleet
    out, without = await statuses(Config("", "", ""))
    assert out["ships"].state == "disabled" and "AISSTREAM_KEY" in out["ships"].detail
    bus = Bus()
    with_key = build_feeds(Config("", "", "", aisstream_key="k"), Store(":memory:"), bus, Prices(), Fleet())
    assert names(with_key) - names(without) == {"ships"}
