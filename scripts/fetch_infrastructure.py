"""Download key city infrastructure from OpenStreetMap for the severity scorer.

Severity scoring raises a complaint's score when it is close to a hospital, school,
fire/police station, transit hub, power substation or a major road. This script
pulls those features for one city from the Overpass API and writes them to
``backend/app/ai/data/infrastructure.json``, which the backend loads at startup.

Usage (defaults to Bhubaneswar, where the map is centred):

    python scripts/fetch_infrastructure.py
    python scripts/fetch_infrastructure.py --bbox 20.15,85.70,20.45,85.95
    python scripts/fetch_infrastructure.py --bbox 12.83,77.46,13.14,77.78 --out other.json

Data (c) OpenStreetMap contributors, available under the Open Database License (ODbL).
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DEFAULT_BBOX = (20.15, 85.70, 20.45, 85.95)  # south, west, north, east: Bhubaneswar
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "backend" / "app" / "ai" / "data" / "infrastructure.json"

# OSM tag -> infrastructure kind understood by app/services/infrastructure.py
POINT_KINDS = {
    ("amenity", "hospital"): "hospital",
    ("amenity", "clinic"): "hospital",
    ("amenity", "school"): "school",
    ("amenity", "college"): "school",
    ("amenity", "university"): "school",
    ("amenity", "kindergarten"): "school",
    ("amenity", "fire_station"): "emergency_service",
    ("amenity", "police"): "emergency_service",
    ("amenity", "bus_station"): "transit_hub",
    ("railway", "station"): "transit_hub",
    ("power", "substation"): "utility",
}
MAJOR_ROADS = ("motorway", "trunk", "primary")


def build_query(south: float, west: float, north: float, east: float) -> str:
    bbox = f"{south},{west},{north},{east}"
    return f"""
[out:json][timeout:120];
(
  nwr["amenity"~"^(hospital|clinic|school|college|university|kindergarten|fire_station|police|bus_station)$"]({bbox});
  nwr["railway"="station"]({bbox});
  nwr["power"="substation"]({bbox});
);
out center tags;
way["highway"~"^({'|'.join(MAJOR_ROADS)})$"]({bbox});
out geom tags;
"""


def classify(tags: dict[str, str]) -> str | None:
    for (key, value), kind in POINT_KINDS.items():
        if tags.get(key) == value:
            return kind
    return None


def decimate(coords: list[list[float]], step: int = 2) -> list[list[float]]:
    """Keep every `step`-th vertex (plus the last) to shrink long road geometries."""
    if len(coords) <= 3:
        return coords
    kept = coords[::step]
    if kept[-1] != coords[-1]:
        kept.append(coords[-1])
    return kept


def convert(elements: list[dict]) -> tuple[list[dict], list[dict]]:
    points: list[dict] = []
    roads: list[dict] = []
    for el in elements:
        tags = el.get("tags", {})
        if el["type"] == "way" and tags.get("highway") in MAJOR_ROADS and "geometry" in el:
            coords = [[round(p["lat"], 6), round(p["lon"], 6)] for p in el["geometry"]]
            roads.append({"kind": tags["highway"], "name": tags.get("name") or tags.get("ref"), "coords": decimate(coords)})
            continue
        kind = classify(tags)
        if kind is None:
            continue
        lat, lng = (el.get("lat"), el.get("lon")) if el["type"] == "node" else (el.get("center", {}).get("lat"), el.get("center", {}).get("lon"))
        if lat is None or lng is None:
            continue
        points.append({"kind": kind, "name": tags.get("name"), "lat": round(lat, 6), "lng": round(lng, 6)})
    return points, roads


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bbox", help="south,west,north,east in decimal degrees", default=",".join(map(str, DEFAULT_BBOX)))
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--endpoint", default=OVERPASS_URL)
    args = parser.parse_args()

    south, west, north, east = (float(v) for v in args.bbox.split(","))
    if not (south < north and west < east):
        parser.error("bbox must be south,west,north,east with south < north and west < east")

    print(f"Querying Overpass for bbox {south},{west},{north},{east} …")
    body = urllib.parse.urlencode({"data": build_query(south, west, north, east)}).encode()
    request = urllib.request.Request(args.endpoint, data=body, headers={"User-Agent": "SmartCity-AI infrastructure fetcher"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            payload = json.load(response)
    except Exception as exc:  # network errors, Overpass rate limits
        print(f"Overpass request failed: {exc}", file=sys.stderr)
        return 1

    points, roads = convert(payload.get("elements", []))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {
                "source": "OpenStreetMap contributors via Overpass API (ODbL)",
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "bbox": [south, west, north, east],
                "points": points,
                "roads": roads,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    counts: dict[str, int] = {}
    for p in points:
        counts[p["kind"]] = counts.get(p["kind"], 0) + 1
    print(f"Wrote {args.out} ({args.out.stat().st_size // 1024} KB)")
    print("  points:", ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none")
    print(f"  major road segments: {len(roads)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
