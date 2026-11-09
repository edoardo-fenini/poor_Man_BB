import json
from pathlib import Path

from terminal.events import Tick
from terminal.feeds.base import safe
from terminal.feeds.finnhub import LIVE_LIMIT, parse_trades, split_live
from terminal.symbols import resolve

SAMPLES = Path(__file__).parent / "samples"


def test_parse_trades_sample():
    msgs = json.loads((SAMPLES / "finnhub_trade.json").read_text())
    ticks = [t for m in msgs for t in safe(parse_trades, m, "f")]
    assert ticks == [Tick("AAPL", 228.51, 100, 1762718400.123, "finnhub"),
                     Tick("MSFT", 415.2, 5, 1762718400.456, "finnhub")]


def test_parse_ping_and_malformed():
    for msg in ({"type": "ping"}, {}, {"type": "trade", "data": None}, {"type": "error", "msg": "x"},
                {"type": "trade", "data": [{"s": "AAPL", "p": 0, "t": 1, "v": 1}]}, None, "x"):
        assert safe(parse_trades, msg, "f") == []


def test_split_live_caps_at_50_and_excludes_non_us_stock():
    syms = [resolve("BTC"), resolve("EURUSD"), resolve("^GSPC"), resolve("ENI.MI")]
    syms += [resolve(f"S{i}") for i in range(60)]
    live, polled = split_live(syms)
    assert LIVE_LIMIT == 50 and len(live) == 50
    assert all(s.asset == "stock" and "." not in s.id for s in live)
    assert [s.id for s in live[:2]] == ["S0", "S1"]  # watchlist order decides who goes live
    assert len(polled) == 14 and {"BTC-USD", "ENI.MI", "S59"} <= {s.id for s in polled}
