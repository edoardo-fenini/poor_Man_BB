import json
from pathlib import Path

import pytest

from terminal.feeds.base import safe
from terminal.feeds.crypto import Trade, big_trade
from terminal.feeds.onchain import load_labels, parse_btc_tx, parse_eth_block, parse_token_logs
from terminal.settings import DEFAULTS, load_settings
from terminal.store import Store

SAMPLES = Path(__file__).parent / "samples"
A, B = "0x" + "a" * 40, "0x" + "b" * 40


def load(name):
    return json.loads((SAMPLES / name).read_text())


def test_big_trade_threshold():
    t = Trade("BTC-USD", 100_000, 12, "buy", 1.0, "binance")
    whale = big_trade(t, 1_000_000)
    assert (whale.usd, whale.type, whale.symbol, whale.side, whale.ts) == (1_200_000, "crypto_trade", "BTC-USD", "buy", 1.0)
    assert "binance" in whale.detail and big_trade(t, 2_000_000) is None


def test_big_trade_ids_differ_per_trade():
    a = big_trade(Trade("BTC-USD", 100_000, 12, "buy", 1.0, "binance"), 1)
    b = big_trade(Trade("BTC-USD", 100_000, 13, "buy", 1.0, "binance"), 1)
    assert a.id != b.id


def test_btc_tx_excludes_change_output():
    whale = parse_btc_tx(load("btc_utx.json"), btc_usd=100_000, min_usd=10_000_000, labels={})
    assert whale.usd == pytest.approx(20_000_000) and whale.symbol == "BTC-USD" and whale.type == "onchain"
    assert whale.detail.startswith("200 BTC") and whale.ts == 1762718400
    assert whale.url == "https://mempool.space/tx/" + load("btc_utx.json")["x"]["hash"] and whale.id == load("btc_utx.json")["x"]["hash"]


def test_btc_tx_below_threshold_is_none():
    assert parse_btc_tx(load("btc_utx.json"), 100_000, 30_000_000, {}) is None


def test_btc_tx_applies_label():
    labels = {"bc1qreceiverbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb": "Exchange X"}
    assert "→ Exchange X" in parse_btc_tx(load("btc_utx.json"), 100_000, 1, labels).detail


def test_eth_block_sample():
    whales = parse_eth_block(load("eth_block.json"), eth_usd=3000, min_usd=10_000_000, labels={B: "Exchange Y"})
    assert len(whales) == 1
    w = whales[0]
    assert w.usd == pytest.approx(15_000_000) and w.symbol == "ETH-USD" and w.id == "0x01"
    assert w.detail.startswith("5,000 ETH") and "Exchange Y" in w.detail and w.ts == 1762719168
    assert w.url == "https://etherscan.io/tx/0x01"


def test_token_logs_decimals():
    whales = parse_token_logs(load("eth_logs.json"), "USDT", 6, min_usd=10_000_000, labels={})
    assert [(w.usd, w.symbol, w.id) for w in whales] == [(25_000_000.0, "USDT-USD", "0x11:0x1")]
    assert whales[0].detail.startswith("25,000,000 USDT 0xaaaa…aaaa → 0xbbbb…bbbb")


def test_malformed_inputs_are_skipped():
    for bad in ({}, {"op": "utx"}, {"op": "utx", "x": {"hash": "h", "inputs": [], "out": None}}, None, "x",
                {"op": "pong"}):
        assert safe(lambda m: parse_btc_tx(m, 100_000, 1, {}), bad, "o") == []
    for bad in ({}, {"result": None}, {"result": {"transactions": [{"hash": "0x1", "value": "zz"}]}}, None):
        assert safe(lambda m: parse_eth_block(m, 3000, 1, {}), bad, "o") == []
    for bad in ({}, {"status": "0", "message": "No records found", "result": []},
                {"status": "0", "message": "NOTOK", "result": "Max rate limit reached"}):
        assert safe(lambda m: parse_token_logs(m, "USDT", 6, 1, {}), bad, "o") == []


def test_one_bad_transaction_does_not_hide_the_rest():
    block = load("eth_block.json")
    block["result"]["transactions"].insert(0, {"hash": "0x00", "value": "zz"})
    assert len(parse_eth_block(block, 3000, 10_000_000, {})) == 1


def test_labels_file_loads_and_ships_empty():
    assert load_labels() == {}


def test_settings_defaults_and_overrides():
    store = Store(":memory:")
    assert load_settings(store) == DEFAULTS and DEFAULTS["whale_trade_usd"] == 1_000_000
    assert DEFAULTS["whale_onchain_usd"] == 10_000_000
    store.set("settings", {"whale_trade_usd": 5, "unknown_key": 1})
    assert load_settings(store)["whale_trade_usd"] == 5 and "unknown_key" not in load_settings(store)
