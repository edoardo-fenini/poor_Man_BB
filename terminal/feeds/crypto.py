"""Live crypto trades. Binance first; Coinbase, then Kraken when it is unreachable."""
import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

import websockets

from terminal.bus import Bus
from terminal.events import Tick
from terminal.feeds.base import safe
from terminal.symbols import Symbol

FIRST_MESSAGE_TIMEOUT = 20.0
IDLE_TIMEOUT = 90.0


@dataclass(frozen=True)
class Trade:
    symbol: str
    price: float
    size: float
    side: str  # taker side: buy | sell
    ts: float
    source: str


def _trade(symbol: str, price, size, side: str, ts: float, source: str) -> list[Trade]:
    price, size = float(price), float(size)
    if not (price > 0 and size > 0) or side not in ("buy", "sell"):
        return []
    return [Trade(symbol, price, size, side, ts, source)]


def _iso(text: str) -> float:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()


def parse_binance(msg: dict) -> list[Trade]:
    d = msg["data"]
    if d["e"] != "aggTrade":
        return []
    pair = d["s"]
    base = pair.removesuffix("USDT")
    # "m": the buyer was the maker, so the taker sold
    return _trade(f"{base}-USD", d["p"], d["q"], "sell" if d["m"] else "buy", d["T"] / 1000, "binance")


def parse_coinbase(msg: dict) -> list[Trade]:
    if msg["type"] not in ("match", "last_match"):
        return []
    taker = "sell" if msg["side"] == "buy" else "buy"  # "side" is the maker's
    return _trade(msg["product_id"], msg["price"], msg["size"], taker, _iso(msg["time"]), "coinbase")


def parse_kraken(msg: dict) -> list[Trade]:
    if msg["channel"] != "trade":
        return []
    out = []
    for d in msg["data"]:
        base = d["symbol"].split("/")[0]
        base = "BTC" if base == "XBT" else base
        out += _trade(f"{base}-USD", d["price"], d["qty"], d["side"], _iso(d["timestamp"]), "kraken")
    return out


class Throttle:
    """At most one tick per symbol per interval; the page does not need every trade."""

    def __init__(self, interval: float):
        self.interval, self._last = interval, {}

    def allow(self, symbol: str, now: float) -> bool:
        if now - self._last.get(symbol, 0.0) < self.interval:
            return False
        self._last[symbol] = now
        return True


def _providers(symbols: list[Symbol]):
    streams = "/".join(f"{s.binance.lower()}@aggTrade" for s in symbols)
    return [
        ("binance", f"wss://stream.binance.com:9443/stream?streams={streams}", None, parse_binance),
        ("coinbase", "wss://ws-feed.exchange.coinbase.com",
         {"type": "subscribe", "product_ids": [s.coinbase for s in symbols], "channels": ["matches"]}, parse_coinbase),
        ("kraken", "wss://ws.kraken.com/v2",
         {"method": "subscribe", "params": {"channel": "trade", "symbol": [s.kraken for s in symbols], "snapshot": False}},
         parse_kraken),
    ]


async def _consume(name, url, subscribe, parse, bus, wanted, symbols, on_trade) -> None:
    throttle = Throttle(0.25)
    async with websockets.connect(url, open_timeout=10, ping_interval=20, max_size=2**20) as ws:
        if subscribe:
            await ws.send(json.dumps(subscribe))
        got_trade = False
        while True:
            raw = await asyncio.wait_for(ws.recv(), IDLE_TIMEOUT if got_trade else FIRST_MESSAGE_TIMEOUT)
            for trade in safe(lambda r: parse(json.loads(r)), raw, "crypto"):
                got_trade = True
                on_trade(trade)
                if throttle.allow(trade.symbol, time.monotonic()):
                    bus.publish(Tick(trade.symbol, trade.price, trade.size, trade.ts, name))
            if [s.id for s in symbols()] != wanted:
                return  # watchlist changed: reconnect with the new symbols


async def stream(bus: Bus, symbols: Callable[[], list[Symbol]],
                 on_trade: Callable[[Trade], None] = lambda t: None) -> None:
    current = symbols()
    if not current:
        await asyncio.sleep(5)
        return
    wanted = [s.id for s in current]
    errors = []
    for name, url, subscribe, parse in _providers(current):
        try:
            return await _consume(name, url, subscribe, parse, bus, wanted, symbols, on_trade)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__} {exc}"[:80])
    raise ConnectionError("; ".join(errors))
