"""Typed events that flow over the bus. Timestamps are epoch seconds, UTC."""
from dataclasses import asdict, dataclass
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class Tick:
    kind: ClassVar[str] = "tick"
    symbol: str
    price: float
    size: float
    ts: float
    source: str


@dataclass(frozen=True, slots=True)
class Candle:
    kind: ClassVar[str] = "candle"
    symbol: str
    interval: str
    ts: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class NewsItem:
    kind: ClassVar[str] = "news"
    id: str
    title: str
    url: str
    source: str
    ts: float
    summary: str


@dataclass(frozen=True, slots=True)
class WhaleEvent:
    kind: ClassVar[str] = "whale"
    id: str
    type: str  # crypto_trade | onchain | insider | fund | volume
    symbol: str
    usd: float
    side: str  # buy | sell | ""
    detail: str
    url: str
    ts: float


@dataclass(frozen=True, slots=True)
class ConflictEvent:
    kind: ClassVar[str] = "conflict"
    id: str
    zone: str
    type: str
    title: str
    url: str
    ts: float


@dataclass(frozen=True, slots=True)
class Alert:
    kind: ClassVar[str] = "alert"
    key: str
    severity: str  # info | warn | critical
    title: str
    body: str
    symbol: str
    ts: float


@dataclass(frozen=True, slots=True)
class FeedStatus:
    kind: ClassVar[str] = "status"
    feed: str
    state: str  # ok | down | disabled | closed
    detail: str
    ts: float


def to_json(event) -> dict:
    return {"kind": event.kind, **asdict(event)}
