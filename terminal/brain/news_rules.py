"""Rules-only news analysis: who is the story about, which themes does it touch, what is the mood."""
import csv
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from terminal.brain.analysis import Analysis, Impact
from terminal.events import NewsItem

DATA_DIR = Path(__file__).parent.parent / "data"
# Any company story is likely to mention one of these, so they count as context for ambiguous names.
GENERIC_CONTEXT = ("shares", "stock", "earnings", "revenue", "profit", "sales", "ceo", "inc", "corp", "quarter",
                   "quarterly", "investors", "analysts", "guidance", "forecast", "dividend", "buyback", "staff",
                   "layoffs", "employees", "workers", "jobs", "shareholders", "market", "markets")
# Tickers that are also everyday words: only a company name or a $cashtag may tag these.
TICKER_STOP = frozenset("ALL NOW LOW COST CAT COIN HOOD SNOW NET DASH SHOP SPOT SNAP PINS ARM DELL UBER LYFT VALE "
                        "RIO RACE SONY UPS".split())
STRONG = frozenset("crash plunge surge soar halt default bankrupt emergency war invasion record".split())
NEGATORS = ("not", "no", "never", "without", "fails", "failed")
_SUFFIXES = ("", "s", "es", "ed", "d", "ing", "ies")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9'&-]*")
_CASHTAG = re.compile(r"\$([A-Z]{1,5}(?:\.[A-Z])?)\b")
_BARE = re.compile(r"\b[A-Z]{3,5}\b")
MAX_TEXT = 2000


@dataclass(frozen=True)
class Entity:
    symbol: str
    names: tuple[re.Pattern, ...]
    ambiguous: bool
    context: tuple[str, ...]


def _phrase(text: str) -> re.Pattern:
    """Whole-word match. Lowercase text is matched in any case; text with a capital must match exactly."""
    flags = 0 if any(ch.isupper() for ch in text) else re.I
    return re.compile(r"(?<![\w$])" + re.escape(text) + r"(?!\w)", flags)


def _lines(path: Path) -> list[str]:
    return [ln.strip().lower() for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith("#")]


class RulesAnalyser:
    def __init__(self, data_dir: Path = DATA_DIR):
        with open(data_dir / "entities.csv", newline="") as fh:
            self.entities = [
                Entity(row["symbol"], tuple(_phrase(n) for n in row["names"].split("|") if n),
                       row["ambiguous"] == "1", tuple(c.lower() for c in row["context"].split("|") if c))
                for row in csv.DictReader(fh)
            ]
        self._tickers = {e.symbol for e in self.entities if not e.ambiguous} - TICKER_STOP
        self._symbols = {e.symbol for e in self.entities}
        self.themes = tomllib.loads((data_dir / "themes.toml").read_text())["theme"]
        for theme in self.themes:
            theme["_patterns"] = [(k, _phrase(k)) for k in theme["keywords"]]
        self._words, self._phrases = {}, []
        for filename, score in (("words_positive.txt", 1), ("words_negative.txt", -1)):
            for entry in _lines(data_dir / filename):
                if " " in entry:
                    self._phrases.append((_phrase(entry), entry, score))
                else:
                    self._words[entry] = score
        feeds = tomllib.loads((data_dir / "rss_feeds.toml").read_text())["feed"]
        self._tiers = {f["name"]: f["tier"] for f in feeds}

    # ---- sentiment
    def _word_score(self, token: str) -> int:
        for suffix in _SUFFIXES:
            if suffix and not token.endswith(suffix):
                continue
            stem = token[: len(token) - len(suffix)] if suffix else token
            if stem in self._words:
                return self._words[stem]
            if suffix == "ies" and stem + "y" in self._words:
                return self._words[stem + "y"]
        return 0

    def _sentiment(self, text: str) -> tuple[int, list[str], bool]:
        lowered, score, reasons = text.lower(), 0, []
        for pattern, phrase, value in self._phrases:
            if pattern.search(lowered):
                score += value
                reasons.append(f"mood {'+' if value > 0 else '-'}: \"{phrase}\"")
                lowered = pattern.sub(" ", lowered)  # a phrase's words are not counted again
        tokens = _TOKEN.findall(lowered)
        strong = False
        for i, token in enumerate(tokens):
            strong = strong or any(token in (s, s + "s", s + "d", s + "ed", s + "ing", s[:-1] + "ing") for s in STRONG)
            value = self._word_score(token)
            if value and not any(t in NEGATORS for t in tokens[max(0, i - 3):i]):  # "not expect losses": no signal
                score += value
                reasons.append(f"mood {'+' if value > 0 else '-'}: \"{token}\"")
        return (score > 0) - (score < 0), reasons, strong

    # ---- who and what
    def _entities(self, text: str, sentiment: int, impacts: dict[str, Impact]) -> None:
        lowered = text.lower()
        for entity in self.entities:
            name = next((m.group() for p in entity.names if (m := p.search(text))), None)
            if not name:
                continue
            reason = f"entity {entity.symbol}: \"{name}\""
            if entity.ambiguous:
                context = next((c for c in entity.context + GENERIC_CONTEXT if _phrase(c).search(lowered)), None)
                if not context:
                    continue
                reason += f" + context \"{context}\""
            impacts.setdefault(entity.symbol, Impact(entity.symbol, sentiment, reason))
        for ticker in _BARE.findall(text):
            if ticker in self._tickers:
                impacts.setdefault(ticker, Impact(ticker, sentiment, f"ticker {ticker}"))
        for ticker in _CASHTAG.findall(text):
            impacts.setdefault(ticker, Impact(ticker, sentiment, f"cashtag ${ticker}"))

    def _themes(self, text: str, impacts: dict[str, Impact]) -> None:
        for theme in self.themes:
            keyword = next((k for k, p in theme["_patterns"] if p.search(text)), None)
            if keyword:
                for impact in theme["impacts"]:
                    impacts.setdefault(impact["symbol"], Impact(
                        impact["symbol"], impact["direction"], f"theme {theme['name']}: \"{keyword}\""))

    def analyse(self, item: NewsItem, watchlist: set[str], outlets: int = 1) -> Analysis:
        text = f"{item.title}. {item.summary}"[:MAX_TEXT]
        sentiment, reasons, strong = self._sentiment(item.title[:MAX_TEXT])  # the headline carries the mood
        impacts: dict[str, Impact] = {}
        self._entities(text, sentiment, impacts)
        self._themes(text, impacts)

        weight = 1
        if self._tiers.get(item.source, 2) == 1:
            weight += 1
            reasons.append(f"weight +1: top-tier source {item.source}")
        if strong:
            weight += 1
            reasons.append("weight +1: strong wording")
        if outlets >= 3:
            weight += 1
            reasons.append(f"weight +1: carried by {outlets} outlets")
        if impacts.keys() & watchlist:
            weight += 1
            reasons.append("weight +1: hits your watchlist")
        ordered = list(impacts.values())
        return Analysis(ordered, sentiment, min(weight, 5), [i.reason for i in ordered] + reasons)
