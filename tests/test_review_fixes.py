"""Regression tests for the findings of the final whole-branch review."""
import asyncio
import time

import httpx
import pytest

from terminal.brain.alerts import AlertEngine
from terminal.brain.conflicts import heat
from terminal.bus import Bus
from terminal.events import FeedStatus, Tick, WhaleEvent
from terminal.feeds import finnhub, gdelt, sec
from terminal.feeds.base import run_feed, scrub
from terminal.feeds.crypto import Throttle
from terminal.feeds.rss import clean
from terminal.prices import Prices
from terminal.settings import DEFAULTS, validate


# I1 — conflict heat must not compare against days that have no data
def test_heat_needs_three_days_of_history():
    assert heat({"100": 100, "101": 29}, 101) == (29, 0.0)
    assert heat({"98": 10, "99": 10, "100": 10, "101": 25}, 101) == (25, 2.5)   # mean over days that have data


# I2 — Finnhub ticks are throttled like crypto ticks
def test_finnhub_ticks_are_throttled_per_symbol():
    bus = Bus()
    sub = bus.subscribe()
    throttle = Throttle(0.25)
    burst = [Tick("AAPL", 100 + i, 1, 1.0, "finnhub") for i in range(50)] + [Tick("MSFT", 5, 1, 1.0, "finnhub")]
    finnhub.publish_ticks(bus, burst, throttle, now=10.0)
    finnhub.publish_ticks(bus, burst, throttle, now=10.1)
    finnhub.publish_ticks(bus, burst, throttle, now=10.3)
    assert sub.queue.qsize() == 4     # AAPL and MSFT at 10.0 and again at 10.3


# I3 — cleaning hostile text must not stall the event loop
def test_clean_is_fast_on_hostile_input():
    started = time.perf_counter()
    assert clean("<" * 200_000) == "" or len(clean("<" * 200_000)) <= 300
    clean("<script>" * 50_000)
    assert time.perf_counter() - started < 0.5


# I5 — one bad filing download does not take the SEC feed down
async def test_fetch_filing_swallows_download_errors_and_oversize():
    filing = {"url": "https://www.sec.gov/Archives/edgar/data/1/0001-25-000001-index.htm", "accession": "a"}

    def handler(request):
        if "slow" in request.headers.get("x-mode", ""):
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, content=b"x" * 100)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await sec.fetch_filing(client, filing) == b"x" * 100
        assert await sec.fetch_filing(client, filing, max_bytes=10) is None
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"x-mode": "slow"}) as client:
        assert await sec.fetch_filing(client, filing) is None


# I6 — "4" must not match 424B2 and friends
def test_form_filter_is_exact_plus_amendments():
    assert sec.is_form("4", "4") and sec.is_form("4/A", "4") and sec.is_form("13F-HR/A", "13F-HR")
    for other in ("424B2", "425", "40-F", "497", "13F-NT", "4X"):
        assert not sec.is_form(other, "4")


# I8 — GDELT answers errors as HTTP 200 text; that is a failure, not "ok"
async def test_gdelt_text_error_counts_as_failure():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text="Your query was too short or too long."))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ConnectionError):
            await gdelt.poll(Bus(), [{"name": "Z", "query": "z", "assets": []}], client=client, pause=0)


async def test_gdelt_good_answer_publishes():
    body = {"articles": [{"url": "https://e.com/1", "title": "Missile strike hits port", "seendate": "20251109T200000Z"}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    bus = Bus()
    sub = bus.subscribe()
    async with httpx.AsyncClient(transport=transport) as client:
        task = asyncio.create_task(gdelt.poll(bus, [{"name": "Z", "query": "z", "assets": []}], client=client, pause=0))
        event = await asyncio.wait_for(anext(sub), 2)
        task.cancel()
    assert (event.zone, event.type) == ("Z", "strike")


# M2 — API keys never reach status text
def test_scrub_removes_keys_from_error_text():
    text = scrub("Client error '401' for url 'https://finnhub.io/api/v1/news?category=general&token=SECRET123'")
    assert "SECRET123" not in text and "token=***" in text
    assert "K9" not in scrub("wss://ws.finnhub.io?token=K9 failed; https://api.etherscan.io/v2/api?apikey=K9&x=1")


async def test_feed_status_detail_is_scrubbed():
    bus = Bus()
    sub = bus.subscribe()

    async def work():
        raise RuntimeError("boom https://x.io/?apikey=TOPSECRET")

    async def stop(delay):
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_feed("x", bus, work, sleep=stop)
    assert "TOPSECRET" not in sub.queue.get_nowait().detail


def test_finnhub_news_sends_key_as_header():
    params, headers = finnhub.news_request("KEY")
    assert "KEY" not in str(params) and headers == {"X-Finnhub-Token": "KEY"}


# M3 — a feed whose work() fails before it even returns a coroutine is reported and retried
async def test_run_feed_survives_synchronous_failure():
    bus = Bus()
    sub = bus.subscribe()
    calls = 0

    def work():
        nonlocal calls
        calls += 1
        raise ValueError("bad toml")

    async def sleep(delay):
        if calls >= 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await run_feed("x", bus, work, sleep=sleep)
    assert calls == 2 and sub.queue.get_nowait().state == "down"


# M4 — a jump window longer than the price history kept would silently disable jump alerts
def test_jump_window_is_bounded():
    assert validate({"price_jump_window_s": 900}) == {"price_jump_window_s": 900}
    for bad in (5, 901, 3600):
        with pytest.raises(ValueError):
            validate({"price_jump_window_s": bad})


# Review "declined to judge" rulings
def engine(now=1_000_000.0):
    return AlertEngine(lambda: DEFAULTS, Prices(), lambda: {"BTC-USD"}, lambda s: "crypto", now=lambda: now)


def test_whales_off_the_watchlist_are_logged_quietly_unless_huge():
    def sev(symbol, usd, kind="onchain"):
        return [a.severity for a in engine().on_event(WhaleEvent("w", kind, symbol, usd, "", "d", "", 1.0))]
    assert sev("BTC-USD", 20_000_000) == ["warn"]
    assert sev("USDT-USD", 20_000_000) == ["info"]          # stablecoins move $10M all day long
    assert sev("USDT-USD", 100_000_000) == ["critical"]
    assert sev("SOL-USD", 2_000_000, "crypto_trade") == ["info"]


def test_old_news_does_not_alert():
    def story(age):
        return {"id": f"n{age}", "title": "T", "ts": 1_000_000.0 - age,
                "analysis": {"weight": 5, "sentiment": -1, "impacts": [{"symbol": "BTC-USD", "direction": -1}]}}
    assert len(engine().on_news(story(60))) == 1
    assert engine().on_news(story(2 * 3600)) == []          # first start replays hours of feed history
