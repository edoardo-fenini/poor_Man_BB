"""SEC EDGAR: 8-K filings as news; insider and fund filings as whale events."""
import asyncio
import re
import time

import feedparser
import httpx

from terminal.bus import Bus
from terminal.events import NewsItem
from terminal.feeds.base import safe
from terminal.feeds.rss import clean, entry_time, news_id

CURRENT = "https://www.sec.gov/cgi-bin/browse-edgar"
_TITLE = re.compile(r"^(?P<form>\S+) - (?P<company>.+?) \((?P<cik>\d+)\) \((?P<role>[^)]*)\)$")
_ITEM = re.compile(r"Item (\d+\.\d+): ([^<]+)")
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")


def sec_headers(user_agent: str) -> dict:
    return {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}


def _entries(atom: bytes, now: float) -> list[dict]:
    """Entries of EDGAR's "current filings" Atom feed."""
    out = []
    for entry in feedparser.parse(atom).entries:
        match = _TITLE.match(entry.get("title", "").strip())
        accession = _ACCESSION.search(entry.get("id", "") + entry.get("link", ""))
        if not match or not accession or not entry.get("link", "").startswith("https://www.sec.gov/"):
            continue
        out.append({**match.groupdict(), "accession": accession.group(), "url": entry["link"],
                    "summary": entry.get("summary", ""), "ts": entry_time(entry, now)})
    return out


def parse_8k(atom: bytes, now: float) -> list[NewsItem]:
    out = []
    for e in _entries(atom, now):
        if not e["form"].startswith("8-K"):
            continue
        # 9.01 (exhibits) is on nearly every 8-K and says nothing
        items = [clean(text) for number, text in _ITEM.findall(e["summary"]) if number != "9.01"]
        title = clean(f"{e['company']} filed {e['form']}" + (f": {items[0]}" if items else ""))
        out.append(NewsItem(news_id(e["accession"]), title, e["url"], "SEC", e["ts"], "; ".join(items)))
    return out


async def fetch_current(client: httpx.AsyncClient, form: str, count: int = 40) -> bytes:
    response = await client.get(CURRENT, params={"action": "getcurrent", "type": form, "company": "", "dateb": "",
                                                 "owner": "include", "count": count, "output": "atom"})
    response.raise_for_status()
    return response.content


async def poll_8k(bus: Bus, user_agent: str, every: float = 60.0) -> None:
    seen: set[str] = set()
    async with httpx.AsyncClient(timeout=20, headers=sec_headers(user_agent)) as client:
        while True:
            for item in safe(lambda b: parse_8k(b, time.time()), await fetch_current(client, "8-K"), "sec-8k"):
                if item.id not in seen:
                    seen.add(item.id)
                    bus.publish(item)
            await asyncio.sleep(every)
