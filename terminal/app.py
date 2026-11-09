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

from terminal.brain.analysis import Analyser
from terminal.brain.conflicts import Zones, load_zones
from terminal.brain.news_rules import RulesAnalyser
from terminal.bus import Bus
from terminal.config import Config
from terminal.events import to_json
from terminal.feeds import yahoo
from terminal.prices import Prices
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
    analyser: Analyser | None = None,
) -> FastAPI:
    clients: set[asyncio.Queue] = set()
    status: dict[str, dict] = {}
    analyser = analyser or RulesAnalyser()
    zones = Zones(load_zones(), store)
    # story id -> the stored story; the same headline from several outlets is one story
    stories: dict[str, dict] = {n["id"]: n for n in store.recent("news", STORED["news"])}

    def news_payload(item) -> dict | None:
        """The story with its analysis, or None when this outlet already reported it."""
        story = stories.get(item.id)
        if story and item.source in story["outlets"]:
            return None
        outlets = (story["outlets"] if story else []) + [item.source]
        analysis = analyser.analyse(item, {s.id for s in watchlist_symbols(store)}, len(outlets))
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
        if kind == "tick":
            prices.update(event)
        elif kind == "status":
            status[event.feed] = {"state": event.state, "detail": event.detail, "ts": event.ts}
        elif kind == "news":
            payload = news_payload(event)
            if payload is None:
                return
            store.add("news", event.id, payload["ts"], payload, replace=True)
        elif kind in STORED:
            if not store.add(kind, _event_key(payload), payload["ts"], payload):
                return  # already seen: do not show it twice
            if kind == "conflict":
                zones.record(event)
        broadcast(payload)

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

    @app.get("/api/candles")
    async def candles(symbol: str, interval: str = "1d", limit: int = 0):
        resolved = resolve(symbol)
        if not resolved:
            return _error(404, "symbol not found")
        if interval not in yahoo.INTERVALS:
            return _error(400, "bad interval")
        try:
            rows = await history(resolved, interval)
        except Exception as exc:
            log.warning("history failed for %s: %s", resolved.id, exc)
            return _error(502, "data source unavailable")
        if not rows:
            return _error(404, "symbol not found")
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
        return get_watchlist()

    @app.get("/api/strip")
    def get_strip():
        return [{"id": s.id, "asset": s.asset} for s in strip_symbols()]

    @app.get("/api/settings")
    def get_settings():
        return store.get("settings", {})

    @app.put("/api/settings")
    async def put_settings(request: Request):
        body = await request.json()
        if not isinstance(body, dict):
            return _error(400, "expected an object")
        store.set("settings", {**store.get("settings", {}), **body})
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
        try:
            while True:
                await socket.send_json(await queue.get())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            clients.discard(queue)

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
