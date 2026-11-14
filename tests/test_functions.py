import pytest

from terminal import functions
from terminal.events import NewsItem


def text(value) -> str:
    return value["v"] if isinstance(value, dict) else value


def pairs(screen: dict) -> dict:
    return {k: text(v) for s in screen["sections"] for k, v in s.get("kv", [])}


def test_function_list_names_every_function(ctx):
    names = {f["name"] for f in ctx.get("/api/fn").json()}
    assert {"DES", "FA", "OMON", "WEI", "DCF", "DRSK", "MAP", "GP", "HELP"} <= names
    assert names == set(functions.FUNCTIONS) | set(functions.PAGE_FUNCTIONS)


def test_des_shows_company_figures(ctx):
    screen = ctx.get("/api/fn/DES?symbol=AAPL").json()
    assert "Test Corp" in screen["title"]
    assert pairs(screen)["Sector"] == "Technology" and pairs(screen)["Market cap"] == "1.00B"
    assert pairs(screen)["Position in range"] == "50%"


def test_unknown_function_and_missing_symbol(ctx):
    assert ctx.get("/api/fn/NOPE").status_code == 404
    r = ctx.get("/api/fn/DES")
    assert r.status_code == 404 and "needs a symbol" in r.json()["error"]
    assert ctx.get("/api/fn/DES?symbol=<bad>").json()["error"] == "symbol not found"


def test_company_functions_refuse_non_stocks_with_a_reason(ctx):
    r = ctx.get("/api/fn/FA?symbol=BTC")
    assert r.status_code == 404 and "stocks" in r.json()["error"]
    assert "no financial statements" in ctx.get("/api/fn/FA?symbol=SPY").json()["error"]


def test_fa_computes_ratios(ctx):
    screen = ctx.get("/api/fn/FA?symbol=AAPL").json()
    ratios = next(s for s in screen["sections"] if s["title"].startswith("RATIOS"))
    rows = {r[0]: [text(c) for c in r[1:]] for r in ratios["rows"]}
    assert rows["Revenue growth"] == ["+25.00%", "—"]
    assert rows["Net margin"][0] == "18.0%" and rows["Interest cover (EBIT / interest)"][0] == "12.0"


def test_omon_summarises_the_chain_and_flags_unusual_volume(ctx):
    screen = ctx.get("/api/fn/OMON?symbol=AAPL").json()
    summary = pairs(screen)
    assert summary["Max pain strike"] == "100.00" and summary["At-the-money implied vol"] == "30.0%"
    assert summary["Expected move by expiry"].startswith("±10.00")
    unusual = screen["sections"][2]
    assert [text(c) for c in unusual["rows"][0][:2]] == ["CALL", "100.00"] and len(unusual["rows"]) == 1
    assert "2099-02-19" in ctx.get("/api/fn/OMON?symbol=AAPL&arg=2").json()["title"]


def test_dcf_and_wacc_and_drsk_run_on_the_fake_company(ctx):
    dcf = pairs(ctx.get("/api/fn/DCF?symbol=AAPL").json())
    assert dcf["Free cash flow, last 12 months"] == "80.00M" and float(dcf["Fair value per share"]) > 0
    assert pairs(ctx.get("/api/fn/WACC?symbol=AAPL").json())["Weight of debt"] == "16.7%"
    drsk = pairs(ctx.get("/api/fn/DRSK?symbol=AAPL").json())
    assert drsk["Piotroski F-score"] == "9 / 9" and drsk["Z-score zone"] == "GREY ZONE"  # Z = 2.76


def test_market_monitor_lists_symbols_that_load_on_click(ctx):
    screen = ctx.get("/api/fn/WEI").json()
    first = screen["sections"][0]["rows"][0]
    assert first[0] == {"v": "S&P 500", "s": "^GSPC"} and first[1] == "12.00"
    assert first[2] == {"v": "+9.09%", "c": "up"}


def test_fxc_crosses_come_from_dollar_rates(ctx):
    table = ctx.get("/api/fn/FXC").json()["sections"][0]
    row = {r[0]["v"]: r for r in table["rows"]}
    usd = table["columns"].index("USD")
    assert row["EUR"][usd] == "12.0000"       # EURUSD closes at 12 in the fake data
    assert row["JPY"][usd] == "0.0833"        # USDJPY is quoted the other way round


def test_cn_and_top_read_stored_news(ctx):
    ctx.app.state.handle(NewsItem("n1", "Nvidia shares soar on record revenue", "https://example.com/n", "CNBC", 2e9, ""))
    cn = ctx.get("/api/fn/CN?symbol=NVDA").json()
    assert cn["sections"][0]["rows"][0][4] == {"v": "Nvidia shares soar on record revenue", "u": "https://example.com/n"}
    assert ctx.get("/api/fn/CN?symbol=MSFT").json()["sections"] == []


def test_ecst_without_a_key_says_where_to_get_one(ctx):
    r = ctx.get("/api/fn/ECST")
    assert r.status_code == 404 and "FRED_KEY" in r.json()["error"]


def test_help_lists_groups_and_a_failing_source_is_a_502(ctx):
    assert len(ctx.get("/api/fn/HELP").json()["sections"]) >= 8

    async def broken(*args):
        raise RuntimeError("yahoo down")
    ctx.app.state.bars = broken
    assert ctx.get("/api/fn/WEI").status_code == 502


def test_fed_contract_tickers_roll_over_the_year():
    from datetime import date
    assert functions.fed_contracts(date(2026, 11, 14), 3) == [
        ("2026-11", "ZQX26.CBT"), ("2026-12", "ZQZ26.CBT"), ("2027-01", "ZQF27.CBT")]


@pytest.mark.parametrize("value,shown", [(None, "—"), (1234.5, "1.23K"), (2.5e12, "2.50T"), (12, "12")])
def test_big(value, shown):
    assert functions.big(value) == shown
