"""News from RSS/Atom feeds. All feed text is untrusted and is reduced to plain text here."""
import asyncio
import calendar
import hashlib
import html
import re
import time
import tomllib
from pathlib import Path

import feedparser
import httpx

from terminal.bus import Bus
from terminal.events import NewsItem
from terminal.feeds.base import safe

FEEDS_FILE = Path(__file__).parent.parent / "data" / "rss_feeds.toml"
USER_AGENT = "Mozilla/5.0 (compatible; terminal-rss/0.1)"
_BLOCKS = re.compile(r"<(script|style)\b.*?</\1\s*>", re.S | re.I)
_TAGS = re.compile(r"<[^>]*>")


def load_feeds() -> list[dict]:
    return tomllib.loads(FEEDS_FILE.read_text())["feed"]


def clean(text, limit: int = 300) -> str:
    """Plain text: no tags, no script bodies, entities decoded, whitespace collapsed, bounded length."""
    text = _TAGS.sub(" ", _BLOCKS.sub(" ", text or ""))
    text = _TAGS.sub(" ", html.unescape(text))  # entities can hide a second layer of markup
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def news_id(title: str) -> str:
    """Same headline from different outlets gets the same id."""
    return hashlib.sha1(re.sub(r"[^a-z0-9]", "", title.lower()).encode()).hexdigest()[:16]


def entry_time(entry, now: float) -> float:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return min(calendar.timegm(parsed), now) if parsed else now


def parse_feed(xml: bytes, source: str, now: float) -> list[NewsItem]:
    out = []
    for entry in feedparser.parse(xml).entries:
        title, url = clean(entry.get("title")), entry.get("link", "")
        if not title or not url.startswith(("http://", "https://")):
            continue
        out.append(NewsItem(news_id(title), title, url, source, entry_time(entry, now), clean(entry.get("summary"))))
    return out


async def poll(bus: Bus, feeds: list[dict], every: float = 45.0) -> None:
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}) as client:
        while True:
            async def fetch(feed: dict) -> list[NewsItem]:
                response = await client.get(feed["url"])
                response.raise_for_status()
                return safe(lambda body: parse_feed(body, feed["name"], time.time()), response.content, "rss")

            results = await asyncio.gather(*(fetch(f) for f in feeds), return_exceptions=True)
            if feeds and all(isinstance(r, BaseException) for r in results):
                raise ConnectionError(f"all {len(feeds)} news feeds failed: {results[0]!r}"[:200])
            for items in results:
                for item in items if isinstance(items, list) else []:
                    key = item.id + item.source
                    if key not in seen:
                        seen.add(key)
                        bus.publish(item)
            if len(seen) > 50000:
                seen.clear()
            await asyncio.sleep(every)
