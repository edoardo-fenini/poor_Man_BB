"""FastAPI app: REST for history and state, one WebSocket for live push."""
import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from terminal.brain.alerts import AlertEngine
from terminal.brain.analysis import Analyser
from terminal.brain.conflicts import Zones, load_zones
from terminal.brain.news_rules import RulesAnalyser
from terminal.bus import Bus
from terminal.config import Config
from terminal.events import to_json
from terminal.feeds import yahoo
from terminal.notify import mac_notify
from terminal.prices import Prices
from terminal.settings import load_settings, validate
from terminal.store import Store
from terminal.symbols import Symbol, resolve

log = logging.getLogger("terminal.app")
WEB = Path(__file__).parent / "web"
DEFAULT_WATCHLIST = ["^GSPC", "^IXIC", "AAPL", "MSFT", "NVDA", "TSLA", "SPY", "QQQ",
                     "BTC", "ETH", "EURUSD", "GC=F", "CL=F"]
STRIP = ["^GSPC", "^IXIC", "^VIX", "DX-Y.NYB", "^TNX", "GC=F", "CL=F", "BTC"]
STORED = {"news": 2000, "whale": 2000, "conflict": 2000, "alert": 1000}  # kind -> rows kept


def watchlist_symbols(store: Store) -> list[Symbol]:
    symbols = [resolve(text) for text in store.get("watchlist", DEFAULT_WATCHLIST)]
    return [s for s in symbols if s]


def strip_symbols() -> list[Symbol]:
    return [resolve(text) for text in STRIP]


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _event_key(payload: dict) -> str:
    if payload["kind"] == "alert":
        return f"{payload['key']}:{payload['ts']}"
    return payload["id"]


def create_app(
    config: Config,
    store: Store,
    bus: Bus,
    prices: Prices,
    feeds: list[Callable[[], Awaitable[None]]] | None = None,
    history=yahoo.history,
    bars=yahoo.bars,
    analyser: Analyser | None = None,
    notify: Callable[[str, str], None] = mac_notify,
) -> FastAPI:
    clients: set[asyncio.Queue] = set()
    status: dict[str, dict] = {}
    analyser = analyser or RulesAnalyser()
    zones = Zones(load_zones(), store)
    cache: dict = {}  # settings and watchlist are read on every tick; cleared whenever either is saved

    def settings() -> dict:
        if "settings" not in cache:
            cache["settings"] = load_settings(store)
        return cache["settings"]

    def watched() -> set[str]:
        if "watched" not in cache:
            cache["watched"] = {s.id for s in watchlist_symbols(store)}
        return cache["watched"]

    def asset_of(symbol: str) -> str:
        resolved = resolve(symbol)
        return resolved.asset if resolved else "stock"

    engine = AlertEngine(settings, prices, watched, asset_of)
    # story id -> the stored story; the same headline from several outlets is one story
    stories: dict[str, dict] = {n["id"]: n for n in store.recent("news", STORED["news"])}

    def news_payload(item) -> dict | None:
        """The story with its analysis, or None when this outlet already reported it."""
        story = stories.get(item.id)
        if story and item.source in story["outlets"]:
            return None
        outlets = (story["outlets"] if story else []) + [item.source]
        analysis = analyser.analyse(item, watched(), len(outlets))
        payload = {**(story or to_json(item)), "outlets": outlets, "analysis": asdict(analysis)}
        stories[item.id] = payload
        if len(stories) > 2 * STORED["news"]:
            for key in list(stories)[: STORED["news"]]:
                del stories[key]
        return payload

    def broadcast(payload: dict) -> None:
        for queue in clients:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(payload)

    def handle(event) -> None:
        payload = to_json(event)
        kind = payload["kind"]
        alerts = []
        if kind == "tick":
            prices.update(event)
            alerts = engine.on_event(event)
        elif kind == "status":
            status[event.feed] = {"state": event.state, "detail": event.detail, "ts": event.ts}
        elif kind == "news":
            payload = news_payload(event)
            if payload is None:
                return
            store.add("news", event.id, payload["ts"], payload, replace=True)
            alerts = engine.on_news(payload)
        elif kind in STORED:
            if not store.add(kind, _event_key(payload), payload["ts"], payload):
                return  # already seen: do not show it twice
            if kind == "whale":
                alerts = engine.on_event(event)
            elif kind == "conflict":
                zone = zones.record(event)
                alerts = engine.on_zone(zone) if zone else []
        broadcast(payload)
        for alert in alerts:
            alert_payload = to_json(alert)
            store.add("alert", _event_key(alert_payload), alert.ts, alert_payload)
            broadcast(alert_payload)
            if alert.severity == "critical":
                notify(alert.title, alert.body)

    async def pump() -> None:
        pruned = 0
        async for event in bus.subscribe(maxsize=10000):
            try:
                handle(event)
            except Exception:
                log.exception("failed to handle %r", event)
            pruned += 1
            if pruned % 5000 == 0:
                for kind, keep in STORED.items():
                    store.prune(kind, keep)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        tasks = [asyncio.create_task(pump())] + [asyncio.create_task(feed()) for feed in feeds or []]
        await asyncio.sleep(0)  # let the pump subscribe before anything publishes
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.handle = handle
    app.state.bars = bars
    app.state.history = history

    @app.get("/api/candles")
    async def candles(symbol: str, interval: str = "1d", limit: int = 0):
        resolved = resolve(symbol)
        if not resolved:
            return _error(404, "symbol not found")
        if interval not in yahoo.INTERVALS:
            return _error(400, "bad interval")
        try:
            rows = await app.state.history(resolved, interval)
        except Exception as exc:
            log.warning("history failed for %s: %s", resolved.id, exc)
            return _error(502, "data source unavailable")
        if not rows:
            return _error(404, "no data")  # a well-formed symbol: unknown to Yahoo, or Yahoo did not answer
        if limit > 0:
            rows = rows[-limit:]
        return {
            "symbol": resolved.id,
            "asset": resolved.asset,
            "candles": [
                {"time": c.ts, "open": c.open, "high": c.high, "low": c.low, "close": c.close, "volume": c.volume}
                for c in rows
            ],
        }

    @app.get("/api/closes")
    async def closes():
        """Previous and latest daily close for every tracked symbol, in one batched request."""
        cached = cache.get("closes")
        if cached and time.time() - cached[0] < 60:
            return cached[1]
        tracked = {s.id: s for s in watchlist_symbols(store) + strip_symbols()}
        try:
            daily = await app.state.bars(list(tracked.values()), "5d", "1d")
        except Exception as exc:
            log.warning("closes failed: %s", exc)
            return {}
        out = {id_: {"prev": rows[-2].close, "last": rows[-1].close, "ts": rows[-1].ts}
               for id_, rows in daily.items() if len(rows) >= 2}
        cache["closes"] = (time.time(), out)
        return out

    @app.get("/api/events")
    def events(kind: str, limit: int = 200):
        if kind not in STORED:
            return _error(400, "bad kind")
        return store.recent(kind, max(1, min(limit, 1000)))

    @app.get("/api/watchlist")
    def get_watchlist():
        return [{"id": s.id, "asset": s.asset} for s in watchlist_symbols(store)]

    @app.put("/api/watchlist")
    async def put_watchlist(request: Request):
        body = await request.json()
        if not isinstance(body, list) or not all(isinstance(x, str) for x in body):
            return _error(400, "expected a list of symbols")
        bad = [x for x in body if not resolve(x)]
        if bad:
            return _error(400, "unknown symbols: " + ", ".join(bad))
        ids = list(dict.fromkeys(resolve(x).id for x in body))[:200]
        store.set("watchlist", ids)
        cache.clear()
        return get_watchlist()

    @app.get("/api/strip")
    def get_strip():
        return [{"id": s.id, "asset": s.asset} for s in strip_symbols()]

    @app.get("/api/settings")
    def get_settings():
        return load_settings(store)

    @app.put("/api/settings")
    async def put_settings(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            return _error(400, "expected an object")
        try:
            update = validate(body)
        except ValueError as exc:
            return _error(400, str(exc))
        store.set("settings", {**store.get("settings", {}), **update})
        cache.clear()
        return get_settings()

    @app.get("/api/conflicts")
    def get_conflicts():
        return zones.state()

    @app.get("/api/status")
    def get_status():
        return status

    @app.get("/api/prices")
    def get_prices():
        now = time.time()
        out = {}
        for s in watchlist_symbols(store) + strip_symbols():
            if (price := prices.last(s.id)) is not None:
                out[s.id] = {"price": price, "ts": now - prices.age(s.id, now)}
        return out

    @app.websocket("/ws")
    async def ws(socket: WebSocket):
        await socket.accept()
        queue: asyncio.Queue = asyncio.Queue(2000)
        clients.add(queue)

        async def until_closed() -> None:  # the page never sends; this returns when it goes away
            with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                while True:
                    await socket.receive_text()

        closed = asyncio.create_task(until_closed())
        try:
            while True:
                item = asyncio.create_task(queue.get())
                await asyncio.wait({item, closed}, return_when=asyncio.FIRST_COMPLETED)
                if closed.done():
                    item.cancel()
                    break
                await socket.send_json(item.result())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            closed.cancel()
            clients.discard(queue)

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
