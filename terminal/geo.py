"""Find the place a headline is about, so news can sit on the map."""
import csv
import functools
import re
from pathlib import Path

PLACES_FILE = Path(__file__).parent / "data" / "places.csv"


@functools.cache
def _places() -> tuple[dict[str, tuple[float, float]], re.Pattern]:
    with PLACES_FILE.open(newline="") as fh:
        places = {row["name"]: (float(row["lat"]), float(row["lon"])) for row in csv.DictReader(fh)}
    # longest first, so "South Korea" wins over "Korea"; exact case, so "Turkey" the bird stays off the map
    names = sorted(places, key=len, reverse=True)
    return places, re.compile(r"(?<![\w.])(" + "|".join(re.escape(n) for n in names) + r")(?![\w])")


def locate(text: str) -> tuple[str, float, float] | None:
    """(place, lat, lon) for the first place named in the text."""
    places, pattern = _places()
    found = pattern.search(text)
    if not found:
        return None
    return found.group(1), *places[found.group(1)]
