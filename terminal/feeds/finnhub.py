"""Finnhub (free key): live US stock trades, and market news."""
import asyncio
import json
import re
from collections.abc import Callable

import httpx
import websockets

from terminal.bus import Bus
from terminal.events import NewsItem, Tick
from terminal.feeds.base import safe
from terminal.feeds.rss import clean, news_id
from terminal.symbols import Symbol

LIVE_LIMIT = 50  # free tier subscription cap
FOREIGN = re.compile(r"\.[A-Z]{2,}$")  # exchange suffix such as .MI or .L


def parse_trades(msg: dict) -> list[Tick]:
    if msg["type"] != "trade":
        return []
    return [Tick(d["s"], float(d["p"]), float(d["v"]), d["t"] / 1000, "finnhub")
            for d in msg["data"] if float(d["p"]) > 0]


def split_live(symbols: list[Symbol]) -> tuple[list[Symbol], list[Symbol]]:
    """(streamed live, left for polling). US stocks and ETFs go live, in watchlist order."""
    live, polled = [], []
    for s in symbols:
        us_stock = s.asset == "stock" and not FOREIGN.search(s.id)
        (live if us_stock and len(live) < LIVE_LIMIT else polled).append(s)
    return live, polled


async def stream(bus: Bus, key: str, symbols: Callable[[], list[Symbol]]) -> None:
    wanted = [s.finnhub for s in split_live(symbols())[0]]
    async with websockets.connect(f"wss://ws.finnhub.io?token={key}", open_timeout=10, ping_interval=20) as ws:
        for symbol in wanted:
            await ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))
        async for raw in ws:
            for tick in safe(lambda r: parse_trades(json.loads(r)), raw, "stocks"):
                bus.publish(tick)
            if [s.finnhub for s in split_live(symbols())[0]] != wanted:
                return  # watchlist changed: reconnect with the new symbols


def parse_news(items: list[dict]) -> list[NewsItem]:
    out = []
    for d in items:
        title, url = clean(d.get("headline")), d.get("url") or ""
        if title and url.startswith(("http://", "https://")) and d.get("datetime"):
            out.append(NewsItem(news_id(title), title, url, clean(d.get("source"), 40) or "Finnhub",
                                float(d["datetime"]), clean(d.get("summary"))))
    return out


async def poll_news(bus: Bus, key: str, every: float = 60.0) -> None:
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            response = await client.get("https://finnhub.io/api/v1/news", params={"category": "general", "token": key})
            response.raise_for_status()
            for item in safe(parse_news, response.json(), "finnhub-news"):
                if item.id not in seen:
                    seen.add(item.id)
                    bus.publish(item)
            await asyncio.sleep(every)
