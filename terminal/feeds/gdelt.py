"""Conflict headlines per zone from GDELT's free article search."""
import asyncio
import calendar
import time

import httpx

from terminal.bus import Bus
from terminal.events import ConflictEvent
from terminal.feeds.base import safe
from terminal.feeds.rss import clean, news_id

API = "https://api.gdeltproject.org/api/v2/doc/doc"
TYPES = {
    "strike": ("airstrike", "air strike", "missile", "drone", "strike", "shelling", "bombing", "bombard"),
    "clash": ("clash", "firefight", "troops", "offensive", "invasion", "incursion", "attack", "killed"),
    "sanction": ("sanction", "embargo", "export ban"),
    "blockade": ("blockade", "seize", "seized", "tanker", "naval"),
    "unrest": ("riot", "protest", "coup", "unrest", "uprising"),
}
CONFLICT_TERMS = ("missile", "airstrike", "attack", "troops", "sanctions", "blockade", "invasion", "clashes",
                  "shelling", "drone", "coup", "military")


def classify(title: str) -> str:
    lowered = title.lower()
    return next((kind for kind, words in TYPES.items() if any(w in lowered for w in words)), "other")


def query_for(zone: dict) -> str:
    return f"{zone['query']} ({' OR '.join(CONFLICT_TERMS)}) sourcelang:english"


def parse_artlist(payload: dict, zone: str) -> list[ConflictEvent]:
    out, seen = [], set()
    for article in payload["articles"]:
        title, url = clean(article.get("title"), 200), article.get("url") or ""
        if not title or not url.startswith(("http://", "https://")) or news_id(title) in seen:
            continue
        seen.add(news_id(title))
        ts = calendar.timegm(time.strptime(article["seendate"], "%Y%m%dT%H%M%SZ"))
        out.append(ConflictEvent(news_id(zone + title), zone, classify(title), title, url, float(ts)))
    return out


async def poll(bus: Bus, zones: list[dict], every: float = 900.0) -> None:
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            failures = 0
            for zone in zones:
                try:
                    response = await client.get(API, params={
                        "query": query_for(zone), "mode": "artlist", "format": "json",
                        "maxrecords": 50, "timespan": "1h", "sort": "datedesc"})
                    response.raise_for_status()
                    for event in safe(lambda r: parse_artlist(r.json(), zone["name"]), response, "gdelt"):
                        bus.publish(event)
                except httpx.HTTPError:
                    failures += 1
                await asyncio.sleep(6)  # GDELT asks for at most one request every five seconds
            if zones and failures == len(zones):
                raise ConnectionError("GDELT unreachable for every zone")
            await asyncio.sleep(every)
