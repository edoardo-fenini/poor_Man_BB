"""Shared feed plumbing: keep a feed alive with backoff, and parse without crashing."""
import asyncio
import logging
import re
import time
from collections import Counter
from collections.abc import Awaitable, Callable

from terminal.bus import Bus
from terminal.events import FeedStatus

log = logging.getLogger("terminal.feeds")
_SECRET = re.compile(r"(?i)\b(token|apikey|api_key|key)=[^&\s'\"]+")


def scrub(text: str) -> str:
    """Error text often quotes the request URL; never let an API key ride along into the UI or logs."""
    return _SECRET.sub(r"\1=***", text)
SKIPPED: Counter[str] = Counter()


async def run_feed(
    name: str,
    bus: Bus,
    work: Callable[[], Awaitable[None]],
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    sleep=asyncio.sleep,
    grace: float = 5.0,
) -> None:
    """Run work() forever. Report ok once it has survived `grace` seconds, down when it dies."""
    failures = 0
    while True:
        started = time.monotonic()
        task = None
        try:
            task = asyncio.ensure_future(work())  # inside the try: work() itself may raise, e.g. on a bad data file
            done, _ = await asyncio.wait({task}, timeout=grace)
            if not done:
                bus.publish(FeedStatus(name, "ok", "", time.time()))
            await task
            await sleep(base_delay)  # clean return (e.g. to resubscribe): restart, not an outage
            continue
        except asyncio.CancelledError:
            if task:
                task.cancel()
            raise
        except Exception as exc:  # any feed failure is retried
            detail = scrub(f"{type(exc).__name__}: {exc}")[:200]
            log.warning("feed %s down: %s", name, detail)
        bus.publish(FeedStatus(name, "down", detail, time.time()))
        if time.monotonic() - started > max_delay:
            failures = 0
        await sleep(min(base_delay * 2**failures, max_delay))
        failures += 1


def safe(parse: Callable, raw, name: str) -> list:
    """parse(raw), or [] if the payload is malformed. One bad message never stops a feed."""
    try:
        out = parse(raw)
    except Exception:
        SKIPPED[name] += 1
        return []
    if out is None:
        return []
    return out if isinstance(out, list) else [out]
