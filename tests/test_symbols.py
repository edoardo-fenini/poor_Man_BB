import pytest

from terminal.symbols import resolve


@pytest.mark.parametrize("text,id_,asset,yahoo", [
    ("aapl", "AAPL", "stock", "AAPL"), ("$TSLA", "TSLA", "stock", "TSLA"),
    ("BRK.B", "BRK.B", "stock", "BRK-B"),
    ("btc", "BTC-USD", "crypto", "BTC-USD"), ("ETHUSDT", "ETH-USD", "crypto", "ETH-USD"),
    ("sol/usd", "SOL-USD", "crypto", "SOL-USD"), ("BTC-USD", "BTC-USD", "crypto", "BTC-USD"),
    ("eur/usd", "EURUSD", "fx", "EURUSD=X"), ("USDJPY", "USDJPY", "fx", "USDJPY=X"),
    ("^gspc", "^GSPC", "index", "^GSPC"), ("GC=F", "GC=F", "future", "GC=F"),
    ("DX-Y.NYB", "DX-Y.NYB", "index", "DX-Y.NYB"),
])
def test_resolve(text, id_, asset, yahoo):
    s = resolve(text)
    assert (s.id, s.asset, s.yahoo) == (id_, asset, yahoo)


def test_crypto_provider_symbols():
    s = resolve("BTC")
    assert (s.binance, s.coinbase, s.kraken) == ("BTCUSDT", "BTC-USD", "XBT/USD")
    assert resolve("ETH").kraken == "ETH/USD"


def test_stock_has_finnhub_symbol_and_no_crypto_symbols():
    s = resolve("MSFT")
    assert s.finnhub == "MSFT" and s.binance == "" and s.coinbase == ""


@pytest.mark.parametrize("bad", ["", "   ", "<script>", "A" * 16, "AAPL; DROP", "héllo"])
def test_garbage_returns_none(bad):
    assert resolve(bad) is None
