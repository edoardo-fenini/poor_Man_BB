"""Company data from Yahoo Finance as plain dicts and lists. Every call blocks: run it in a thread."""
import functools
import math
import time


def cached(ttl: float):
    """Remember each answer for `ttl` seconds: Yahoo rate-limits, and these figures move slowly."""
    def wrap(fn):
        memo: dict = {}

        @functools.wraps(fn)
        def inner(*args):
            hit = memo.get(args)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
            value = fn(*args)
            memo[args] = (time.time(), value)
            return value
        return inner
    return wrap


def _num(x):
    """A float, or None for missing / NaN."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _records(df, limit: int) -> list[dict]:
    if df is None or len(df) == 0:
        return []
    out = []
    for row in df.head(limit).reset_index().to_dict("records"):
        out.append({str(k): (v.isoformat()[:10] if hasattr(v, "isoformat") else _num(v) if isinstance(v, float) else v)
                    for k, v in row.items()})
    return out


def _ticker(symbol: str):
    import yfinance as yf

    return yf.Ticker(symbol)


@cached(900)
def info(symbol: str) -> dict:
    return dict(_ticker(symbol).info or {})


def _table(df) -> dict:
    if df is None or len(df) == 0:
        return {"periods": [], "rows": {}}
    return {"periods": [str(c)[:10] for c in df.columns],
            "rows": {str(name): [_num(v) for v in values] for name, values in zip(df.index, df.values.tolist())}}


@cached(3600)
def statements(symbol: str) -> dict:
    """Annual statements, newest period first: {income|balance|cash: {periods, rows: {line: [values]}}}."""
    t = _ticker(symbol)
    return {"income": _table(t.income_stmt), "balance": _table(t.balance_sheet), "cash": _table(t.cashflow)}


@cached(120)
def options(symbol: str, index: int = 0) -> dict:
    """One expiry of the option chain. `index` picks the expiry, nearest first."""
    t = _ticker(symbol)
    expiries = list(t.options or [])
    if not expiries:
        return {"expiries": [], "expiry": None, "calls": [], "puts": []}
    expiry = expiries[max(0, min(index, len(expiries) - 1))]
    chain = t.option_chain(expiry)

    def side(df) -> list[dict]:
        return [{"strike": _num(r["strike"]) or 0.0, "last": _num(r["lastPrice"]) or 0.0, "bid": _num(r["bid"]) or 0.0,
                 "ask": _num(r["ask"]) or 0.0, "volume": _num(r["volume"]) or 0.0, "oi": _num(r["openInterest"]) or 0.0,
                 "iv": _num(r["impliedVolatility"]) or 0.0} for r in df.to_dict("records")]
    return {"expiries": expiries, "expiry": expiry, "calls": side(chain.calls), "puts": side(chain.puts)}


@cached(3600)
def earnings(symbol: str) -> list[dict]:
    return _records(_ticker(symbol).earnings_dates, 16)


@cached(3600)
def dividends(symbol: str) -> list[tuple[str, float]]:
    series = _ticker(symbol).dividends
    return [(str(ts)[:10], float(v)) for ts, v in series.items()] if series is not None else []


@cached(3600)
def holders(symbol: str) -> dict:
    t = _ticker(symbol)
    major = t.major_holders
    return {"major": {str(k): _num(v) for k, v in major["Value"].items()} if major is not None and len(major) else {},
            "institutions": _records(t.institutional_holders, 15), "insiders": _records(t.insider_transactions, 20)}


@cached(3600)
def analysts(symbol: str) -> dict:
    t = _ticker(symbol)
    return {"trend": _records(t.recommendations, 4), "changes": _records(t.upgrades_downgrades, 20)}


@cached(86400)
def peers(symbol: str) -> list[str]:
    """Largest companies in the same industry, the symbol itself first."""
    import yfinance as yf

    key = info(symbol).get("industryKey")
    if not key:
        return [symbol]
    top = yf.Industry(key).top_companies
    names = [str(s) for s in top.index[:9]] if top is not None else []
    return [symbol] + [s for s in names if s != symbol][:8]


@cached(120)
def screen(name: str) -> list[dict]:
    """Yahoo's ready-made screens: day_gainers, day_losers, most_actives."""
    import yfinance as yf

    return list(yf.screen(name).get("quotes", []))[:25]
