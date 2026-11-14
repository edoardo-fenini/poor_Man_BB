"""Analytics that paid terminals sell, computed from free prices and filings. Pure functions, no network."""
import math
import statistics
from datetime import date

TRADING_DAYS = 252


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def norm_pdf(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def greeks(spot: float, strike: float, years: float, rate: float, vol: float, call: bool) -> dict | None:
    """Black-Scholes value and greeks. Theta is per calendar day, vega per vol point. None when undefined."""
    if min(spot, strike, years, vol) <= 0:
        return None
    root = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / root
    d2 = d1 - root
    discount = math.exp(-rate * years)
    if call:
        price = spot * norm_cdf(d1) - strike * discount * norm_cdf(d2)
        delta = norm_cdf(d1)
        theta = -spot * norm_pdf(d1) * vol / (2 * math.sqrt(years)) - rate * strike * discount * norm_cdf(d2)
    else:
        price = strike * discount * norm_cdf(-d2) - spot * norm_cdf(-d1)
        delta = norm_cdf(d1) - 1
        theta = -spot * norm_pdf(d1) * vol / (2 * math.sqrt(years)) + rate * strike * discount * norm_cdf(-d2)
    return {"price": price, "delta": delta, "gamma": norm_pdf(d1) / (spot * root),
            "theta": theta / 365, "vega": spot * norm_pdf(d1) * math.sqrt(years) / 100}


def returns(closes: list[float]) -> list[float]:
    return [b / a - 1 for a, b in zip(closes, closes[1:]) if a > 0]


def hist_vol(closes: list[float], days: int) -> float | None:
    """Annualised volatility of the last `days` daily log returns."""
    window = closes[-(days + 1):]
    if len(window) < days + 1 or min(window) <= 0:
        return None
    logs = [math.log(b / a) for a, b in zip(window, window[1:])]
    return statistics.stdev(logs) * math.sqrt(TRADING_DAYS)


def aligned(a: dict, b: dict) -> tuple[list[float], list[float]]:
    """Two {day: close} series cut down to the days both have, in order."""
    days = sorted(a.keys() & b.keys())
    return [a[d] for d in days], [b[d] for d in days]


def beta(asset: list[float], market: list[float]) -> dict | None:
    """Regression of asset returns on market returns (same length, same days)."""
    if len(asset) < 20 or len(asset) != len(market) or statistics.pstdev(market) == 0 or statistics.pstdev(asset) == 0:
        return None
    slope, intercept = statistics.linear_regression(market, asset)
    corr = statistics.correlation(asset, market)
    return {"beta": slope, "adjusted": slope * 2 / 3 + 1 / 3,  # the adjustment Bloomberg shows: pulls beta toward 1
            "alpha": intercept * TRADING_DAYS, "r2": corr * corr, "corr": corr, "n": len(asset)}


def correlation(a: list[float], b: list[float]) -> float | None:
    if len(a) < 20 or len(a) != len(b) or statistics.pstdev(a) == 0 or statistics.pstdev(b) == 0:
        return None
    return statistics.correlation(a, b)


def max_drawdown(closes: list[float]) -> float:
    peak, worst = -math.inf, 0.0
    for c in closes:
        peak = max(peak, c)
        worst = min(worst, c / peak - 1)
    return worst


def sharpe(rets: list[float], risk_free: float) -> float | None:
    if len(rets) < 20 or statistics.stdev(rets) == 0:
        return None
    return (statistics.mean(rets) - risk_free / TRADING_DAYS) / statistics.stdev(rets) * math.sqrt(TRADING_DAYS)


def var95(rets: list[float]) -> float | None:
    """One-day historical value at risk: the loss exceeded on 5% of days."""
    if len(rets) < 40:
        return None
    return sorted(rets)[int(len(rets) * 0.05)]


def max_pain(calls: list[dict], puts: list[dict]) -> float | None:
    """The strike at expiry where option holders, taken together, are paid the least."""
    strikes = sorted({o["strike"] for o in calls + puts})
    if not strikes:
        return None

    def paid(at: float) -> float:
        return (sum(max(at - c["strike"], 0) * c["oi"] for c in calls)
                + sum(max(p["strike"] - at, 0) * p["oi"] for p in puts))
    return min(strikes, key=paid)


def altman_z(working_capital, retained, ebit, market_cap, liabilities, revenue, assets) -> float | None:
    if not assets or not liabilities or None in (working_capital, retained, ebit, market_cap, revenue):
        return None
    return (1.2 * working_capital / assets + 1.4 * retained / assets + 3.3 * ebit / assets
            + 0.6 * market_cap / liabilities + 1.0 * revenue / assets)


def piotroski(now: dict, before: dict) -> list[tuple[str, bool | None]]:
    """The nine F-score tests. Each is True, False, or None when a figure is missing."""
    def ratio(d, top, bottom):
        return d[top] / d[bottom] if d.get(top) is not None and d.get(bottom) else None

    def test(a, b, better=lambda x, y: x > y):
        return None if a is None or b is None else better(a, b)

    roa, roa_before = ratio(now, "net_income", "assets"), ratio(before, "net_income", "assets")
    return [
        ("Net income positive", test(now.get("net_income"), 0)),
        ("Operating cash flow positive", test(now.get("cfo"), 0)),
        ("Return on assets rising", test(roa, roa_before)),
        ("Cash flow above net income", test(now.get("cfo"), now.get("net_income"))),
        ("Long-term debt ratio falling", test(ratio(now, "long_debt", "assets"), ratio(before, "long_debt", "assets"),
                                               lambda x, y: x < y)),
        ("Current ratio rising", test(ratio(now, "current_assets", "current_liabilities"),
                                       ratio(before, "current_assets", "current_liabilities"))),
        ("No new shares issued", test(now.get("shares"), before.get("shares"), lambda x, y: x <= y * 1.001)),
        ("Gross margin rising", test(ratio(now, "gross_profit", "revenue"), ratio(before, "gross_profit", "revenue"))),
        ("Asset turnover rising", test(ratio(now, "revenue", "assets"), ratio(before, "revenue", "assets"))),
    ]


def merton(equity: float, equity_vol: float, debt: float, rate: float, years: float = 1.0) -> dict | None:
    """Merton model: treat equity as a call on the firm's assets, solve for asset value and volatility,
    and read off the distance to default and the chance assets end below debt."""
    if min(equity, equity_vol, debt) <= 0:
        return None
    assets, vol = equity + debt, equity_vol * equity / (equity + debt)
    for _ in range(200):
        d1 = (math.log(assets / debt) + (rate + vol * vol / 2) * years) / (vol * math.sqrt(years))
        d2 = d1 - vol * math.sqrt(years)
        new_assets = (equity + debt * math.exp(-rate * years) * norm_cdf(d2)) / norm_cdf(d1)
        new_vol = equity_vol * equity / (new_assets * norm_cdf(d1))
        done = abs(new_assets - assets) < 1e-6 * assets and abs(new_vol - vol) < 1e-8
        assets, vol = new_assets, new_vol
        if done:
            break
    d2 = (math.log(assets / debt) + (rate - vol * vol / 2) * years) / (vol * math.sqrt(years))
    return {"assets": assets, "asset_vol": vol, "distance": d2, "default_prob": norm_cdf(-d2)}


def wacc(market_cap: float, debt: float, beta_: float, risk_free: float, cost_of_debt: float, tax: float,
         equity_premium: float = 0.05) -> dict:
    cost_equity = risk_free + beta_ * equity_premium
    total = market_cap + debt
    we, wd = (market_cap / total, debt / total) if total else (1.0, 0.0)
    return {"cost_equity": cost_equity, "cost_debt_after_tax": cost_of_debt * (1 - tax), "weight_equity": we,
            "weight_debt": wd, "wacc": we * cost_equity + wd * cost_of_debt * (1 - tax)}


def dcf(fcf: float, growth: float, discount: float, terminal_growth: float, net_debt: float, shares: float,
        years: int = 5) -> float | None:
    """Fair value per share: `years` of growing free cash flow, then a perpetuity."""
    if shares <= 0 or discount <= terminal_growth:
        return None
    value, cash = 0.0, fcf
    for year in range(1, years + 1):
        cash *= 1 + growth
        value += cash / (1 + discount) ** year
    value += cash * (1 + terminal_growth) / (discount - terminal_growth) / (1 + discount) ** years
    return (value - net_debt) / shares


def cagr(first: float, last: float, years: float) -> float | None:
    if first <= 0 or last <= 0 or years <= 0:
        return None
    return (last / first) ** (1 / years) - 1


def fed_path(contracts: list[tuple[str, float]]) -> list[dict]:
    """Fed funds futures settle at 100 minus the month's average rate, so each price is the market's
    forecast. `contracts` is [(month label, price)], nearest first. Moves are counted in 25bp steps."""
    if not contracts:
        return []
    now = 100 - contracts[0][1]
    out = []
    for month, price in contracts:
        rate = 100 - price
        out.append({"month": month, "rate": rate, "change_bp": (rate - now) * 100, "moves": (rate - now) / 0.25})
    return out


def seasonality(monthly: list[tuple[int, int, float]]) -> list[dict]:
    """Average return and hit rate per calendar month from [(year, month, close)] in order."""
    by_month: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for (_, _, before), (_, month, after) in zip(monthly, monthly[1:]):
        if before > 0:
            by_month[month].append(after / before - 1)
    return [{"month": m, "mean": statistics.mean(r) if r else None, "up": sum(x > 0 for x in r), "n": len(r)}
            for m, r in by_month.items()]


def rsi(closes: list[float], n: int = 14) -> float | None:
    if len(closes) <= n:
        return None
    gain = loss = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        g, l = max(d, 0), max(-d, 0)
        if i <= n:
            gain, loss = gain + g / n, loss + l / n
        else:
            gain, loss = (gain * (n - 1) + g) / n, (loss * (n - 1) + l) / n
    return 100.0 if loss == 0 else 100 - 100 / (1 + gain / loss)


def ema(values: list[float], n: int) -> list[float]:
    k, out = 2 / (n + 1), []
    for v in values:
        out.append(v if not out else out[-1] + k * (v - out[-1]))
    return out


def macd(closes: list[float]) -> tuple[float, float] | None:
    """(MACD line, signal line) with the usual 12/26/9."""
    if len(closes) < 35:
        return None
    line = [a - b for a, b in zip(ema(closes, 12), ema(closes, 26))]
    return line[-1], ema(line, 9)[-1]


def years_until(expiry: str, today: date) -> float:
    return max((date.fromisoformat(expiry) - today).days, 0.5) / 365
