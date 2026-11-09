"""SEC EDGAR: 8-K filings as news; insider and fund filings as whale events."""
import asyncio
import re
import time
import xml.etree.ElementTree as ET
from collections import defaultdict

import feedparser
import httpx

from terminal.bus import Bus
from terminal.events import NewsItem, WhaleEvent
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


# ---------------------------------------------------------------- insider and fund filings
_XML_BLOCK = re.compile(rb"<XML>\s*(.*?)\s*</XML>", re.S)
_NAMESPACE = re.compile(r"^\{[^}]*\}")
MAX_FILING_BYTES = 30_000_000


def parse_current(atom: bytes, now: float) -> list[dict]:
    """New filings, one per accession (EDGAR lists a filing once per party)."""
    seen, out = set(), []
    for entry in _entries(atom, now):
        if entry["accession"] not in seen:
            seen.add(entry["accession"])
            out.append(entry)
    return out


def filing_text_url(filing: dict) -> str:
    """The complete submission text file, which embeds every XML document of the filing."""
    return filing["url"].removesuffix("-index.htm") + ".txt"


def _xml_documents(text: bytes) -> list[ET.Element]:
    roots = []
    for block in _XML_BLOCK.findall(text):
        if b"<!DOCTYPE" in block or b"<!ENTITY" in block:
            continue  # EDGAR XML never declares entities; refuse anything that tries to expand them
        try:
            root = ET.fromstring(block)
        except ET.ParseError:
            continue
        for node in root.iter():
            node.tag = _NAMESPACE.sub("", node.tag)
        roots.append(root)
    return roots


def _value(node: ET.Element | None, path: str) -> str:
    found = node.find(path) if node is not None else None
    return (found.text or "").strip() if found is not None else ""


def parse_form4(text: bytes, filing: dict) -> list[WhaleEvent]:
    """Open-market insider purchases (code P) and sales (code S), summed per side."""
    doc = next((r for r in _xml_documents(text) if r.tag == "ownershipDocument"), None)
    if doc is None:
        return []
    symbol = _value(doc, "issuer/issuerTradingSymbol").upper()
    owner = _value(doc, "reportingOwner/reportingOwnerId/rptOwnerName")
    relation = doc.find("reportingOwner/reportingOwnerRelationship")
    title = _value(relation, "officerTitle") or ("Director" if _value(relation, "isDirector") in ("1", "true") else
                                                 "10% owner" if _value(relation, "isTenPercentOwner") in ("1", "true") else "")
    shares, dollars = defaultdict(float), defaultdict(float)
    for tx in doc.findall("nonDerivativeTable/nonDerivativeTransaction"):
        side = {"P": "buy", "S": "sell"}.get(_value(tx, "transactionCoding/transactionCode"))
        try:
            count = float(_value(tx, "transactionAmounts/transactionShares/value"))
            price = float(_value(tx, "transactionAmounts/transactionPricePerShare/value"))
        except ValueError:
            continue
        if side and count > 0 and price > 0:
            shares[side] += count
            dollars[side] += count * price
    who = clean(f"{owner} ({title})" if title else owner, 80)
    return [
        WhaleEvent(id=f"{filing['accession']}:{side}", type="insider", symbol=clean(symbol, 12), usd=dollars[side],
                   side=side, detail=f"{who} {'bought' if side == 'buy' else 'sold'} {shares[side]:,.0f} "
                                     f"@ ${dollars[side] / shares[side]:,.2f}",
                   url=filing["url"], ts=filing["ts"])
        for side in ("buy", "sell") if shares[side]
    ]


def parse_13f(text: bytes, filing: dict, top: int = 10) -> WhaleEvent | None:
    """A fund's reported holdings: total value and its largest positions. Values are in dollars (2023+ filings)."""
    table = next((r for r in _xml_documents(text) if r.tag == "informationTable"), None)
    if table is None:
        return None
    holdings: dict[str, float] = defaultdict(float)
    for row in table.findall("infoTable"):
        try:
            holdings[_value(row, "nameOfIssuer")] += float(_value(row, "value"))
        except ValueError:
            continue
    total = sum(holdings.values())
    if total <= 0:
        return None
    biggest = sorted(holdings.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    positions = ", ".join(f"{clean(name, 40)} {value / total:.1%}" for name, value in biggest)
    return WhaleEvent(id=filing["accession"], type="fund", symbol="", usd=total, side="",
                      detail=f"{clean(filing['company'], 60)} 13F: {positions}", url=filing["url"], ts=filing["ts"])


def is_form(filed: str, wanted: str) -> bool:
    """EDGAR's type filter is a prefix match ("4" also returns 424B2, 425...). Accept the form and its amendments."""
    return filed in (wanted, wanted + "/A")


async def fetch_filing(client: httpx.AsyncClient, filing: dict, max_bytes: int = MAX_FILING_BYTES) -> bytes | None:
    """The filing's full text, or None if it could not be read. One bad filing must not stop the feed."""
    try:
        response = await client.get(filing_text_url(filing))
    except httpx.HTTPError:
        return None
    if response.status_code != 200 or len(response.content) > max_bytes:
        return None
    return response.content


_SEEN_FILINGS: set[str] = set()  # outlives a feed restart, so a restart never replays or skips filings


async def poll_filings(bus: Bus, user_agent: str, every: float = 60.0) -> None:
    async with httpx.AsyncClient(timeout=30, headers=sec_headers(user_agent)) as client:
        while True:
            for form, parse in (("4", parse_form4), ("13F-HR", parse_13f)):
                listed = safe(lambda b: parse_current(b, time.time()), await fetch_current(client, form, 100), "sec-filings")
                new = [f for f in listed if f["accession"] not in _SEEN_FILINGS and is_form(f["form"], form)]
                # on first start read only the latest few, so start-up does not replay a hundred filings
                for filing in new if _SEEN_FILINGS else new[:10]:
                    text = await fetch_filing(client, filing)
                    await asyncio.sleep(0.25)  # SEC fair-access limit is 10 requests per second
                    if text is not None:
                        # big 13F tables take a while to parse: keep that off the event loop
                        for whale in await asyncio.to_thread(safe, lambda body: parse(body, filing), text, "sec-filings"):
                            bus.publish(whale)
                _SEEN_FILINGS.update(f["accession"] for f in new)
                if len(_SEEN_FILINGS) > 20000:
                    _SEEN_FILINGS.clear()
                    _SEEN_FILINGS.update(f["accession"] for f in listed)
            await asyncio.sleep(every)
