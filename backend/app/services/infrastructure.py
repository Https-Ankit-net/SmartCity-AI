"""Nearest key infrastructure (hospitals, schools, major roads…) for a complaint location.

Data comes from ``app/ai/data/infrastructure.json`` (generated from OpenStreetMap by
``scripts/fetch_infrastructure.py``). Points and road vertices are bucketed into a
coarse grid so a lookup only checks nearby cells.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "ai" / "data" / "infrastructure.json"
EARTH_RADIUS_M = 6_371_000
CELL_DEG = 0.005  # ~550 m grid cells


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _segment_distance_m(lat: float, lng: float, a: tuple[float, float], b: tuple[float, float]) -> float:
    """Point-to-segment distance using a local equirectangular projection (fine at city scale)."""
    k = math.cos(math.radians(lat))
    ax, ay = (a[1] - lng) * k, a[0] - lat
    bx, by = (b[1] - lng) * k, b[0] - lat
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length2))
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px, py) * math.pi / 180 * EARTH_RADIUS_M


@dataclass(frozen=True)
class Nearby:
    kind: str
    name: str | None
    distance_m: float


class InfrastructureIndex:
    def __init__(self, points: list[dict], roads: list[dict]) -> None:
        self.points = points
        self.point_cells: dict[tuple[int, int], list[dict]] = {}
        for p in points:
            self.point_cells.setdefault(self._cell(p["lat"], p["lng"]), []).append(p)
        self.road_cells: dict[tuple[int, int], list[tuple[dict, tuple[float, float], tuple[float, float]]]] = {}
        for road in roads:
            coords = [tuple(c) for c in road["coords"]]
            for a, b in zip(coords, coords[1:]):
                for cell in {self._cell(*a), self._cell(*b)}:
                    self.road_cells.setdefault(cell, []).append((road, a, b))

    @staticmethod
    def _cell(lat: float, lng: float) -> tuple[int, int]:
        return int(math.floor(lat / CELL_DEG)), int(math.floor(lng / CELL_DEG))

    def _cells_around(self, lat: float, lng: float, radius_m: float):
        span = int(math.ceil(radius_m / 111_000 / CELL_DEG)) + 1
        ci, cj = self._cell(lat, lng)
        for i in range(ci - span, ci + span + 1):
            for j in range(cj - span, cj + span + 1):
                yield i, j

    def nearest_points(self, lat: float, lng: float, radius_m: float) -> list[Nearby]:
        found: dict[tuple[str, str | None], Nearby] = {}
        for cell in self._cells_around(lat, lng, radius_m):
            for p in self.point_cells.get(cell, ()):
                d = haversine_m(lat, lng, p["lat"], p["lng"])
                if d <= radius_m:
                    key = (p["kind"], p.get("name"))
                    if key not in found or d < found[key].distance_m:
                        found[key] = Nearby(p["kind"], p.get("name"), d)
        return sorted(found.values(), key=lambda n: n.distance_m)

    def nearest_road(self, lat: float, lng: float, radius_m: float) -> Nearby | None:
        best: Nearby | None = None
        seen: set[tuple] = set()
        for cell in self._cells_around(lat, lng, radius_m):
            for road, a, b in self.road_cells.get(cell, ()):
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                d = _segment_distance_m(lat, lng, a, b)
                if d <= radius_m and (best is None or d < best.distance_m):
                    best = Nearby(road["kind"], road.get("name"), d)
        return best


@lru_cache(maxsize=1)
def get_index() -> InfrastructureIndex:
    path = Path(os.getenv("INFRASTRUCTURE_FILE", DEFAULT_PATH))
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("Infrastructure file %s not found; proximity scoring disabled", path)
        return InfrastructureIndex([], [])
    except (OSError, ValueError) as exc:
        logger.warning("Infrastructure file %s unreadable (%s); proximity scoring disabled", path, exc)
        return InfrastructureIndex([], [])
    return InfrastructureIndex(data.get("points", []), data.get("roads", []))
