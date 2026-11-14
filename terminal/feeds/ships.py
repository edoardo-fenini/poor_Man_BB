"""Live ship positions at the sea lanes that move markets, from aisstream.io (free key)."""
import json
import time

import websockets

from terminal.feeds.base import safe

URL = "wss://stream.aisstream.io/v0/stream"
# name -> ((south, west), (north, east)). The whole ocean is a firehose; these are where cargo gets stuck.
CHOKEPOINTS = {
    "Strait of Hormuz": ((24.0, 54.0), (27.5, 58.5)),
    "Bab el-Mandeb": ((11.5, 42.0), (14.5, 45.5)),
    "Suez Canal": ((29.5, 32.0), (31.7, 33.0)),
    "Bosphorus": ((40.7, 28.5), (41.4, 29.5)),
    "Gibraltar": ((35.5, -6.5), (36.5, -4.5)),
    "English Channel": ((49.5, -2.0), (51.5, 2.5)),
    "Malacca / Singapore": ((0.8, 99.5), (3.5, 104.5)),
    "Taiwan Strait": ((22.5, 117.5), (26.0, 121.0)),
    "Panama Canal": ((8.5, -80.2), (9.7, -79.2)),
    "Cape of Good Hope": ((-35.5, 17.0), (-33.0, 20.5)),
    "Houston / Gulf": ((28.5, -95.5), (30.0, -93.5)),
    "Rotterdam": ((51.7, 3.0), (52.2, 4.6)),
}
KEEP_S = 1800   # a ship silent this long has left the box or switched off
MAX_SHIPS = 8000


def ship_class(ais_type: int) -> str:
    if 80 <= ais_type <= 89:
        return "tanker"
    if 70 <= ais_type <= 79:
        return "cargo"
    if 60 <= ais_type <= 69:
        return "passenger"
    return "other"


def parse(message: dict) -> dict | None:
    """One AIS message as an update for a ship: position, or name/type/destination."""
    meta = message["MetaData"]
    update = {"id": str(meta["MMSI"]), "name": str(meta.get("ShipName") or "").strip()}
    kind, body = message["MessageType"], message["Message"]
    if kind == "PositionReport":
        report = body["PositionReport"]
        update.update(lat=float(meta["latitude"]), lon=float(meta["longitude"]),
                      speed=float(report.get("Sog") or 0), course=float(report.get("Cog") or 0))
    elif kind == "ShipStaticData":
        static = body["ShipStaticData"]
        update.update(cls=ship_class(int(static.get("Type") or 0)),
                      dest=str(static.get("Destination") or "").strip(" @"))
    else:
        return None
    return update


class Fleet:
    """Latest known state per ship."""

    def __init__(self):
        self.ships: dict[str, dict] = {}

    def update(self, change: dict, now: float) -> None:
        ship = self.ships.setdefault(change["id"], {"cls": "other", "dest": ""})
        ship.update({k: v for k, v in change.items() if v != ""}, ts=now)
        if len(self.ships) > MAX_SHIPS:
            self.prune(now)
            for key in list(self.ships)[: len(self.ships) - MAX_SHIPS]:
                del self.ships[key]

    def prune(self, now: float) -> None:
        for key in [k for k, s in self.ships.items() if now - s["ts"] > KEEP_S]:
            del self.ships[key]

    def positions(self, now: float) -> list[dict]:
        self.prune(now)
        return [s for s in self.ships.values() if "lat" in s]

    def traffic(self, now: float) -> list[dict]:
        """Ships per chokepoint: how many, how many tankers, how many stopped (under 1 knot)."""
        ships = self.positions(now)
        out = []
        for name, ((south, west), (north, east)) in CHOKEPOINTS.items():
            inside = [s for s in ships if south <= s["lat"] <= north and west <= s["lon"] <= east]
            out.append({"name": name, "lat": (south + north) / 2, "lon": (west + east) / 2, "ships": len(inside),
                        "tankers": sum(s["cls"] == "tanker" for s in inside),
                        "cargo": sum(s["cls"] == "cargo" for s in inside),
                        "stopped": sum(s.get("speed", 0) < 1 for s in inside)})
        return out


async def stream(key: str, fleet: Fleet) -> None:
    async with websockets.connect(URL, open_timeout=15) as ws:
        await ws.send(json.dumps({
            "APIKey": key,
            "BoundingBoxes": [[list(a), list(b)] for a, b in CHOKEPOINTS.values()],
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"]}))
        async for raw in ws:
            message = json.loads(raw)
            if "error" in message:
                raise ConnectionError(f"aisstream: {message['error']}")
            for change in safe(parse, message, "ships"):
                fleet.update(change, time.time())
