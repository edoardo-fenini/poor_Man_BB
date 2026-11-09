"""Finnhub (free key): live US stock trades, and market news."""
import json
import re
from collections.abc import Callable

import websockets

from terminal.bus import Bus
from terminal.events import Tick
from terminal.feeds.base import safe
from terminal.symbols import Symbol

LIVE_LIMIT = 50  # free tier subscription cap
_FOREIGN = re.compile(r"\.[A-Z]{2,}$")  # exchange suffix such as .MI or .L


def parse_trades(msg: dict) -> list[Tick]:
    if msg["type"] != "trade":
        return []
    return [Tick(d["s"], float(d["p"]), float(d["v"]), d["t"] / 1000, "finnhub")
            for d in msg["data"] if float(d["p"]) > 0]


def split_live(symbols: list[Symbol]) -> tuple[list[Symbol], list[Symbol]]:
    """(streamed live, left for polling). US stocks and ETFs go live, in watchlist order."""
    live, polled = [], []
    for s in symbols:
        us_stock = s.asset == "stock" and not _FOREIGN.search(s.id)
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
