"""Aircraft positions from adsb.lol (free, no key): around a point, and military worldwide."""
import httpx

API = "https://api.adsb.lol/v2"
MAX_RADIUS_NM = 250


def parse(payload: dict) -> list[dict]:
    out = []
    for ac in payload.get("ac") or []:
        lat, lon = ac.get("lat"), ac.get("lon")
        if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        alt = ac.get("alt_baro")
        out.append({"id": str(ac.get("hex", "")), "flight": str(ac.get("flight") or "").strip(),
                    "reg": str(ac.get("r") or ""), "type": str(ac.get("t") or ""), "lat": lat, "lon": lon,
                    "alt": alt if isinstance(alt, (int, float)) else 0,  # "ground" becomes 0
                    "speed": ac.get("gs") or 0, "track": ac.get("track") or 0,
                    "mil": bool(int(ac.get("dbFlags") or 0) & 1)})
    return out


async def around(client: httpx.AsyncClient, lat: float, lon: float, radius_nm: float) -> list[dict]:
    radius = max(1, min(int(radius_nm), MAX_RADIUS_NM))
    response = await client.get(f"{API}/point/{lat:.3f}/{lon:.3f}/{radius}")
    response.raise_for_status()
    return parse(response.json())


async def military(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get(f"{API}/mil")
    response.raise_for_status()
    return [{**ac, "mil": True} for ac in parse(response.json())]
