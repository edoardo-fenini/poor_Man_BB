import numpy as np
import pandas as pd

from terminal.feeds.yahoo import frame_to_candles, frame_to_ticks

IDX = pd.to_datetime(["2026-11-06 14:30", "2026-11-06 14:31", "2026-11-06 14:32"], utc=True)


def ohlcv(closes):
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": [10, 20, 30]}, index=IDX
    )


def test_frame_to_candles_drops_nan_rows():
    out = frame_to_candles(ohlcv([1.0, np.nan, 3.0]), "AAPL", "1m")
    assert [c.close for c in out] == [1.0, 3.0]
    assert out[0].ts < out[1].ts and out[0].ts == IDX[0].timestamp()
    assert out[0].symbol == "AAPL" and out[0].interval == "1m" and out[1].volume == 30


def test_frame_to_candles_empty_or_none_is_empty_list():
    assert frame_to_candles(pd.DataFrame(), "AAPL", "1d") == []
    assert frame_to_candles(None, "AAPL", "1d") == []


def test_frame_to_ticks_takes_last_valid_row_per_symbol():
    df = pd.concat({"AAPL": ohlcv([1.0, 2.0, 3.0]), "EURUSD=X": ohlcv([1.1, 1.2, np.nan])}, axis=1)
    ticks = frame_to_ticks(df, {"AAPL": "AAPL", "EURUSD=X": "EURUSD"}, now=IDX[2].timestamp() + 5)
    by = {t.symbol: t for t in ticks}
    assert by["AAPL"].price == 3.0 and by["EURUSD"].price == 1.2
    assert by["AAPL"].source == "yahoo"
    # timestamp reflects the data, never later than now
    assert by["AAPL"].ts == IDX[2].timestamp() + 5
    assert by["EURUSD"].ts == IDX[1].timestamp() + 60


def test_frame_to_ticks_skips_missing_symbols():
    df = pd.concat({"AAPL": ohlcv([1.0, 2.0, 3.0])}, axis=1)
    assert [t.symbol for t in frame_to_ticks(df, {"AAPL": "AAPL", "ZZZZ": "ZZZZ"}, now=2e9)] == ["AAPL"]
    assert frame_to_ticks(pd.DataFrame(), {"AAPL": "AAPL"}, now=0) == []


async def test_poll_quotes_raises_when_yahoo_returns_nothing():
    import pytest

    from terminal.bus import Bus
    from terminal.feeds.yahoo import poll_quotes
    from terminal.symbols import resolve

    with pytest.raises(ConnectionError):   # yfinance swallows network errors and hands back an empty frame
        await poll_quotes(Bus(), lambda: [resolve("AAPL")], download=lambda symbols, period, interval: pd.DataFrame())


async def test_poll_quotes_publishes_then_skips_unchanged():
    import asyncio

    from terminal.bus import Bus
    from terminal.feeds.yahoo import poll_quotes
    from terminal.symbols import resolve

    bus = Bus()
    sub = bus.subscribe()
    frame = pd.concat({"AAPL": ohlcv([1.0, 2.0, 3.0])}, axis=1)
    task = asyncio.create_task(poll_quotes(bus, lambda: [resolve("AAPL")], every=0.01,
                                           download=lambda symbols, period, interval: frame))
    await asyncio.sleep(0.1)
    task.cancel()
    assert sub.queue.qsize() == 1 and sub.queue.get_nowait().price == 3.0   # an old bar is published once, not every poll
