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
    # bare "strike" is left out on purpose: "strikes deal" and labour strikes are not conflict
    "strike": ("airstrike", "air strike", "missile", "drone", "strike on", "strikes on", "strike hit", "strike kill",
               "shelling", "bombing", "bombard"),
    "clash": ("clash", "firefight", "troops", "offensive", "invasion", "incursion", "attack", "killed", "military",
              "warship", "fighter jet", "drills"),
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


async def poll(bus: Bus, zones: list[dict], every: float = 900.0, client: httpx.AsyncClient | None = None,
               pause: float = 6.0) -> None:
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        while True:
            failures = []
            for zone in zones:
                try:
                    response = await client.get(API, params={
                        "query": query_for(zone), "mode": "artlist", "format": "json",
                        "maxrecords": 50, "timespan": "1h", "sort": "datedesc"})
                    response.raise_for_status()
                    payload = response.json()  # GDELT reports query errors and rate limits as HTTP 200 plain text
                except (httpx.HTTPError, ValueError) as exc:
                    failures.append(f"{zone['name']}: {type(exc).__name__}")
                else:
                    for event in safe(lambda p: parse_artlist(p, zone["name"]), payload, "gdelt"):
                        bus.publish(event)
                await asyncio.sleep(pause)  # GDELT asks for at most one request every five seconds
            if zones and len(failures) == len(zones):
                raise ConnectionError(f"GDELT gave no usable answer for any zone ({failures[0]})")
            await asyncio.sleep(every)
    finally:
        if own:
            await client.aclose()
