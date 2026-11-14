"""`python -m terminal doctor`: one small real request per data source, so you can see what works here."""
import asyncio
import json
from collections.abc import Awaitable, Callable

import httpx
import websockets

from terminal.brain.conflicts import load_zones
from terminal.config import Config
from terminal.feeds.base import scrub
from datetime import date

from terminal.feeds import crypto, econ, finnhub, gdelt, onchain, planes, quakes, rss, sec, ships, yahoo
from terminal.symbols import resolve

Check = tuple[str, Callable[[], Awaitable[str]]]


class Skip(Exception):
    """The check cannot run, e.g. a key is missing. The message says what to do."""


async def _first(url: str, subscribe: dict | None, accept: Callable[[dict], bool]) -> str:
    async with websockets.connect(url, open_timeout=8) as ws:
        if subscribe:
            await ws.send(json.dumps(subscribe))
        seen = 0
        while True:
            seen += 1
            if accept(json.loads(await ws.recv())):
                return f"data after {seen} message(s)"


def default_checks(config: Config) -> list[Check]:
    btc = [resolve("BTC")]

    def need(value: str, key: str, where: str) -> None:
        if not value:
            raise Skip(f"Set {key} in .env — {where}")

    async def yahoo_check() -> str:
        return f"{len(await yahoo.history(resolve('AAPL'), '1d'))} daily candles for AAPL"

    def safe_parse(parse):
        def accept(message) -> bool:
            try:
                return bool(parse(message))
            except Exception:
                return False
        return accept

    def exchange_check(index: int) -> Callable[[], Awaitable[str]]:
        async def check() -> str:
            _, url, subscribe, parse = crypto._providers(btc)[index]
            return await _first(url, subscribe, safe_parse(parse))
        return check

    async def finnhub_stream() -> str:
        need(config.finnhub_key, "FINNHUB_KEY", "free key at finnhub.io")
        async with websockets.connect(f"wss://ws.finnhub.io?token={config.finnhub_key}", open_timeout=8) as ws:
            await ws.send(json.dumps({"type": "subscribe", "symbol": "AAPL"}))
            return "connected (trades arrive only while the US market is open)"

    async def finnhub_news() -> str:
        need(config.finnhub_key, "FINNHUB_KEY", "free key at finnhub.io")
        async with httpx.AsyncClient(timeout=8) as client:
            params, headers = finnhub.news_request(config.finnhub_key)
            response = await client.get("https://finnhub.io/api/v1/news", params=params, headers=headers)
            response.raise_for_status()
            return f"{len(finnhub.parse_news(response.json()))} stories"

    def rss_check(feed: dict) -> Callable[[], Awaitable[str]]:
        async def check() -> str:
            async with httpx.AsyncClient(timeout=8, follow_redirects=True,
                                         headers={"User-Agent": rss.USER_AGENT}) as client:
                response = await client.get(feed["url"])
                response.raise_for_status()
                items = rss.parse_feed(response.content, feed["name"], 0)
                if not items:
                    raise ValueError("reachable, but no stories parsed — the URL may have moved")
                return f"{len(items)} stories"
        return check

    async def blockchain() -> str:
        return await _first("wss://ws.blockchain.info/inv", {"op": "unconfirmed_sub"}, lambda m: m.get("op") == "utx")

    async def etherscan() -> str:
        need(config.etherscan_key, "ETHERSCAN_KEY", "free key at etherscan.io/myapikey")
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(onchain.ETHERSCAN, params={
                "chainid": 1, "module": "proxy", "action": "eth_blockNumber", "apikey": config.etherscan_key})
            response.raise_for_status()
            return f"latest block {int(response.json()['result'], 16)}"

    async def edgar() -> str:
        need(config.sec_user_agent, "SEC_USER_AGENT", 'your contact, e.g. "Jane Doe jane@example.com"')
        async with httpx.AsyncClient(timeout=8, headers=sec.sec_headers(config.sec_user_agent)) as client:
            return f"{len(sec.parse_current(await sec.fetch_current(client, '4', 40), 0))} recent Form 4 filings"

    async def gdelt_check() -> str:
        zone = load_zones()[0]
        async with httpx.AsyncClient(timeout=8) as client:
            response = await client.get(gdelt.API, params={
                "query": gdelt.query_for(zone), "mode": "artlist", "format": "json", "maxrecords": 10, "timespan": "1d"})
            response.raise_for_status()
            return f"{len(gdelt.parse_artlist(response.json(), zone['name']))} headlines for {zone['name']}"

    def get(fetch, what: str, timeout: float = 8) -> Callable[[], Awaitable[str]]:
        async def check() -> str:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers={"User-Agent": rss.USER_AGENT}) as client:
                return f"{len(await fetch(client))} {what}"
        return check

    async def aisstream() -> str:
        need(config.aisstream_key, "AISSTREAM_KEY", "free key at aisstream.io")
        box = ships.CHOKEPOINTS["English Channel"]
        return await _first(ships.URL, {"APIKey": config.aisstream_key, "BoundingBoxes": [[list(box[0]), list(box[1])]]},
                            lambda m: "MetaData" in m)

    async def fred() -> str:
        need(config.fred_key, "FRED_KEY", "free key at fred.stlouisfed.org/docs/api/api_key.html")
        async with httpx.AsyncClient(timeout=8) as client:
            return f"{len(await econ.fred(client, config.fred_key, 'UNRATE'))} unemployment readings"

    return [
        ("planes (adsb.lol)", get(lambda c: planes.around(c, 51.5, 0.0, 100), "aircraft near London")),
        ("military planes", get(planes.military, "military aircraft")),
        ("ships (aisstream)", aisstream),
        ("earthquakes (usgs)", get(quakes.recent, "earthquakes today")),
        ("economic calendar", get(econ.calendar, "events this week")),
        ("treasury curve", get(lambda c: econ.curve(c, date.today()), "daily curves", 40)),
        ("fred", fred),
        ("yahoo", yahoo_check),
        ("binance", exchange_check(0)), ("coinbase", exchange_check(1)), ("kraken", exchange_check(2)),
        ("finnhub stream", finnhub_stream), ("finnhub news", finnhub_news),
        ("blockchain.info", blockchain), ("etherscan", etherscan), ("sec edgar", edgar), ("gdelt", gdelt_check),
        *[(f"rss: {feed['name']}", rss_check(feed)) for feed in rss.load_feeds()],
    ]


async def doctor(checks: list[Check], timeout: float = 12.0) -> int:
    """Run every check at once, print one line each, return how many failed."""
    async def run(check: Callable[[], Awaitable[str]]) -> tuple[str, str]:
        try:
            return "OK  ", await asyncio.wait_for(check(), timeout)
        except Skip as exc:
            return "SKIP", str(exc)
        except TimeoutError:
            return "FAIL", f"timed out after {timeout:g} s"
        except Exception as exc:
            return "FAIL", scrub(f"{type(exc).__name__}: {exc}")[:160]

    results = await asyncio.gather(*(run(check) for _, check in checks))
    width = max((len(name) for name, _ in checks), default=0)
    for (name, _), (state, detail) in zip(checks, results):
        print(f"{state} {name:<{width}}  {detail}")
    failed = sum(state == "FAIL" for state, _ in results)
    skipped = sum(state == "SKIP" for state, _ in results)
    print(f"\n{len(results) - failed - skipped} ok, {failed} failed, {skipped} skipped")
    return failed
