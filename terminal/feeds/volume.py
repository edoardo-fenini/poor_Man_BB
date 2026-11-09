"""Unusual stock volume: today's cumulative volume against the usual for this point in the session.
The free stand-in for paid block-trade and options-flow data."""
import asyncio
import csv
import time
from collections.abc import Callable
from pathlib import Path

from terminal.bus import Bus
from terminal.events import Candle, WhaleEvent
from terminal.feeds import yahoo
from terminal.feeds.finnhub import FOREIGN
from terminal.symbols import Symbol, resolve

ENTITIES_FILE = Path(__file__).parent.parent / "data" / "entities.csv"
SLOT = 300  # seconds; 5-minute bars
DAY = 86400


def sessions(candles: list[Candle]) -> list[list[Candle]]:
    """Bars grouped by UTC day, oldest first."""
    days: dict[int, list[Candle]] = {}
    for candle in candles:
        days.setdefault(int(candle.ts // DAY), []).append(candle)
    return [days[day] for day in sorted(days)]


def _cumulative(session: list[Candle]) -> list[float]:
    """Running volume per slot since the session's first bar; missing bars carry the total forward."""
    out: list[float] = []
    total = 0.0
    for candle in session:
        slot = int((candle.ts - session[0].ts) // SLOT)
        out.extend([total] * (slot - len(out)))
        total += candle.volume
        out.append(total)
    return out


def profile(history: dict[str, list[Candle]]) -> dict[str, list[float]]:
    """Per symbol: the mean cumulative volume at each slot over the sessions given (up to the last 20)."""
    out = {}
    for symbol, candles in history.items():
        curves = [_cumulative(s) for s in sessions(candles)[-20:]]
        if not curves:
            continue
        length = max(len(c) for c in curves)
        padded = [c + [c[-1]] * (length - len(c)) for c in curves]  # a short day ends at its final total
        out[symbol] = [sum(column) / len(padded) for column in zip(*padded)]
    return out


def unusual(symbol: str, today: list[Candle], prof: list[float], ratio: float) -> WhaleEvent | None:
    curve = _cumulative(today) if today else []
    if len(curve) < 3 or not prof:
        return None
    usual = prof[min(len(curve), len(prof)) - 1]
    if usual <= 0 or curve[-1] / usual < ratio:
        return None
    return WhaleEvent(
        id=f"vol:{symbol}:{int(today[0].ts // DAY)}", type="volume", symbol=symbol,
        usd=sum(c.close * c.volume for c in today), side="",
        detail=f"{curve[-1] / usual:.1f}× normal volume", url="", ts=today[-1].ts)


def universe(watchlist: list[Symbol]) -> list[Symbol]:
    """US stocks worth scanning: the watchlist plus every stock in the entity list."""
    with open(ENTITIES_FILE, newline="") as fh:
        listed = [resolve(row["symbol"]) for row in csv.DictReader(fh)]
    unique = {s.id: s for s in watchlist + [s for s in listed if s]}
    return [s for s in unique.values() if s.asset == "stock" and not FOREIGN.search(s.id)]


async def poll(bus: Bus, symbols: Callable[[], list[Symbol]], settings: Callable[[], dict],
               every: float = 120.0) -> None:
    profiles: dict[str, list[float]] = {}
    built_on = None
    while True:
        scan = universe(symbols())
        today = int(time.time() // DAY)
        if built_on != today or any(s.id not in profiles for s in scan):
            history = await yahoo.bars(scan, "1mo", "5m")
            # the profile is the past: leave out today's partial session
            profiles = profile({k: [c for c in v if int(c.ts // DAY) < today] for k, v in history.items()})
            built_on = today
            for s in scan:
                profiles.setdefault(s.id, [])
        for symbol, candles in (await yahoo.bars(scan, "1d", "5m")).items():
            current = [c for c in candles if int(c.ts // DAY) == today]
            if whale := unusual(symbol, current, profiles.get(symbol, []), settings()["volume_ratio"]):
                bus.publish(whale)
        await asyncio.sleep(every)
