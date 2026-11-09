import json
from pathlib import Path

import pytest

from terminal.feeds.base import safe
from terminal.feeds.crypto import Throttle, Trade, parse_binance, parse_coinbase, parse_kraken
from terminal.symbols import resolve

SAMPLES = Path(__file__).parent / "samples"


def load(name):
    return json.loads((SAMPLES / name).read_text())


def all_trades(parse, name):
    return [t for msg in load(name) for t in safe(parse, msg, "c")]


def test_binance_sample():
    trades = all_trades(parse_binance, "binance_trade.json")
    assert trades == [
        Trade("BTC-USD", 103250.50, 0.25, "sell", 1762718400.05, "binance"),  # m true: taker sold
        Trade("ETH-USD", 3400.10, 2.5, "buy", 1762718401.0, "binance"),
    ]


def test_coinbase_sample_inverts_maker_side():
    trades = all_trades(parse_coinbase, "coinbase_match.json")
    assert [(t.symbol, t.price, t.size, t.side, t.source) for t in trades] == [
        ("BTC-USD", 103200.01, 0.5, "sell", "coinbase"),
        ("BTC-USD", 103201.00, 1.25, "buy", "coinbase"),
    ]
    assert trades[0].ts == pytest.approx(1762718400.5)


def test_kraken_sample_maps_xbt_to_btc():
    trades = all_trades(parse_kraken, "kraken_trade.json")
    assert [(t.symbol, t.price, t.size, t.side) for t in trades] == [
        ("BTC-USD", 103199.5, 0.75, "sell"), ("BTC-USD", 103200.0, 0.1, "buy")]
    assert trades[0].ts == pytest.approx(1762718402.0)


@pytest.mark.parametrize("parse", [parse_binance, parse_coinbase, parse_kraken])
def test_non_trade_and_malformed_messages_yield_nothing(parse):
    bad = ({}, {"data": None}, {"type": "subscriptions"}, {"data": {"p": "abc"}}, [], "x", None,
           {"type": "match", "price": "abc", "size": "1", "product_id": "BTC-USD"},
           {"channel": "trade", "data": [{"symbol": "BTC/USD", "price": None}]},
           {"data": {"e": "aggTrade", "s": "BTCUSDT", "p": "0", "q": "1", "T": 1, "m": True}})
    for msg in bad:
        assert safe(parse, msg, "c") == []


def test_kraken_symbol_spelling_for_v2():
    assert resolve("BTC").kraken == "BTC/USD"


def test_throttle_lets_one_tick_per_symbol_per_interval():
    t = Throttle(0.25)
    assert t.allow("BTC-USD", 10.0) and not t.allow("BTC-USD", 10.1)
    assert t.allow("ETH-USD", 10.1) and t.allow("BTC-USD", 10.26)
