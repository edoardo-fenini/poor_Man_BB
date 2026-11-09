from pathlib import Path

import pytest

from terminal.events import Candle
from terminal.feeds.base import safe
from terminal.feeds.sec import filing_text_url, parse_13f, parse_current, parse_form4
from terminal.feeds.volume import profile, sessions, unusual, universe

SAMPLES = Path(__file__).parent / "samples"
NOW = 1762720000.0
DAY = 86400


def filings():
    return parse_current((SAMPLES / "edgar_form4.atom").read_bytes(), NOW)


def test_parse_current_dedupes_by_accession():
    out = filings()
    assert [(f["form"], f["company"], f["accession"]) for f in out] == [
        ("4", "Acme Corp", "0001000001-25-000007"), ("13F-HR", "BIG FUND LP", "0001888888-25-000002")]
    assert out[0]["ts"] == 1762551005.0


def test_filing_text_url():
    assert filing_text_url(filings()[0]) == (
        "https://www.sec.gov/Archives/edgar/data/1000001/000100000125000007/0001000001-25-000007.txt")


def test_form4_purchase_and_sale_are_summed_per_side():
    buy, sell = parse_form4((SAMPLES / "form4.txt").read_bytes(), filings()[0])
    assert (buy.type, buy.symbol, buy.side) == ("insider", "ACME", "buy")
    assert buy.usd == pytest.approx(6000 * 52.00 + 4000 * 52.25)
    assert buy.detail == "DOE JANE (Chief Executive Officer) bought 10,000 @ $52.10"
    assert (sell.side, sell.usd) == ("sell", 53_000.0) and "sold 1,000 @ $53.00" in sell.detail
    assert buy.id != sell.id and buy.url == filings()[0]["url"] and buy.ts == filings()[0]["ts"]


def test_form4_ignores_grants_taxes_and_derivatives():
    # codes A (grant) and F (tax withholding) and the derivative table must not count
    events = parse_form4((SAMPLES / "form4.txt").read_bytes(), filings()[0])
    assert sum(e.usd for e in events) == pytest.approx(521_000 + 53_000)
    only_grants = (SAMPLES / "form4.txt").read_bytes().replace(b"<transactionCode>P<", b"<transactionCode>A<").replace(
        b"<transactionCode>S<", b"<transactionCode>M<")
    assert parse_form4(only_grants, filings()[0]) == []


def test_13f_top_positions_and_total():
    whale = parse_13f((SAMPLES / "infotable.txt").read_bytes(), filings()[1])
    assert (whale.type, whale.symbol, whale.side, whale.usd) == ("fund", "", "", 1_000_000_000.0)
    assert whale.detail == "BIG FUND LP 13F: APPLE INC 50.0%, MICROSOFT CORP 50.0%"
    assert parse_13f((SAMPLES / "infotable.txt").read_bytes(), filings()[1], top=1).detail.count("%") == 1


def test_sec_parsers_survive_garbage():
    f = filings()[0]
    for raw in (b"", b"<html>", b"<XML><ownershipDocument><issuer>", b"\xff\xfe"):
        assert safe(lambda b: parse_form4(b, f), raw, "s") == []
        assert safe(lambda b: parse_13f(b, f), raw, "s") == []
        assert safe(lambda b: parse_current(b, NOW), raw, "s") == []


def bars(day, volumes, symbol="AAA", price=10.0):
    start = day * DAY + 13.5 * 3600
    return [Candle(symbol, "5m", start + i * 300, price, price, price, price, v) for i, v in enumerate(volumes)]


def test_sessions_split_by_day():
    history = bars(20000, [1, 2]) + bars(20001, [3, 4, 5])
    assert [len(s) for s in sessions(history)] == [2, 3]


def test_profile_and_unusual():
    history = [c for day in range(20000, 20020) for c in bars(day, [100] * 78)]
    prof = profile({"AAA": history})["AAA"]
    assert prof[:3] == [100.0, 200.0, 300.0] and len(prof) == 78
    whale = unusual("AAA", bars(20020, [400, 400, 400]), prof, ratio=3.0)
    assert (whale.type, whale.symbol, whale.detail) == ("volume", "AAA", "4.0× normal volume")
    assert whale.usd == 12_000.0 and whale.id == "vol:AAA:20020"
    assert unusual("AAA", bars(20020, [250, 250, 250]), prof, ratio=3.0) is None


def test_unusual_needs_three_slots_and_nonzero_profile():
    prof = profile({"AAA": [c for day in range(20000, 20020) for c in bars(day, [100] * 78)]})["AAA"]
    assert unusual("AAA", bars(20020, [9000, 9000]), prof, 3.0) is None
    assert unusual("AAA", bars(20020, [9000] * 3), [0.0] * 78, 3.0) is None
    assert unusual("AAA", [], prof, 3.0) is None
    assert unusual("AAA", bars(20020, [9000] * 90), prof, 3.0) is not None  # longer day than the profile


def test_profile_handles_missing_bars_and_short_history():
    gappy = bars(20000, [100] * 5)
    del gappy[2]
    assert profile({"AAA": gappy})["AAA"] == [100.0, 200.0, 200.0, 300.0, 400.0]
    assert profile({"AAA": []}) == {}


def test_universe_is_us_stocks_from_watchlist_and_entity_list():
    from terminal.symbols import resolve
    ids = {s.id for s in universe([resolve("ZZZZ"), resolve("BTC"), resolve("ENI.MI")])}
    assert {"ZZZZ", "AAPL", "NVDA"} <= ids and not ids & {"BTC-USD", "ENI.MI", "GC=F", "^VIX"}


def test_filing_xml_with_entity_declarations_is_refused():
    raw = (SAMPLES / "form4.txt").read_bytes()
    bomb = raw.replace(b'<?xml version="1.0"?>', b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa">]>')
    assert bomb != raw and len(parse_form4(raw, filings()[0])) == 2
    assert parse_form4(bomb, filings()[0]) == []


async def test_volume_poll_raises_when_no_data_comes_back():
    from terminal.bus import Bus
    from terminal.feeds.volume import poll

    async def no_bars(symbols, period, interval):
        return {s.id: [] for s in symbols}

    with pytest.raises(ConnectionError):
        await poll(Bus(), lambda: [], lambda: {"volume_ratio": 3.0}, bars=no_bars)


async def test_volume_poll_publishes_unusual_volume():
    import asyncio
    import time

    from terminal.bus import Bus
    from terminal.feeds.volume import poll

    today = int(time.time() // DAY)

    async def fake_bars(symbols, period, interval):
        if period == "1mo":
            return {"AAPL": [c for day in range(today - 20, today) for c in bars(day, [100] * 78, "AAPL")]}
        return {"AAPL": bars(today, [500, 500, 500], "AAPL")}

    bus = Bus()
    sub = bus.subscribe()
    task = asyncio.create_task(poll(bus, lambda: [], lambda: {"volume_ratio": 3.0}, every=60, bars=fake_bars))
    whale = await asyncio.wait_for(anext(sub), 2)
    task.cancel()
    assert (whale.symbol, whale.type, whale.detail) == ("AAPL", "volume", "5.0× normal volume")
