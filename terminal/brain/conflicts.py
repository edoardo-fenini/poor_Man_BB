"""Conflict zones: how many conflict headlines today, and how that compares with the last week."""
import time
import tomllib
from pathlib import Path

from terminal.events import ConflictEvent
from terminal.store import Store

ZONES_FILE = Path(__file__).parent.parent / "data" / "zones.toml"
DAY = 86400
KEEP_DAYS = 30
MIN_HISTORY_DAYS = 3


def load_zones() -> list[dict]:
    return tomllib.loads(ZONES_FILE.read_text())["zone"]


def heat(day_counts: dict[str, int], today: int) -> tuple[int, float]:
    """(today's count, today / mean of the previous week's days that have data).
    Ratio is 0.0 until three such days exist: a new install or a week off is not a calm week."""
    count = day_counts.get(str(today), 0)
    past = [day_counts[str(today - d)] for d in range(1, 8) if day_counts.get(str(today - d))]
    if len(past) < MIN_HISTORY_DAYS:
        return count, 0.0
    return count, round(count / (sum(past) / len(past)), 2)


class Zones:
    def __init__(self, zones: list[dict], store: Store):
        self.zones = {z["name"]: z for z in zones}
        self.store = store

    def _state(self, name: str, counts: dict, today: int) -> dict:
        count, ratio = heat(counts.get(name, {}), today)
        return {"zone": name, "count": count, "ratio": ratio, "assets": self.zones[name]["assets"]}

    def record(self, ev: ConflictEvent) -> dict | None:
        """Count one new headline. Returns the zone's state, or None for a zone that is not configured."""
        if ev.zone not in self.zones:
            return None
        counts = self.store.get("conflict_counts", {})
        today = int(ev.ts // DAY)
        days = counts.setdefault(ev.zone, {})
        days[str(today)] = days.get(str(today), 0) + 1
        for day in [d for d in days if int(d) < today - KEEP_DAYS + 1]:
            del days[day]
        self.store.set("conflict_counts", counts)
        return self._state(ev.zone, counts, today)

    def state(self, now: float | None = None) -> list[dict]:
        counts = self.store.get("conflict_counts", {})
        today = int((time.time() if now is None else now) // DAY)
        return [self._state(name, counts, today) for name in self.zones]
