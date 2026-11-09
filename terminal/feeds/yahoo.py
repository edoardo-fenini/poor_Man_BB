"""Yahoo Finance: chart history for everything, and polled quotes for what has no live stream."""
import asyncio
import math
import time
from collections.abc import Callable

from terminal.bus import Bus
from terminal.events import Candle, Tick
from terminal.feeds.base import safe
from terminal.symbols import Symbol

# ui interval -> (yahoo interval, range)
INTERVALS = {
    "1m": ("1m", "5d"), "5m": ("5m", "1mo"), "15m": ("15m", "1mo"), "1h": ("60m", "6mo"),
    "1d": ("1d", "5y"), "1wk": ("1wk", "max"), "1mo": ("1mo", "max"),
}
_FIELDS = ["Open", "High", "Low", "Close", "Volume"]


def frame_to_candles(df, symbol: str, interval: str) -> list[Candle]:
    if df is None or len(df) == 0:
        return []
    out = []
    for ts, row in zip(df.index, df[_FIELDS].itertuples(index=False, name=None)):
        o, h, l, c, v = row
        if any(math.isnan(x) for x in (o, h, l, c)):
            continue
        out.append(Candle(symbol, interval, ts.timestamp(), o, h, l, c, 0.0 if math.isnan(v) else float(v)))
    return out


def frame_to_ticks(df, ids: dict[str, str], now: float) -> list[Tick]:
    """Last valid bar per Yahoo symbol. `df` has (symbol, field) columns."""
    if df is None or len(df) == 0:
        return []
    out = []
    for yahoo, id_ in ids.items():
        if yahoo not in df.columns.get_level_values(0):
            continue
        closes = df[yahoo]["Close"].dropna()
        if closes.empty:
            continue
        ts = closes.index[-1]
        volume = df[yahoo]["Volume"].get(ts, 0.0)
        # a 1-minute bar is stamped at its start; never claim data newer than now
        out.append(Tick(id_, float(closes.iloc[-1]), 0.0 if math.isnan(volume) else float(volume),
                        min(now, ts.timestamp() + 60), "yahoo"))
    return out


async def history(symbol: Symbol, interval: str) -> list[Candle]:
    import yfinance as yf

    yahoo_interval, period = INTERVALS[interval]
    df = await asyncio.to_thread(
        lambda: yf.Ticker(symbol.yahoo).history(period=period, interval=yahoo_interval, auto_adjust=True)
    )
    return frame_to_candles(df, symbol.id, interval)


def _download(yahoo_symbols: list[str], period: str, interval: str):
    import yfinance as yf

    return yf.download(yahoo_symbols, period=period, interval=interval, group_by="ticker",
                       progress=False, threads=True, auto_adjust=True)


async def bars(symbols: list[Symbol], period: str, interval: str) -> dict[str, list[Candle]]:
    """Batched history for many symbols, keyed by symbol id."""
    if not symbols:
        return {}
    df = await asyncio.to_thread(_download, [s.yahoo for s in symbols], period, interval)
    out = {}
    for s in symbols:
        if df is not None and len(df) and s.yahoo in df.columns.get_level_values(0):
            out[s.id] = frame_to_candles(df[s.yahoo], s.id, interval)
    return out


async def poll_quotes(bus: Bus, symbols: Callable[[], list[Symbol]], every: float = 15.0,
                      download=_download) -> None:
    seen: dict[str, tuple[float, float]] = {}
    while True:
        wanted = symbols()
        if wanted:
            df = await asyncio.to_thread(download, [s.yahoo for s in wanted], "1d", "1m")
            ids = {s.yahoo: s.id for s in wanted}
            ticks = safe(lambda d: frame_to_ticks(d, ids, time.time()), df, "yahoo")
            if not ticks:
                # yfinance swallows network errors and returns an empty frame: say so instead of looking healthy
                raise ConnectionError("Yahoo returned no quotes")
            for tick in ticks:
                # a closed market repeats its last bar: publish only when something changed
                if seen.get(tick.symbol) != (tick.ts, tick.price):
                    seen[tick.symbol] = (tick.ts, tick.price)
                    bus.publish(tick)
        await asyncio.sleep(every)
