"""What an analyser says about one news story. The rules engine is one implementation;
a local AI model can be another without any caller changing."""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from terminal.events import NewsItem


@dataclass(frozen=True)
class Impact:
    symbol: str
    direction: int  # -1 down, 0 affected with unclear direction, +1 up
    reason: str


@dataclass(frozen=True)
class Analysis:
    impacts: list[Impact]
    sentiment: int  # -1 | 0 | +1
    weight: int  # 1 (noise) .. 5 (drop everything)
    reasons: list[str]


@runtime_checkable
class Analyser(Protocol):
    def analyse(self, item: NewsItem, watchlist: set[str], outlets: int = 1) -> Analysis: ...
