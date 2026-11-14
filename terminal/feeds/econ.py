"""Macro data: this week's economic calendar, the US Treasury yield curve, FRED statistics."""
import csv
import io
from datetime import date, datetime

import httpx

CALENDAR = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
TREASURY = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/"
            "{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv")
FRED = "https://api.stlouisfed.org/fred/series/observations"
# series id -> (label, how to show it): level, yoy (% change on a year), chg (change on the previous reading)
FRED_SERIES = {
    "CPIAUCSL": ("CPI inflation", "yoy"), "CPILFESL": ("Core CPI inflation", "yoy"),
    "PCEPILFE": ("Core PCE inflation", "yoy"), "UNRATE": ("Unemployment rate %", "level"),
    "PAYEMS": ("Nonfarm payrolls, monthly change (thousands)", "chg"), "ICSA": ("Initial jobless claims", "level"),
    "GDPC1": ("Real GDP", "yoy"), "INDPRO": ("Industrial production", "yoy"), "RSAFS": ("Retail sales", "yoy"),
    "HOUST": ("Housing starts (thousands)", "level"), "UMCSENT": ("Consumer sentiment", "level"),
    "FEDFUNDS": ("Fed funds rate %", "level"), "DGS10": ("10-year Treasury yield %", "level"),
    "T10Y2Y": ("10y minus 2y spread", "level"), "BAMLH0A0HYM2": ("High-yield credit spread %", "level"),
    "BAMLC0A0CM": ("Investment-grade credit spread %", "level"), "MORTGAGE30US": ("30-year mortgage rate %", "level"),
    "M2SL": ("M2 money supply", "yoy"), "WALCL": ("Fed balance sheet ($ millions)", "level"),
    "DTWEXBGS": ("Broad dollar index", "level"),
}


def parse_calendar(payload: list) -> list[dict]:
    out = []
    for item in payload:
        out.append({"ts": datetime.fromisoformat(item["date"]).timestamp(), "currency": str(item.get("country", "")),
                    "impact": str(item.get("impact", "")), "title": str(item.get("title", "")),
                    "forecast": str(item.get("forecast", "")), "previous": str(item.get("previous", ""))})
    return sorted(out, key=lambda e: e["ts"])


async def calendar(client: httpx.AsyncClient) -> list[dict]:
    response = await client.get(CALENDAR)
    response.raise_for_status()
    return parse_calendar(response.json())


def parse_curve(text: str) -> list[tuple[str, dict[str, float]]]:
    """[(iso date, {maturity: yield %})], oldest first."""
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        day = datetime.strptime(row.pop("Date"), "%m/%d/%Y").date().isoformat()
        points = {k: float(v) for k, v in row.items() if v not in ("", "N/A", None)}
        if points:
            out.append((day, points))
    return sorted(out)


async def curve(client: httpx.AsyncClient, today: date) -> list[tuple[str, dict[str, float]]]:
    """This year's daily curves, plus last year's so a year-ago comparison exists in January too."""
    rows = []
    for year in (today.year - 1, today.year):
        response = await client.get(TREASURY.format(year=year))
        response.raise_for_status()
        rows += parse_curve(response.text)
    return rows


def parse_fred(payload: dict) -> list[tuple[str, float]]:
    """[(date, value)], oldest first, without the "." FRED uses for a missing reading."""
    return [(o["date"], float(o["value"])) for o in reversed(payload["observations"]) if o["value"] != "."]


async def fred(client: httpx.AsyncClient, key: str, series: str) -> list[tuple[str, float]]:
    response = await client.get(FRED, params={"series_id": series, "api_key": key, "file_type": "json",
                                              "sort_order": "desc", "limit": 60})
    response.raise_for_status()
    return parse_fred(response.json())
