"""Earthquakes of magnitude 4.5+ in the last day, from the USGS (free, no key)."""
import httpx

API = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"


def parse(payload: dict) -> list[dict]:
    out = []
    for feature in payload.get("features") or []:
        props, coords = feature.get("properties") or {}, (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2 or props.get("mag") is None:
            continue
        out.append({"id": str(feature.get("id", "")), "lat": coords[1], "lon": coords[0], "mag": props["mag"],
                    "place": str(props.get("place") or ""), "ts": (props.get("time") or 0) / 1000,
                    "url": str(props.get("url") or "")})
    return out


async def recent(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get(API)
    response.raise_for_status()
    return parse(response.json())
