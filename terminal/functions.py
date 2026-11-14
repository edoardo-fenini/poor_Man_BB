"""Bloomberg-style functions: `AAPL DES`, `WEI`, `OMON`... Each builds one screen as plain data.

A screen is {"title", "sections": [...], "note"}. A section has a "title" and one of:
  "kv": [[label, cell]]   "columns" + "rows": [[cell]]   "text": str   "line": {"labels", "series": [{name, values}]}
A cell is a string, or {"v": text, "c": up|down|dim|hot, "s": symbol to load, "u": link, "f": function to run}.
"""
import asyncio
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from terminal import calc
from terminal.config import Config
from terminal.feeds import econ
from terminal.store import Store
from terminal.symbols import Symbol, resolve

DASH = "—"


class Unavailable(Exception):
    """The function cannot answer for this symbol or setup. The message is shown to the user."""


@dataclass
class Ctx:
    src: object                                   # terminal.feeds.fundamentals, or a fake in tests
    bars: Callable[..., Awaitable[dict]]          # yahoo.bars
    store: Store
    watchlist: Callable[[], list[Symbol]]
    config: Config


@dataclass(frozen=True)
class Fn:
    name: str
    about: str
    group: str
    build: Callable[..., Awaitable[dict]]
    symbol: bool = False


# ---------------------------------------------------------------- formatting
def cell(v, c: str = "", s: str = "", u: str = "", f: str = "") -> dict:
    return {k: val for k, val in (("v", str(v)), ("c", c), ("s", s), ("u", u), ("f", f)) if val}


def num(x, digits: int = 2) -> str:
    return DASH if x is None else f"{x:,.{digits}f}"


def big(x) -> str:
    if x is None:
        return DASH
    for limit, unit in ((1e12, "T"), (1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(x) >= limit:
            return f"{x / limit:,.2f}{unit}"
    return f"{x:,.0f}"


def tone(x) -> str:
    return "" if x is None else "up" if x > 0 else "down" if x < 0 else "dim"


def pct(x, digits: int = 2, plain: bool = False):
    """A fraction as a signed, coloured percent. `plain` drops the sign and colour (margins, yields)."""
    if x is None:
        return DASH
    return f"{x * 100:,.{digits}f}%" if plain else cell(f"{x * 100:+,.{digits}f}%", tone(x))


def stamp(ts) -> str:
    return DASH if not ts else datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def div(a, b):
    return a / b if a is not None and b else None


def change(a, b):
    """Fractional change from b to a, or None when either is missing."""
    return a / b - 1 if a is not None and b else None


def screen(title: str, sections: list, note: str = "") -> dict:
    return {"title": title, "sections": [s for s in sections if s], "note": note}


def kv(title: str, pairs: list) -> dict:
    return {"title": title, "kv": [[k, v] for k, v in pairs]}


def table(title: str, columns: list[str], rows: list[list]) -> dict | None:
    return {"title": title, "columns": columns, "rows": rows} if rows else None


# ---------------------------------------------------------------- data helpers
async def run(fn, *args):
    return await asyncio.to_thread(fn, *args)


async def closes(ctx: Ctx, symbols: list[Symbol], period: str, interval: str = "1d") -> dict[str, dict[int, float]]:
    """{symbol id: {day number: close}}"""
    data = await ctx.bars(symbols, period, interval)
    return {id_: {int(c.ts // 86400): c.close for c in rows} for id_, rows in data.items() if rows}


def series(days: dict[int, float]) -> list[float]:
    return [days[d] for d in sorted(days)]


def need_stock(sym: Symbol, fn: str) -> None:
    if sym.asset != "stock":
        raise Unavailable(f"{fn} works on stocks and ETFs, not on {sym.asset} symbols")


async def company(ctx: Ctx, sym: Symbol) -> dict:
    info = await run(ctx.src.info, sym.yahoo)
    if not info or len(info) < 5:
        raise Unavailable(f"Yahoo has no company data for {sym.id}")
    return info


def spot_of(info: dict) -> float | None:
    return info.get("regularMarketPrice") or info.get("currentPrice") or info.get("previousClose")


async def risk_free(ctx: Ctx, ticker: str = "^IRX") -> float:
    """A Treasury yield as a fraction: 13-week bill by default, ^TNX for ten years."""
    try:
        days = (await closes(ctx, [resolve(ticker)], "5d")).get(ticker)
        return series(days)[-1] / 100 if days else 0.04
    except Exception:
        return 0.04  # shortcut: a fixed 4% when Yahoo will not give the yield; fine for a Sharpe ratio


def line(stmts: dict, table_: str, name: str, i: int = 0):
    values = stmts[table_]["rows"].get(name) or []
    return values[i] if i < len(values) else None


def periods_back(values: list[float], this_year: list[int]) -> dict[str, float | None]:
    """Change over the usual look-backs. `this_year[i]` is the calendar year of values[i]."""
    def back(n):
        return values[-1] / values[-1 - n] - 1 if len(values) > n and values[-1 - n] else None
    ytd_base = next((v for v, y in zip(reversed(values), reversed(this_year)) if y < this_year[-1]), None)
    return {"1D": back(1), "5D": back(5), "1M": back(21), "3M": back(63), "6M": back(126),
            "YTD": values[-1] / ytd_base - 1 if ytd_base else None, "1Y": back(252) if len(values) > 252 else
            (values[-1] / values[0] - 1 if len(values) > 200 else None)}


def year_of(day: int) -> int:
    return date.fromordinal(day + 719163).year  # day 0 is 1970-01-01


# ---------------------------------------------------------------- company
async def des(ctx, sym, arg):
    i = await company(ctx, sym)
    get = i.get
    low, high, px = get("fiftyTwoWeekLow"), get("fiftyTwoWeekHigh"), spot_of(i)
    officers = [[o.get("name", ""), o.get("title", ""), big(o.get("totalPay"))] for o in (get("companyOfficers") or [])[:8]]
    return screen(f"{sym.id}  {get('longName') or get('shortName') or ''}", [
        kv("PROFILE", [("Sector", get("sector", DASH)), ("Industry", get("industry", DASH)),
                       ("Country", get("country", DASH)), ("Employees", big(get("fullTimeEmployees"))),
                       ("Exchange", get("fullExchangeName", DASH)), ("Currency", get("currency", DASH)),
                       ("Website", cell(get("website", DASH), u=get("website", "")))]),
        {"title": "BUSINESS", "text": get("longBusinessSummary") or get("description") or ""} if
        get("longBusinessSummary") or get("description") else None,
        kv("MARKET", [("Price", num(px)), ("Market cap", big(get("marketCap"))), ("Enterprise value", big(get("enterpriseValue"))),
                      ("52-week range", f"{num(low)} – {num(high)}"),
                      ("Position in range", pct(div(px - low, high - low), 0, True) if px and low and high else DASH),
                      ("Beta", num(get("beta"))), ("Average volume", big(get("averageVolume"))),
                      ("Shares outstanding", big(get("sharesOutstanding"))), ("Float", big(get("floatShares")))]),
        kv("VALUATION", [("P/E trailing", num(get("trailingPE"))), ("P/E forward", num(get("forwardPE"))),
                         ("PEG", num(get("trailingPegRatio"))), ("Price / book", num(get("priceToBook"))),
                         ("Price / sales", num(get("priceToSalesTrailing12Months"))),
                         ("EV / EBITDA", num(get("enterpriseToEbitda"))), ("EV / revenue", num(get("enterpriseToRevenue"))),
                         ("Earnings yield", pct(div(get("trailingEps"), px), 2, True)),
                         ("Free cash flow yield", pct(div(get("freeCashflow"), get("marketCap")), 2, True))]),
        kv("FINANCIALS (TTM)", [("Revenue", big(get("totalRevenue"))), ("Revenue growth", pct(get("revenueGrowth"))),
                                ("Gross margin", pct(get("grossMargins"), 1, True)), ("Operating margin", pct(get("operatingMargins"), 1, True)),
                                ("Net margin", pct(get("profitMargins"), 1, True)), ("EBITDA", big(get("ebitda"))),
                                ("Free cash flow", big(get("freeCashflow"))), ("Return on equity", pct(get("returnOnEquity"), 1, True)),
                                ("Total cash", big(get("totalCash"))), ("Total debt", big(get("totalDebt"))),
                                ("Debt / equity", num(div(get("debtToEquity"), 100)))]),
        kv("DIVIDEND", [("Rate", num(get("dividendRate"))), ("Yield", num(get("dividendYield")) + "%" if get("dividendYield") else DASH),
                        ("Payout ratio", pct(get("payoutRatio"), 0, True)), ("Ex-dividend", stamp(get("exDividendDate")))]),
        table("MANAGEMENT", ["Name", "Title", "Pay"], officers),
    ], "Source: Yahoo Finance. Ratios marked yield are computed here.")


FA_LINES = {
    "income": ["Total Revenue", "Cost Of Revenue", "Gross Profit", "Research And Development",
               "Selling General And Administration", "Operating Income", "EBITDA", "Interest Expense", "Pretax Income",
               "Tax Provision", "Net Income", "Diluted EPS", "Diluted Average Shares"],
    "balance": ["Cash Cash Equivalents And Short Term Investments", "Receivables", "Inventory", "Current Assets",
                "Net PPE", "Goodwill And Other Intangible Assets", "Total Assets", "Current Liabilities", "Total Debt",
                "Total Liabilities Net Minority Interest", "Stockholders Equity", "Retained Earnings", "Working Capital",
                "Net Debt"],
    "cash": ["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow", "Stock Based Compensation",
             "Repurchase Of Capital Stock", "Cash Dividends Paid", "Issuance Of Debt", "Repayment Of Debt"],
}


async def statements(ctx, sym, fn: str) -> dict:
    need_stock(sym, fn)
    stmts = await run(ctx.src.statements, sym.yahoo)
    if not stmts["income"]["periods"]:
        raise Unavailable(f"Yahoo has no financial statements for {sym.id} (funds and ETFs have none)")
    return stmts


async def fa(ctx, sym, arg):
    stmts = await statements(ctx, sym, "FA")
    periods = stmts["income"]["periods"]

    def block(name: str, title: str):
        t = stmts[name]
        rows = [[label] + [num(v) if "EPS" in label else big(v) for v in t["rows"][label]]
                for label in FA_LINES[name] if label in t["rows"]]
        return table(title, ["Year ending"] + t["periods"], rows)

    def ratio(label, fn_):
        return [label] + [fn_(i) for i in range(len(periods))]

    def g(t, name):
        return lambda i: line(stmts, t, name, i)
    rev, ni, ebit = g("income", "Total Revenue"), g("income", "Net Income"), g("income", "EBIT")
    ratios = [
        ratio("Revenue growth", lambda i: pct(change(rev(i), rev(i + 1)))),
        ratio("Gross margin", lambda i: pct(div(g("income", "Gross Profit")(i), rev(i)), 1, True)),
        ratio("Operating margin", lambda i: pct(div(g("income", "Operating Income")(i), rev(i)), 1, True)),
        ratio("Net margin", lambda i: pct(div(ni(i), rev(i)), 1, True)),
        ratio("Free cash flow margin", lambda i: pct(div(g("cash", "Free Cash Flow")(i), rev(i)), 1, True)),
        ratio("Return on equity", lambda i: pct(div(ni(i), g("balance", "Stockholders Equity")(i)), 1, True)),
        ratio("Return on assets", lambda i: pct(div(ni(i), g("balance", "Total Assets")(i)), 1, True)),
        ratio("Return on invested capital", lambda i: pct(div(
            ebit(i) * (1 - (g("income", "Tax Rate For Calcs")(i) or 0.21)) if ebit(i) is not None else None,
            g("balance", "Invested Capital")(i)), 1, True)),
        ratio("Debt / equity", lambda i: num(div(g("balance", "Total Debt")(i), g("balance", "Stockholders Equity")(i)))),
        ratio("Interest cover (EBIT / interest)", lambda i: num(div(ebit(i), g("income", "Interest Expense")(i)), 1)),
    ]
    return screen(f"{sym.id}  FINANCIAL ANALYSIS", [
        block("income", "INCOME STATEMENT"), table("RATIOS (computed)", ["Year ending"] + periods, ratios),
        block("balance", "BALANCE SHEET"), block("cash", "CASH FLOW")], "Annual, as reported to Yahoo Finance.")


async def hp(ctx, sym, arg):
    data = (await ctx.bars([sym], "6mo", "1d")).get(sym.id) or []
    if not data:
        raise Unavailable(f"no price history for {sym.id}")
    rows = [[stamp(c.ts), num(c.open), num(c.high), num(c.low), num(c.close), pct(change(c.close, prev.close)), big(c.volume)]
            for prev, c in zip(data, data[1:])]
    return screen(f"{sym.id}  HISTORICAL PRICES", [table("DAILY", ["Date", "Open", "High", "Low", "Close", "Change", "Volume"],
                                                         rows[::-1])])


async def anr(ctx, sym, arg):
    need_stock(sym, "ANR")
    i, a = await asyncio.gather(company(ctx, sym), run(ctx.src.analysts, sym.yahoo))
    px, target = spot_of(i), i.get("targetMeanPrice")
    trend = [[r.get("period", ""), r.get("strongBuy", 0), r.get("buy", 0), r.get("hold", 0), r.get("sell", 0),
              r.get("strongSell", 0)] for r in a["trend"]]
    changes = [[r.get("GradeDate", ""), r.get("Firm", ""), r.get("Action", ""), r.get("FromGrade", ""), r.get("ToGrade", ""),
                num(r.get("currentPriceTarget")) if r.get("currentPriceTarget") else DASH] for r in a["changes"]]
    return screen(f"{sym.id}  ANALYST RECOMMENDATIONS", [
        kv("CONSENSUS", [("Rating", str(i.get("recommendationKey", DASH)).upper()), ("Score (1 buy – 5 sell)", num(i.get("recommendationMean"))),
                         ("Analysts", num(i.get("numberOfAnalystOpinions"), 0)), ("Price", num(px)),
                         ("Target mean", num(target)), ("Target high", num(i.get("targetHighPrice"))),
                         ("Target low", num(i.get("targetLowPrice"))),
                         ("Upside to mean target", pct(change(target, px)))]),
        table("RATING COUNT BY MONTH", ["Month", "Strong buy", "Buy", "Hold", "Sell", "Strong sell"],
              [[str(x) for x in r] for r in trend]),
        table("RATING CHANGES", ["Date", "Firm", "Action", "From", "To", "Target"], changes)])


async def ern(ctx, sym, arg):
    need_stock(sym, "ERN")
    rows = [[r.get("Earnings Date", ""), num(r.get("EPS Estimate")), num(r.get("Reported EPS")),
             pct(div(r.get("Surprise(%)"), 100)) if r.get("Surprise(%)") is not None else DASH]
            for r in await run(ctx.src.earnings, sym.yahoo)]
    beats = [r for r in rows if isinstance(r[3], dict)]
    return screen(f"{sym.id}  EARNINGS", [
        kv("RECORD", [("Quarters reported", str(len(beats))), ("Beat the estimate", str(sum(b[3]["c"] == "up" for b in beats)))]),
        table("EARNINGS DATES", ["Date", "EPS estimate", "EPS reported", "Surprise"], rows)])


async def dvd(ctx, sym, arg):
    need_stock(sym, "DVD")
    i, paid = await asyncio.gather(company(ctx, sym), run(ctx.src.dividends, sym.yahoo))
    if not paid:
        raise Unavailable(f"{sym.id} has paid no dividends")
    by_year: dict[str, float] = {}
    for day, amount in paid:
        by_year[day[:4]] = by_year.get(day[:4], 0) + amount
    full = sorted(by_year)[:-1]  # the current year is not finished
    growth = calc.cagr(by_year[full[-6]], by_year[full[-1]], 5) if len(full) >= 6 else None
    return screen(f"{sym.id}  DIVIDENDS", [
        kv("SUMMARY", [("Annual rate", num(i.get("dividendRate"))), ("Yield", num(i.get("dividendYield")) + "%" if i.get("dividendYield") else DASH),
                       ("Payout ratio", pct(i.get("payoutRatio"), 0, True)), ("5-year growth a year (computed)", pct(growth)),
                       ("Ex-dividend", stamp(i.get("exDividendDate")))]),
        table("PER YEAR", ["Year", "Paid per share"], [[y, num(by_year[y], 4)] for y in sorted(by_year, reverse=True)[:12]]),
        table("PAYMENTS", ["Ex-date", "Amount"], [[d, num(a, 4)] for d, a in paid[::-1][:24]])])


async def hds(ctx, sym, arg):
    need_stock(sym, "HDS")
    h = await run(ctx.src.holders, sym.yahoo)
    m = h["major"]
    return screen(f"{sym.id}  HOLDERS", [
        kv("OWNERSHIP", [("Held by insiders", pct(m.get("insidersPercentHeld"), 2, True)),
                         ("Held by institutions", pct(m.get("institutionsPercentHeld"), 2, True)),
                         ("Institutions", num(m.get("institutionsCount"), 0))]),
        table("LARGEST INSTITUTIONS", ["Reported", "Holder", "% held", "Shares", "Value", "Change"],
              [[r.get("Date Reported", ""), r.get("Holder", ""), pct(r.get("pctHeld"), 2, True), big(r.get("Shares")),
                big(r.get("Value")), pct(r.get("pctChange"))] for r in h["institutions"]]),
        table("INSIDER TRADES", ["Date", "Insider", "Position", "What", "Shares", "Value"],
              [[r.get("Start Date", ""), r.get("Insider", ""), r.get("Position", ""), r.get("Text") or r.get("Transaction", ""),
                big(r.get("Shares")), big(r.get("Value"))] for r in h["insiders"]])])


async def si(ctx, sym, arg):
    need_stock(sym, "SI")
    i = await company(ctx, sym)
    short, before = i.get("sharesShort"), i.get("sharesShortPriorMonth")
    return screen(f"{sym.id}  SHORT INTEREST", [kv("SHORT INTEREST", [
        ("As of", stamp(i.get("dateShortInterest"))), ("Shares short", big(short)), ("Month before", big(before)),
        ("Change on the month", pct(change(short, before))),
        ("% of float", pct(i.get("shortPercentOfFloat"), 2, True)), ("% of shares", pct(i.get("sharesPercentSharesOut"), 2, True)),
        ("Days to cover", num(i.get("shortRatio")))])], "Exchanges publish short interest twice a month.")


def news_rows(items: list[dict]) -> list[list]:
    return [[datetime.fromtimestamp(n["ts"]).strftime("%m-%d %H:%M"), n["source"], "▮" * n["analysis"]["weight"],
             cell("▲" if n["analysis"]["sentiment"] > 0 else "▼" if n["analysis"]["sentiment"] < 0 else "•", tone(n["analysis"]["sentiment"])),
             cell(n["title"], u=n["url"])] for n in items]


async def cn(ctx, sym, arg):
    items = [n for n in ctx.store.recent("news", 2000) if any(i["symbol"] == sym.id for i in n["analysis"]["impacts"])]
    return screen(f"{sym.id}  COMPANY NEWS", [table("STORIES TAGGED WITH THIS SYMBOL", ["Time", "Source", "Weight", "", "Headline"],
                                                    news_rows(items[:200]))], "" if items else "No stored story names this symbol yet.")


async def top(ctx, sym, arg):
    items = [n for n in ctx.store.recent("news", 2000) if n["analysis"]["weight"] >= 3]
    return screen("TOP NEWS", [table("WEIGHT 3 AND ABOVE", ["Time", "Source", "Weight", "", "Headline"], news_rows(items[:200]))],
                  "" if items else "No heavy story stored yet.")


# ---------------------------------------------------------------- options
async def chain(ctx, sym, arg):
    index = int(arg) - 1 if arg.isdigit() else 0
    i, opts = await asyncio.gather(company(ctx, sym), run(ctx.src.options, sym.yahoo, index))
    spot = spot_of(i)
    if not opts["expiry"] or not spot:
        raise Unavailable(f"Yahoo lists no options for {sym.id}")
    return spot, opts


def atm(opts: dict, spot: float) -> dict:
    """The at-the-money strike with its call and put."""
    calls, puts = {o["strike"]: o for o in opts["calls"]}, {o["strike"]: o for o in opts["puts"]}
    both = sorted(calls.keys() & puts.keys(), key=lambda k: abs(k - spot))
    if not both:
        raise Unavailable("this expiry has no strike with both a call and a put")
    return {"strike": both[0], "call": calls[both[0]], "put": puts[both[0]]}


def mid(option: dict) -> float:
    return (option["bid"] + option["ask"]) / 2 if option["bid"] > 0 and option["ask"] > 0 else option["last"]


async def omon(ctx, sym, arg):
    spot, opts = await chain(ctx, sym, arg)
    years, rate = calc.years_until(opts["expiry"], date.today()), await risk_free(ctx)
    money = atm(opts, spot)
    iv = (money["call"]["iv"] + money["put"]["iv"]) / 2
    move = mid(money["call"]) + mid(money["put"])
    volume = {side: sum(o["volume"] for o in opts[side]) for side in ("calls", "puts")}
    interest = {side: sum(o["oi"] for o in opts[side]) for side in ("calls", "puts")}
    puts = {o["strike"]: o for o in opts["puts"]}

    def half(o, call: bool) -> list:
        if not o:
            return [DASH] * 6
        g = calc.greeks(spot, o["strike"], years, rate, o["iv"], call)
        hot = "hot" if o["volume"] >= 500 and o["volume"] > 3 * o["oi"] else ""
        cells = [num(o["bid"]), num(o["ask"]), cell(big(o["volume"]), hot), big(o["oi"]), pct(o["iv"], 1, True),
                 num(g["delta"]) if g else DASH]
        return cells if call else cells[::-1]

    rows = [half(c, True) + [cell(num(c["strike"]), "hot" if c["strike"] == money["strike"] else "")] + half(puts.get(c["strike"]), False)
            for c in opts["calls"] if abs(c["strike"] / spot - 1) <= 0.15]
    unusual = sorted([(o, side) for side in ("calls", "puts") for o in opts[side] if o["volume"] >= 500 and o["volume"] > 3 * o["oi"]],
                     key=lambda x: -x[0]["volume"] * x[0]["last"])[:15]
    g = calc.greeks(spot, money["strike"], years, rate, iv, True)
    return screen(f"{sym.id}  OPTION MONITOR  {opts['expiry']}", [
        kv("SUMMARY (computed)", [
            ("Spot", num(spot)), ("Expiry", f"{opts['expiry']}  ({years * 365:.0f} days)"), ("At-the-money implied vol", pct(iv, 1, True)),
            ("Expected move by expiry", f"±{num(move)}  (±{move / spot * 100:.1f}%)"),
            ("Put / call volume", num(div(volume["puts"], volume["calls"]))), ("Put / call open interest", num(div(interest["puts"], interest["calls"]))),
            ("Max pain strike", num(calc.max_pain(opts["calls"], opts["puts"]))),
            ("ATM call greeks", f"Δ {g['delta']:.2f}  Γ {g['gamma']:.4f}  Θ {g['theta']:.3f}/day  V {g['vega']:.3f}/pt" if g else DASH),
            ("Other expiries", "  ".join(f"{n + 1}) {e}" for n, e in enumerate(opts["expiries"][:12])))]),
        table("CHAIN, STRIKES WITHIN 15% OF SPOT",
              ["C bid", "C ask", "C vol", "C OI", "C IV", "C Δ", "STRIKE", "P Δ", "P IV", "P OI", "P vol", "P ask", "P bid"], rows),
        table("UNUSUAL ACTIVITY (volume over 3× open interest)", ["Side", "Strike", "Last", "Volume", "Open interest", "Premium traded"],
              [[cell("CALL", "up") if side == "calls" else cell("PUT", "down"), num(o["strike"]), num(o["last"]), big(o["volume"]),
                big(o["oi"]), big(o["volume"] * o["last"] * 100)] for o, side in unusual]),
    ], f"Pick another expiry with a number: {sym.id} OMON 3. Greeks are Black-Scholes at Yahoo's implied vol; quotes are delayed.")


async def skew(ctx, sym, arg):
    spot, opts = await chain(ctx, sym, arg)
    years, rate = calc.years_until(opts["expiry"], date.today()), await risk_free(ctx)

    def near(side: str, target: float):
        """The option whose delta is closest to the target."""
        scored = [(abs(g["delta"] - target), o) for o in opts[side]
                  if o["iv"] > 0 and (g := calc.greeks(spot, o["strike"], years, rate, o["iv"], side == "calls"))]
        return min(scored, key=lambda x: x[0])[1] if scored else None

    call25, put25, money = near("calls", 0.25), near("puts", -0.25), atm(opts, spot)
    strikes = sorted({o["strike"] for o in opts["calls"] + opts["puts"] if abs(o["strike"] / spot - 1) <= 0.2})
    by = {side: {o["strike"]: o["iv"] * 100 for o in opts[side] if o["iv"] > 0.001} for side in ("calls", "puts")}
    term = []
    for n, expiry in enumerate(opts["expiries"][:8]):
        try:
            other = await run(ctx.src.options, sym.yahoo, n)
            m = atm(other, spot)
            term.append((expiry, (m["call"]["iv"] + m["put"]["iv"]) / 2 * 100))
        except Exception:
            continue  # one unreadable expiry must not lose the rest of the term structure
    atm_iv = (money["call"]["iv"] + money["put"]["iv"]) / 2
    return screen(f"{sym.id}  VOLATILITY SKEW  {opts['expiry']}", [
        kv("SKEW (computed)", [
            ("At-the-money implied vol", pct(atm_iv, 1, True)),
            ("25-delta put vol", pct(put25["iv"], 1, True) if put25 else DASH), ("25-delta call vol", pct(call25["iv"], 1, True) if call25 else DASH),
            ("25-delta risk reversal (call − put)", pct(call25["iv"] - put25["iv"], 1) if call25 and put25 else DASH),
            ("25-delta butterfly", pct((call25["iv"] + put25["iv"]) / 2 - atm_iv, 1) if call25 and put25 else DASH)]),
        {"title": "IMPLIED VOL BY STRIKE (%)", "line": {"labels": [num(k, 0) for k in strikes], "series": [
            {"name": "calls", "values": [by["calls"].get(k) for k in strikes]},
            {"name": "puts", "values": [by["puts"].get(k) for k in strikes]}]}},
        {"title": "TERM STRUCTURE: AT-THE-MONEY VOL BY EXPIRY (%)", "line": {"labels": [e for e, _ in term],
                                                                              "series": [{"name": "ATM vol", "values": [v for _, v in term]}]}},
    ], "A negative risk reversal means puts cost more than calls: the market pays for downside protection.")


async def hvg(ctx, sym, arg):
    days = (await closes(ctx, [sym], "2y")).get(sym.id)
    if not days or len(days) < 40:
        raise Unavailable(f"not enough price history for {sym.id}")
    px, order = series(days), sorted(days)
    rolling = [(d, calc.hist_vol(px[: n + 1], 30)) for n, d in enumerate(order)][30:][-250:]
    values = [v for _, v in rolling if v is not None]
    now = calc.hist_vol(px, 30)
    implied = None
    try:
        spot, opts = await chain(ctx, sym, "2")  # skip the nearest expiry: its vol is mostly noise
        money = atm(opts, spot)
        implied = (money["call"]["iv"] + money["put"]["iv"]) / 2
    except Exception:
        pass  # no options for this symbol: realised vol alone still answers
    return screen(f"{sym.id}  VOLATILITY", [
        kv("REALISED VOLATILITY, ANNUALISED (computed)", [(f"{n} days", pct(calc.hist_vol(px, n), 1, True)) for n in (10, 30, 60, 90, 252)] + [
            ("30-day vol, rank in the last year", pct(div(sum(v <= now for v in values), len(values)), 0, True) if now else DASH),
            ("Implied vol (at the money)", pct(implied, 1, True)),
            ("Implied minus realised 30-day", pct(implied - now, 1) if implied and now else DASH)]),
        {"title": "30-DAY REALISED VOL (%)", "line": {"labels": [date.fromordinal(d + 719163).isoformat() for d, _ in rolling],
                                                      "series": [{"name": "HV30", "values": [v * 100 if v else None for _, v in rolling]}]}},
    ], "Implied above realised: options are priced for more movement than the stock has been making.")


# ---------------------------------------------------------------- risk and value
BENCHMARK = "^GSPC"


async def beta(ctx, sym, arg):
    data = await closes(ctx, [sym, resolve(BENCHMARK)], "2y")
    if sym.id not in data or BENCHMARK not in data:
        raise Unavailable(f"no price history for {sym.id}")
    a, m = calc.aligned(data[sym.id], data[BENCHMARK])
    out = calc.beta(calc.returns(a), calc.returns(m))
    if not out:
        raise Unavailable("not enough shared trading days to fit a beta")
    return screen(f"{sym.id}  BETA vs S&P 500", [kv("REGRESSION ON DAILY RETURNS, 2 YEARS (computed)", [
        ("Raw beta", num(out["beta"], 3)), ("Adjusted beta", num(out["adjusted"], 3)), ("Alpha, a year", pct(out["alpha"])),
        ("R²", num(out["r2"], 3)), ("Correlation", num(out["corr"], 3)), ("Days", str(out["n"]))])],
        "Adjusted beta = 0.67 × raw + 0.33, the same adjustment Bloomberg shows.")


async def watch_returns(ctx, extra: list[Symbol] = (), cap: int = 16) -> tuple[list[Symbol], dict[str, dict[int, float]]]:
    symbols = list({s.id: s for s in [*extra, *ctx.watchlist()]}.values())[:cap]
    data = await closes(ctx, symbols, "1y")
    return [s for s in symbols if len(data.get(s.id, {})) > 40], data


async def corr(ctx, sym, arg):
    symbols, data = await watch_returns(ctx, [sym] if sym else [])
    if len(symbols) < 2:
        raise Unavailable("correlation needs at least two symbols with history in the watchlist")
    short = [s.id.replace("-USD", "") for s in symbols]

    def one(a: Symbol, b: Symbol):
        if a.id == b.id:
            return cell("1.00", "dim")
        x, y = calc.aligned(data[a.id], data[b.id])
        c = calc.correlation(calc.returns(x), calc.returns(y))
        return DASH if c is None else cell(f"{c:.2f}", "up" if c >= 0.5 else "down" if c < 0 else "")
    return screen("CORRELATION MATRIX", [table("DAILY RETURNS, 1 YEAR (computed)", [""] + short,
                                               [[cell(short[n], s=a.id)] + [one(a, b) for b in symbols] for n, a in enumerate(symbols)])],
                  "Watchlist symbols. Green: move together (0.5 and up). Red: move against each other.")


async def tra(ctx, sym, arg):
    data = await closes(ctx, [sym, resolve(BENCHMARK)], "5y")
    if sym.id not in data:
        raise Unavailable(f"no price history for {sym.id}")

    def table_for(days: dict[int, float]) -> dict:
        order = sorted(days)
        px = [days[d] for d in order]
        out = periods_back(px, [year_of(d) for d in order])
        for label, n in (("3Y", 756), ("5Y", len(px) - 1)):
            out[label] = px[-1] / px[-1 - n] - 1 if len(px) > n and n > 700 else None
        return out
    mine, market = table_for(data[sym.id]), table_for(data.get(BENCHMARK, data[sym.id]))
    years = {"1Y": 1, "3Y": 3, "5Y": 5}
    rows = [[label, pct(mine[label]), pct(calc.cagr(1, 1 + mine[label], years[label])) if label in years and mine[label] is not None and mine[label] > -1 else DASH,
             pct(market[label]), pct(mine[label] - market[label]) if None not in (mine[label], market[label]) else DASH] for label in mine]
    return screen(f"{sym.id}  TOTAL RETURN", [table("DIVIDENDS REINVESTED (computed from adjusted prices)",
                                                    ["Period", sym.id, "A year", "S&P 500", "Excess"], rows)])


async def port(ctx, sym, arg):
    symbols, data = await watch_returns(ctx, [resolve(BENCHMARK)], 26)
    rate = await risk_free(ctx)
    market = data.get(BENCHMARK, {})
    rows, daily = [], {}
    for s in symbols:
        if s.id == BENCHMARK:
            continue
        px = series(data[s.id])
        rets = calc.returns(px)
        for d, r in zip(sorted(data[s.id])[1:], rets):
            daily.setdefault(d, []).append(r)
        a, m = calc.aligned(data[s.id], market)
        b = calc.beta(calc.returns(a), calc.returns(m))
        rows.append([cell(s.id.replace("-USD", ""), s=s.id), pct(px[-1] / px[0] - 1), pct(statistics.stdev(rets) * calc.TRADING_DAYS ** 0.5, 1, True),
                     num(calc.sharpe(rets, rate)), pct(calc.max_drawdown(px), 1), pct(calc.var95(rets)), num(b["beta"]) if b else DASH])
    if not rows:
        raise Unavailable("the watchlist has no symbol with a year of history")
    book = [statistics.mean(daily[d]) for d in sorted(daily)]
    level, curve = 1.0, []
    for r in book:
        level *= 1 + r
        curve.append(level)
    return screen("PORTFOLIO RISK: WATCHLIST, EQUAL WEIGHT", [
        kv("PORTFOLIO, 1 YEAR (computed)", [("Return", pct(curve[-1] - 1)), ("Volatility", pct(statistics.stdev(book) * calc.TRADING_DAYS ** 0.5, 1, True)),
                                            ("Sharpe ratio", num(calc.sharpe(book, rate))), ("Worst drawdown", pct(calc.max_drawdown(curve), 1)),
                                            ("1-day value at risk, 95%", pct(calc.var95(book))), ("Risk-free rate used", pct(rate, 2, True))]),
        table("BY SYMBOL", ["Symbol", "1Y return", "Volatility", "Sharpe", "Max drawdown", "VaR 95% 1d", "Beta"], rows),
    ], "Equal weights, rebalanced daily. VaR is historical: the daily loss that was exceeded on 5% of days.")


async def value_inputs(ctx, sym, fn: str) -> dict:
    need_stock(sym, fn)
    i, stmts, rf = await asyncio.gather(company(ctx, sym), statements(ctx, sym, fn), risk_free(ctx, "^TNX"))
    debt, cap = i.get("totalDebt") or 0.0, i.get("marketCap")
    if not cap:
        raise Unavailable(f"no market value for {sym.id}")
    interest = abs(line(stmts, "income", "Interest Expense") or 0)
    cost_debt = min(max(interest / debt, rf), 0.15) if debt and interest else rf + 0.015
    tax = min(max(line(stmts, "income", "Tax Rate For Calcs") or 0.21, 0), 0.4)
    return {"info": i, "stmts": stmts, "rf": rf, "debt": debt, "cap": cap, "cost_debt": cost_debt, "tax": tax,
            "beta": i.get("beta") or 1.0, "w": calc.wacc(cap, debt, i.get("beta") or 1.0, rf, cost_debt, tax)}


async def wacc(ctx, sym, arg):
    v = await value_inputs(ctx, sym, "WACC")
    w = v["w"]
    return screen(f"{sym.id}  WEIGHTED AVERAGE COST OF CAPITAL", [kv("WACC (computed)", [
        ("Risk-free rate (10-year Treasury)", pct(v["rf"], 2, True)), ("Beta", num(v["beta"])), ("Equity risk premium (assumed)", "5.00%"),
        ("Cost of equity", pct(w["cost_equity"], 2, True)), ("Cost of debt before tax", pct(v["cost_debt"], 2, True)),
        ("Tax rate", pct(v["tax"], 1, True)), ("Cost of debt after tax", pct(w["cost_debt_after_tax"], 2, True)),
        ("Market value of equity", big(v["cap"])), ("Debt", big(v["debt"])), ("Weight of equity", pct(w["weight_equity"], 1, True)),
        ("Weight of debt", pct(w["weight_debt"], 1, True)), ("WACC", cell(f"{w['wacc'] * 100:.2f}%", "hot"))])],
        "Cost of equity is CAPM. Cost of debt is interest paid over total debt.")


async def dcf(ctx, sym, arg):
    v = await value_inputs(ctx, sym, "DCF")
    i, stmts = v["info"], v["stmts"]
    fcf = i.get("freeCashflow") or line(stmts, "cash", "Free Cash Flow")
    shares, px = i.get("sharesOutstanding"), spot_of(i)
    if not fcf or fcf <= 0 or not shares or not px:
        raise Unavailable(f"{sym.id} has no positive free cash flow to discount")
    revenue = [r for r in stmts["income"]["rows"].get("Total Revenue", []) if r]
    past = calc.cagr(revenue[-1], revenue[0], len(revenue) - 1) if len(revenue) > 1 else None
    growth = min(max(past or 0.03, 0.0), 0.20)
    rate, net_debt, terminal = max(v["w"]["wacc"], 0.06), v["debt"] - (i.get("totalCash") or 0), 0.025
    fair = calc.dcf(fcf, growth, rate, terminal, net_debt, shares)
    steps = [-0.04, -0.02, 0, 0.02, 0.04]
    grid = [[pct(rate + dr, 1, True)] + [cell(num(f), tone(f - px)) if (f := calc.dcf(fcf, max(growth + dg, 0), rate + dr, terminal, net_debt, shares)) else DASH
                                         for dg in steps] for dr in (-0.01, 0, 0.01, 0.02)]
    return screen(f"{sym.id}  DISCOUNTED CASH FLOW", [
        kv("FAIR VALUE (computed)", [
            ("Free cash flow, last 12 months", big(fcf)), ("Growth for 5 years", pct(growth, 1, True)),
            ("  from past revenue growth of", pct(past, 1, True)), ("Discount rate (WACC, floor 6%)", pct(rate, 2, True)),
            ("Growth after year 5", pct(terminal, 1, True)), ("Net debt", big(net_debt)), ("Shares", big(shares)),
            ("Fair value per share", cell(num(fair), "hot")), ("Price", num(px)), ("Upside", pct(change(fair, px)))]),
        table("FAIR VALUE BY DISCOUNT RATE (down) AND GROWTH (across)", ["Rate \\ growth"] + [pct(max(growth + dg, 0), 1, True) for dg in steps], grid),
    ], "A model, not a forecast: growth is capped at 20% and taken from past revenue. Green cells are above today's price.")


async def drsk(ctx, sym, arg):
    v = await value_inputs(ctx, sym, "DRSK")
    stmts, i = v["stmts"], v["info"]

    def year(n: int) -> dict:
        def g(t, name):
            return line(stmts, t, name, n)
        return {"net_income": g("income", "Net Income"), "assets": g("balance", "Total Assets"), "cfo": g("cash", "Operating Cash Flow"),
                "long_debt": g("balance", "Long Term Debt") or 0.0, "current_assets": g("balance", "Current Assets"),
                "current_liabilities": g("balance", "Current Liabilities"), "shares": g("balance", "Ordinary Shares Number"),
                "gross_profit": g("income", "Gross Profit"), "revenue": g("income", "Total Revenue")}
    tests = calc.piotroski(year(0), year(1))
    z = calc.altman_z(line(stmts, "balance", "Working Capital"), line(stmts, "balance", "Retained Earnings"), line(stmts, "income", "EBIT"),
                      v["cap"], line(stmts, "balance", "Total Liabilities Net Minority Interest"), line(stmts, "income", "Total Revenue"),
                      line(stmts, "balance", "Total Assets"))
    days = (await closes(ctx, [sym], "2y")).get(sym.id) or {}
    vol = calc.hist_vol(series(days), 252) if len(days) > 252 else None
    m = calc.merton(v["cap"], vol, v["debt"], v["rf"]) if vol and v["debt"] else None
    ebit, interest = line(stmts, "income", "EBIT"), abs(line(stmts, "income", "Interest Expense") or 0)
    zone = DASH if z is None else cell("SAFE", "up") if z > 2.99 else cell("DISTRESS", "down") if z < 1.81 else cell("GREY ZONE", "hot")
    score = sum(1 for _, ok in tests if ok)
    return screen(f"{sym.id}  DEFAULT RISK", [
        kv("CREDIT HEALTH (computed)", [
            ("Altman Z-score", num(z)), ("Z-score zone", zone), ("Piotroski F-score", f"{score} / 9"),
            ("Interest cover (EBIT / interest)", num(div(ebit, interest), 1)), ("Net debt / EBITDA", num(div(v["debt"] - (i.get("totalCash") or 0), i.get("ebitda")))),
            ("Equity volatility, 1 year", pct(vol, 1, True)), ("Merton distance to default", num(m["distance"]) + " σ" if m else DASH),
            ("Merton 1-year default probability", (f"{m['default_prob'] * 100:.4f}%" if m["default_prob"] >= 1e-6 else "< 0.0001%") if m else
             ("no debt" if not v["debt"] else DASH))]),
        table("PIOTROSKI TESTS", ["Test", "Result"], [[name, DASH if ok is None else cell("PASS", "up") if ok else cell("FAIL", "down")] for name, ok in tests]),
    ], "Z above 2.99 is safe, below 1.81 is distress (built for manufacturers; banks read wrong). "
       "Merton treats all debt as due in one year, which overstates risk for long-dated borrowers.")


async def seag(ctx, sym, arg):
    data = (await ctx.bars([sym], "10y", "1mo")).get(sym.id) or []
    monthly = [(datetime.fromtimestamp(c.ts + 86400 * 3).year, datetime.fromtimestamp(c.ts + 86400 * 3).month, c.close) for c in data]
    stats = calc.seasonality(monthly)
    if not any(s["n"] for s in stats):
        raise Unavailable(f"not enough monthly history for {sym.id}")
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return screen(f"{sym.id}  SEASONALITY", [
        table("AVERAGE RETURN BY MONTH, 10 YEARS (computed)", ["Month", "Average", "Up years", "Years"],
              [[names[s["month"] - 1], pct(s["mean"]), f"{s['up']} / {s['n']}", str(s["n"])] for s in stats]),
        {"title": "AVERAGE MONTHLY RETURN (%)", "line": {"labels": names, "series": [
            {"name": "average", "values": [s["mean"] * 100 if s["mean"] is not None else None for s in stats]}]}}])


async def rv(ctx, sym, arg):
    need_stock(sym, "RV")
    names = await run(ctx.src.peers, sym.yahoo)
    infos = await asyncio.gather(*(run(ctx.src.info, n) for n in names), return_exceptions=True)
    fields = [("Mkt cap", "marketCap", big), ("P/E", "trailingPE", num), ("Fwd P/E", "forwardPE", num), ("P/S", "priceToSalesTrailing12Months", num),
              ("P/B", "priceToBook", num), ("EV/EBITDA", "enterpriseToEbitda", num),
              ("Net margin", "profitMargins", lambda x: pct(x, 1, True)), ("ROE", "returnOnEquity", lambda x: pct(x, 1, True)),
              ("Rev growth", "revenueGrowth", pct)]
    peers = [(n, i) for n, i in zip(names, infos) if isinstance(i, dict) and i.get("marketCap")]
    if not peers:
        raise Unavailable(f"no peer data for {sym.id}")
    rows = [[cell(n, "hot" if n == sym.yahoo else "", s=n)] + [fmt(i.get(key)) for _, key, fmt in fields] for n, i in peers]

    def median(key):
        values = [i[key] for _, i in peers if isinstance(i.get(key), (int, float))]
        return statistics.median(values) if values else None
    mine = peers[0][1]
    rows.append([cell("MEDIAN", "dim")] + [fmt(median(key)) for _, key, fmt in fields])
    rows.append([cell(f"{sym.id} vs median", "dim"), DASH] + [pct(change(mine.get(key), median(key)), 0)
                                                               for _, key, _ in fields[1:6]] + [DASH] * 3)
    return screen(f"{sym.id}  RELATIVE VALUATION", [table(f"LARGEST IN {str(mine.get('industry', 'INDUSTRY')).upper()}",
                                                          ["Symbol"] + [f[0] for f in fields], rows)],
                  "Peers are the largest companies in Yahoo's industry group. Last row: premium (+) or discount (−) to the peer median.")


async def tech(ctx, sym, arg):
    data = (await ctx.bars([sym], "2y", "1d")).get(sym.id) or []
    if len(data) < 60:
        raise Unavailable(f"not enough price history for {sym.id}")
    px = [c.close for c in data]
    last = px[-1]
    signals = []

    def average(n):
        return statistics.mean(px[-n:]) if len(px) >= n else None

    def verdict(label, value, bullish: bool | None, shown: str):
        signals.append([label, shown, DASH if bullish is None else cell("BULLISH", "up") if bullish else cell("BEARISH", "down")])
    for n in (20, 50, 200):
        if (avg := average(n)) is not None:
            verdict(f"Price vs {n}-day average", avg, last > avg, f"{num(avg)}  ({(last / avg - 1) * 100:+.1f}%)")
    if average(200) is not None:
        verdict("50-day vs 200-day average", None, average(50) > average(200), "golden cross" if average(50) > average(200) else "death cross")
    r = calc.rsi(px)
    verdict("RSI 14", r, None if r is None or 30 <= r <= 70 else r < 30, f"{num(r, 1)}  ({'oversold' if r < 30 else 'overbought' if r > 70 else 'neutral'})")
    if m := calc.macd(px):
        verdict("MACD vs signal", m, m[0] > m[1], f"{m[0]:.3f} vs {m[1]:.3f}")
    mean20, sd20 = average(20), statistics.stdev(px[-20:])
    band = (last - (mean20 - 2 * sd20)) / (4 * sd20) if sd20 else None
    verdict("Bollinger position (0 low band, 1 high band)", band, None if band is None or 0 <= band <= 1 else band < 0, num(band))
    ranges = [max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)) for p, c in zip(data[-15:], data[-14:])]
    year = px[-252:]
    bulls, bears = sum(s[2] != DASH and s[2]["c"] == "up" for s in signals), sum(s[2] != DASH and s[2]["c"] == "down" for s in signals)
    return screen(f"{sym.id}  TECHNICAL SUMMARY", [
        kv("SUMMARY (computed)", [("Last", num(last)), ("Signals", f"{bulls} bullish, {bears} bearish"),
                                  ("Average true range, 14 days", f"{num(statistics.mean(ranges))}  ({statistics.mean(ranges) / last * 100:.1f}%)"),
                                  ("Below 52-week high", pct(last / max(year) - 1, 1)), ("Above 52-week low", pct(last / min(year) - 1, 1))]),
        table("SIGNALS", ["Indicator", "Value", "Reads"], signals)], "Daily bars. Signals are textbook rules, not advice.")


# ---------------------------------------------------------------- markets
async def monitor(ctx, title: str, groups: dict[str, list[tuple[str, str]]], yields: bool = False) -> dict:
    """A table of instruments by group with their moves. `yields` shows changes in basis points."""
    wanted = {ticker: resolve(ticker) for rows in groups.values() for _, ticker in rows}
    data = await closes(ctx, [s for s in wanted.values() if s], "1y")
    if not data:
        raise ConnectionError("Yahoo returned no prices")
    labels = ["1D", "5D", "1M", "YTD", "1Y"]
    sections = []
    for group, rows in groups.items():
        out = []
        for name, ticker in rows:
            sym = wanted[ticker]
            days = data.get(sym.id) if sym else None
            if not days:
                out.append([name] + [DASH] * 6)
                continue
            order = sorted(days)
            px = [days[d] for d in order]
            if yields:
                years = [year_of(d) for d in order]
                base = {"1D": px[-2] if len(px) > 1 else None, "5D": px[-6] if len(px) > 5 else None, "1M": px[-22] if len(px) > 21 else None,
                        "YTD": next((v for v, y in zip(reversed(px), reversed(years)) if y < years[-1]), None), "1Y": px[0] if len(px) > 200 else None}
                moves = [cell(f"{(px[-1] - base[k]) * 100:+.0f}bp", tone(px[-1] - base[k])) if base[k] is not None else DASH for k in labels]
            else:
                back = periods_back(px, [year_of(d) for d in order])
                moves = [pct(back[k]) for k in labels]
            out.append([cell(name, s=sym.id), num(px[-1], 3 if yields else 4 if px[-1] < 10 else 2)] + moves)
        sections.append(table(group, ["Name", "Yield %" if yields else "Last"] + labels, out))
    return screen(title, sections, "Yahoo Finance daily closes; today's row moves during the session.")


WEI = {
    "AMERICAS": [("S&P 500", "^GSPC"), ("Dow Jones", "^DJI"), ("Nasdaq", "^IXIC"), ("Russell 2000", "^RUT"), ("Canada TSX", "^GSPTSE"),
                 ("Brazil Bovespa", "^BVSP"), ("Mexico IPC", "^MXX")],
    "EUROPE": [("Euro Stoxx 50", "^STOXX50E"), ("UK FTSE 100", "^FTSE"), ("Germany DAX", "^GDAXI"), ("France CAC 40", "^FCHI"),
               ("Italy FTSE MIB", "FTSEMIB.MI"), ("Spain IBEX 35", "^IBEX"), ("Switzerland SMI", "^SSMI"), ("Netherlands AEX", "^AEX")],
    "ASIA / PACIFIC": [("Japan Nikkei 225", "^N225"), ("Hong Kong Hang Seng", "^HSI"), ("Shanghai Composite", "000001.SS"),
                       ("India Nifty 50", "^NSEI"), ("Korea KOSPI", "^KS11"), ("Taiwan", "^TWII"), ("Australia ASX 200", "^AXJO")],
    "VOLATILITY": [("VIX", "^VIX"), ("Nasdaq vol", "^VXN")],
}
WCR = {
    "MAJORS": [("EUR/USD", "EURUSD"), ("USD/JPY", "USDJPY"), ("GBP/USD", "GBPUSD"), ("USD/CHF", "USDCHF"), ("AUD/USD", "AUDUSD"),
               ("USD/CAD", "USDCAD"), ("NZD/USD", "NZDUSD"), ("Dollar index", "DX-Y.NYB")],
    "CROSSES": [("EUR/GBP", "EURGBP"), ("EUR/JPY", "EURJPY"), ("EUR/CHF", "EURCHF"), ("GBP/JPY", "GBPJPY")],
    "EMERGING": [("USD/CNY", "USDCNY"), ("USD/INR", "USDINR"), ("USD/MXN", "USDMXN"), ("USD/BRL", "USDBRL"), ("USD/TRY", "USDTRY"),
                 ("USD/ZAR", "USDZAR"), ("USD/KRW", "USDKRW"), ("USD/PLN", "USDPLN")],
}
GLCO = {
    "ENERGY": [("WTI crude", "CL=F"), ("Brent crude", "BZ=F"), ("Natural gas", "NG=F"), ("Gasoline", "RB=F"), ("Heating oil", "HO=F")],
    "METALS": [("Gold", "GC=F"), ("Silver", "SI=F"), ("Copper", "HG=F"), ("Platinum", "PL=F"), ("Palladium", "PA=F")],
    "AGRICULTURE": [("Corn", "ZC=F"), ("Wheat", "ZW=F"), ("Soybeans", "ZS=F"), ("Coffee", "KC=F"), ("Sugar", "SB=F"), ("Cotton", "CT=F"),
                    ("Cocoa", "CC=F"), ("Live cattle", "LE=F")],
}
CRYP = {"CRYPTO": [(c, c) for c in ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "LTC", "DOT", "TRX")]}
IMAP = {
    "US SECTORS (SPDR funds)": [("Technology", "XLK"), ("Financials", "XLF"), ("Health care", "XLV"), ("Energy", "XLE"), ("Industrials", "XLI"),
                                ("Consumer discretionary", "XLY"), ("Consumer staples", "XLP"), ("Utilities", "XLU"), ("Materials", "XLB"),
                                ("Real estate", "XLRE"), ("Communications", "XLC")],
    "STYLES AND THEMES": [("S&P 500", "SPY"), ("Equal weight", "RSP"), ("Small caps", "IWM"), ("Growth", "IWF"), ("Value", "IWD"),
                          ("Semiconductors", "SMH"), ("Banks", "KBE"), ("Defence", "ITA"), ("Airlines", "JETS"), ("Homebuilders", "XHB")],
    "BONDS AND CREDIT": [("Treasuries 20y+", "TLT"), ("Treasuries 7-10y", "IEF"), ("Investment grade", "LQD"), ("High yield", "HYG"),
                         ("Emerging debt", "EMB"), ("TIPS", "TIP")],
}
WB = {"US TREASURY YIELDS": [("13-week bill", "^IRX"), ("5-year", "^FVX"), ("10-year", "^TNX"), ("30-year", "^TYX")]}


def monitor_fn(title: str, groups: dict, yields: bool = False):
    async def build(ctx, sym, arg):
        return await monitor(ctx, title, groups, yields)
    return build


async def fxc(ctx, sym, arg):
    pairs = {"EUR": ("EURUSD", False), "JPY": ("USDJPY", True), "GBP": ("GBPUSD", False), "CHF": ("USDCHF", True),
             "CAD": ("USDCAD", True), "AUD": ("AUDUSD", False), "NZD": ("NZDUSD", False), "CNY": ("USDCNY", True)}
    data = await closes(ctx, [resolve(p) for p, _ in pairs.values()], "5d")
    usd = {"USD": 1.0}  # dollars one unit of each currency buys
    for code, (pair, inverted) in pairs.items():
        if days := data.get(pair):
            usd[code] = 1 / series(days)[-1] if inverted else series(days)[-1]
    if len(usd) < 3:
        raise ConnectionError("Yahoo returned no currency prices")
    codes = list(usd)
    rows = [[cell(a, "hot")] + [cell("1", "dim") if a == b else num(usd[a] / usd[b], 4 if usd[a] / usd[b] < 20 else 2) for b in codes] for a in codes]
    return screen("FX CROSS RATES", [table("ONE UNIT OF THE ROW CURRENCY, IN THE COLUMN CURRENCY (computed)", [""] + codes, rows)],
                  "Crosses are computed from each currency's dollar rate.")


_memo: dict = {}


async def remember(key: str, ttl: float, make: Callable[[], Awaitable]):
    """Slow public sources (Treasury, FRED, the calendar) are asked at most once per `ttl` seconds."""
    hit = _memo.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    value = await make()
    _memo[key] = (time.time(), value)
    return value


def http() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=45, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (terminal)"})


async def gc(ctx, sym, arg):
    async def fetch():
        async with http() as client:
            return await econ.curve(client, date.today())
    rows = await remember("curve", 3600, fetch)
    if not rows:
        raise ConnectionError("the Treasury returned no yield curve")
    (day, now), month, year = rows[-1], rows[max(len(rows) - 22, 0)][1], rows[max(len(rows) - 251, 0)][1]
    maturities = list(now)

    def spread(long: str, short: str):
        if long not in now or short not in now:
            return DASH
        s = now[long] - now[short]
        return cell(f"{s * 100:+.0f}bp" + ("  INVERTED" if s < 0 else ""), "down" if s < 0 else "up")
    return screen(f"US TREASURY YIELD CURVE  {day}", [
        kv("SPREADS (computed)", [("10-year minus 2-year", spread("10 Yr", "2 Yr")), ("10-year minus 3-month", spread("10 Yr", "3 Mo")),
                                  ("30-year minus 5-year", spread("30 Yr", "5 Yr"))]),
        {"title": "CURVE (%)", "line": {"labels": maturities, "series": [
            {"name": "now", "values": [now.get(m) for m in maturities]}, {"name": "1 month ago", "values": [month.get(m) for m in maturities]},
            {"name": "1 year ago", "values": [year.get(m) for m in maturities]}]}},
        table("PAR YIELDS", ["Maturity", "Now", "1 month ago", "1 year ago", "Change on month", "Change on year"],
              [[m, num(now[m]), num(month.get(m)), num(year.get(m)),
                cell(f"{(now[m] - month[m]) * 100:+.0f}bp", tone(now[m] - month[m])) if m in month else DASH,
                cell(f"{(now[m] - year[m]) * 100:+.0f}bp", tone(now[m] - year[m])) if m in year else DASH] for m in maturities]),
    ], "Source: US Treasury daily par yield curve. An inverted curve has preceded most US recessions.")


MONTH_CODES = "FGHJKMNQUVXZ"


def fed_contracts(today: date, count: int = 14) -> list[tuple[str, str]]:
    """[(month label, Yahoo ticker)] for fed funds futures from this month on."""
    out = []
    for n in range(count):
        year, month = today.year + (today.month - 1 + n) // 12, (today.month - 1 + n) % 12 + 1
        out.append((f"{year}-{month:02d}", f"ZQ{MONTH_CODES[month - 1]}{year % 100:02d}.CBT"))
    return out


async def wirp(ctx, sym, arg):
    contracts = fed_contracts(date.today())
    data = await closes(ctx, [Symbol(t, "future", t) for _, t in contracts], "5d")
    priced = [(label, series(data[t])[-1]) for label, t in contracts if data.get(t)]
    path = calc.fed_path(priced)
    if len(path) < 2:
        raise ConnectionError("Yahoo returned no fed funds futures")

    def words(moves: float) -> str:
        return "no change" if abs(moves) < 0.05 else f"{abs(moves):.1f} {'cut' if moves < 0 else 'hike'}{'s' if abs(moves) >= 1.5 else ''}"
    return screen("FED RATE EXPECTATIONS", [
        kv("PRICED BY FED FUNDS FUTURES (computed)", [("Rate now (this month's contract)", f"{path[0]['rate']:.3f}%"),
                                                      (f"By {path[-1]['month']}", cell(words(path[-1]["moves"]), tone(path[-1]["moves"]))),
                                                      ("Lowest point on the path", f"{min(p['rate'] for p in path):.3f}%")]),
        table("IMPLIED AVERAGE RATE BY MONTH", ["Month", "Implied rate", "Change from now", "25bp moves priced"],
              [[p["month"], f"{p['rate']:.3f}%", cell(f"{p['change_bp']:+.0f}bp", tone(p["change_bp"])), words(p["moves"])] for p in path]),
        {"title": "IMPLIED FED FUNDS RATE (%)", "line": {"labels": [p["month"] for p in path], "series": [
            {"name": "implied rate", "values": [p["rate"] for p in path]}]}},
    ], "Each contract settles at 100 minus the month's average fed funds rate. These are month averages, not per-meeting odds.")


async def most(ctx, sym, arg):
    names = {"day_gainers": "GAINERS", "day_losers": "LOSERS", "most_actives": "MOST ACTIVE"}
    results = await asyncio.gather(*(run(ctx.src.screen, n) for n in names), return_exceptions=True)
    sections = []
    for title, quotes in zip(names.values(), results):
        if isinstance(quotes, Exception):
            continue
        sections.append(table(title, ["Symbol", "Name", "Last", "Change", "Volume", "Mkt cap"], [
            [cell(q.get("symbol", ""), s=q.get("symbol", "")), str(q.get("shortName") or "")[:40], num(q.get("regularMarketPrice")),
             pct(div(q.get("regularMarketChangePercent"), 100)), big(q.get("regularMarketVolume")), big(q.get("marketCap"))] for q in quotes]))
    if not any(sections):
        raise ConnectionError("Yahoo returned no screens")
    return screen("MOVERS: US STOCKS", sections)


async def eco(ctx, sym, arg):
    async def fetch():
        async with http() as client:
            return await econ.calendar(client)
    events = await remember("calendar", 1800, fetch)
    colours = {"High": "down", "Medium": "hot", "Low": "dim", "Holiday": "dim"}
    now = time.time()
    rows = [[cell(datetime.fromtimestamp(e["ts"]).strftime("%a %d %H:%M"), "dim" if e["ts"] < now else ""), e["currency"],
             cell(e["impact"].upper(), colours.get(e["impact"], "")), e["title"], e["forecast"] or DASH, e["previous"] or DASH]
            for e in events if arg.upper() in ("", e["currency"]) or (arg.upper() == "HIGH" and e["impact"] == "High")]
    return screen("ECONOMIC CALENDAR: THIS WEEK", [table("EVENTS, YOUR LOCAL TIME", ["When", "Ccy", "Impact", "Event", "Forecast", "Previous"], rows)],
                  "Filter with ECO USD or ECO HIGH. Source: Forex Factory weekly calendar.")


async def ecst(ctx, sym, arg):
    if not ctx.config.fred_key:
        raise Unavailable("Set FRED_KEY in .env — free key at fred.stlouisfed.org/docs/api/api_key.html")

    async def fetch():
        gate = asyncio.Semaphore(4)
        async with http() as client:
            async def one(series_id):
                async with gate:
                    return await econ.fred(client, ctx.config.fred_key, series_id)
            return await asyncio.gather(*(one(s) for s in econ.FRED_SERIES), return_exceptions=True)
    rows = []
    for (label, how), obs in zip(econ.FRED_SERIES.values(), await remember("fred", 3600, fetch)):
        if isinstance(obs, Exception) or len(obs) < 2:
            rows.append([label] + [DASH] * 4)
            continue
        (day, last), (_, before) = obs[-1], obs[-2]
        year_ago = next((v for d, v in reversed(obs) if d <= f"{int(day[:4]) - 1}{day[4:]}"), None)
        if how == "yoy":
            day_before = obs[-2][0]
            before_ago = next((v for d, v in reversed(obs) if d <= f"{int(day_before[:4]) - 1}{day_before[4:]}"), None)
            rows.append([label, day, pct(change(last, year_ago)), pct(change(before, before_ago)), "on a year"])
        elif how == "chg":
            rows.append([label, day, cell(f"{last - before:+,.0f}", tone(last - before)), num(before - obs[-3][1], 0) if len(obs) > 2 else DASH, "on the month"])
        else:
            rows.append([label, day, num(last), num(before), cell(f"{last - before:+,.2f}", tone(last - before))])
    return screen("US ECONOMIC STATISTICS", [table("LATEST READINGS", ["Indicator", "Date", "Latest", "Previous", "Change"], rows)],
                  "Source: FRED, Federal Reserve Bank of St. Louis.")


async def evts(ctx, sym, arg):
    stocks = [s for s in ctx.watchlist() if s.asset == "stock"][:30]
    infos = await asyncio.gather(*(run(ctx.src.info, s.yahoo) for s in stocks), return_exceptions=True)
    now, events = time.time(), []
    for s, i in zip(stocks, infos):
        if not isinstance(i, dict):
            continue
        for label, key in (("Earnings", "earningsTimestampStart"), ("Ex-dividend", "exDividendDate"), ("Dividend paid", "dividendDate")):
            if (ts := i.get(key)) and ts > now - 86400:
                events.append((ts, [stamp(ts), cell(s.id, s=s.id), label, "estimate" if key.startswith("earnings") and i.get("isEarningsDateEstimate") else ""]))
    return screen("EVENTS: WATCHLIST", [table("COMING UP", ["Date", "Symbol", "Event", ""], [row for _, row in sorted(events, key=lambda e: e[0])])],
                  "" if events else "No dated events for the stocks in the watchlist.")


async def help_(ctx, sym, arg):
    sections = []
    for group in dict.fromkeys(f.group for f in FUNCTIONS.values()):
        sections.append(table(group.upper(), ["Type", "Shows"], [
            [cell(("AAPL " if f.symbol else "") + f.name, "hot", f=f.name), f.about] for f in FUNCTIONS.values() if f.group == group]))
    sections.append(table("SCREENS", ["Type", "Shows"], [[cell(name, "hot", f=name), about] for name, about in PAGE_FUNCTIONS.items()]))
    return screen("FUNCTIONS", sections, "Type a symbol then a function (AAPL DES), or a function alone to use the symbol on the chart. "
                                         "Bloomberg-style endings (US EQUITY, CURNCY, GO) are ignored. Esc returns to the dashboard.")


FUNCTIONS = {f.name: f for f in [
    Fn("DES", "Company description, valuation and key figures", "Company", des, True),
    Fn("FA", "Financial statements with computed ratios", "Company", fa, True),
    Fn("RV", "Relative valuation against industry peers", "Company", rv, True),
    Fn("ANR", "Analyst ratings, targets and rating changes", "Company", anr, True),
    Fn("ERN", "Earnings dates, estimates and surprises", "Company", ern, True),
    Fn("DVD", "Dividend history and growth", "Company", dvd, True),
    Fn("HDS", "Holders: institutions and insider trades", "Company", hds, True),
    Fn("SI", "Short interest", "Company", si, True),
    Fn("CN", "News tagged with the symbol", "Company", cn, True),
    Fn("DCF", "Discounted cash flow fair value, with sensitivity", "Valuation and credit", dcf, True),
    Fn("WACC", "Weighted average cost of capital", "Valuation and credit", wacc, True),
    Fn("DRSK", "Default risk: Altman Z, Piotroski F, Merton model", "Valuation and credit", drsk, True),
    Fn("HP", "Historical prices table", "Price and risk", hp, True),
    Fn("TRA", "Total return against the S&P 500", "Price and risk", tra, True),
    Fn("BETA", "Beta, alpha and correlation against the S&P 500", "Price and risk", beta, True),
    Fn("HVG", "Realised against implied volatility", "Price and risk", hvg, True),
    Fn("TECH", "Technical indicator summary", "Price and risk", tech, True),
    Fn("SEAG", "Seasonality: average return by month", "Price and risk", seag, True),
    Fn("OMON", "Option chain with greeks, max pain, unusual activity", "Options", omon, True),
    Fn("SKEW", "Volatility skew and term structure", "Options", skew, True),
    Fn("CORR", "Correlation matrix of the watchlist", "Portfolio", corr),
    Fn("PORT", "Watchlist risk: volatility, Sharpe, drawdown, VaR", "Portfolio", port),
    Fn("EVTS", "Earnings and dividend dates for the watchlist", "Portfolio", evts),
    Fn("WEI", "World equity indices", "Markets", monitor_fn("WORLD EQUITY INDICES", WEI)),
    Fn("WCR", "World currencies", "Markets", monitor_fn("WORLD CURRENCIES", WCR)),
    Fn("FXC", "FX cross-rate matrix", "Markets", fxc),
    Fn("GLCO", "Global commodities", "Markets", monitor_fn("GLOBAL COMMODITIES", GLCO)),
    Fn("CRYP", "Crypto monitor", "Markets", monitor_fn("CRYPTO", CRYP)),
    Fn("IMAP", "Sectors, styles and credit funds", "Markets", monitor_fn("SECTORS AND STYLES", IMAP)),
    Fn("MOST", "Top gainers, losers and most active US stocks", "Markets", most),
    Fn("WB", "Treasury yields, moves in basis points", "Rates and economy", monitor_fn("WORLD BONDS: US TREASURIES", WB, True)),
    Fn("GC", "US Treasury yield curve and spreads", "Rates and economy", gc),
    Fn("WIRP", "Fed rate path priced by futures", "Rates and economy", wirp),
    Fn("ECO", "Economic calendar for the week", "Rates and economy", eco),
    Fn("ECST", "US economic statistics (FRED)", "Rates and economy", ecst),
    Fn("TOP", "Top news", "News and map", top),
    Fn("HELP", "This list", "News and map", help_),
]}
# handled by the page itself; listed so HELP shows them
PAGE_FUNCTIONS = {"GP": "Price chart (the dashboard)", "MAP": "World map: news, conflicts, ships, planes, earthquakes"}
