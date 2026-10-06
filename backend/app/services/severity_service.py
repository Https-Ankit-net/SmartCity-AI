"""Automated severity scoring (1–10) for complaints.

The score adds up four explainable signals and clamps the total to 1–10:

* **Category hazard**: how dangerous this kind of incident usually is (fire > garbage).
* **Description keywords**: "fire", "danger", "blocked highway", "injured"… (negations such
  as "no fire" are ignored). Capped so a wall of keywords can't max the score alone.
* **Image detection**: a confident detection of something relevant in the photo.
* **Proximity to key infrastructure**: near a hospital, school, emergency service, transit
  hub, substation or on a major road (see ``infrastructure.py``).

Every contribution is returned as a factor so staff can see *why* a score is what it is.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from app.services.infrastructure import get_index

CATEGORY_BASE = {
    "fire": 5,
    "accident": 5,
    "electrical": 4,
    "water": 3,
    "road": 3,
    "garbage": 2,
    "general": 2,
}

# (phrase, points). Longer phrases are matched first, so "blocked highway" wins over "blocked".
KEYWORDS: tuple[tuple[str, int], ...] = (
    # life-threatening
    ("fire", 3), ("explosion", 3), ("electrocution", 3), ("electrocuted", 3), ("live wire", 3),
    ("gas leak", 3), ("collapsed", 3), ("collapse", 3), ("trapped", 3), ("injured", 3),
    ("bleeding", 3), ("unconscious", 3), ("dead body", 3),
    # dangerous / blocking
    ("blocked highway", 3), ("highway blocked", 3), ("danger", 2), ("dangerous", 2), ("hazard", 2),
    ("smoke", 2), ("sparking", 2), ("sparks", 2), ("flood", 2), ("flooding", 2), ("accident", 2),
    ("blocked road", 2), ("road blocked", 2), ("fallen tree", 2), ("tree fell", 2), ("children", 2),
    ("ambulance", 2), ("emergency", 2), ("urgent", 1), ("urgently", 1), ("immediately", 1), ("asap", 1),
    # nuisance
    ("blocked", 1), ("overflowing", 1), ("leak", 1), ("leaking", 1), ("sewage", 1), ("pothole", 1),
    ("broken", 1), ("not working", 1), ("stopped working", 1), ("not functioning", 1), ("dark", 1), ("stench", 1), ("mosquito", 1),
)
KEYWORD_CAP = 4
NEGATIONS = re.compile(r"\b(no|not|without|never|isn't|wasn't|aren't)\s+(\w+\s+)?$")

# Image labels that corroborate a civic incident (COCO + the fine-tuned civic classes).
RELEVANT_DETECTIONS = {
    "pothole", "overflowing_trash", "streetlight_failure", "fallen_tree",
    "car", "bus", "truck", "motorcycle", "bicycle", "fire hydrant", "traffic light",
}

# kind -> [(within metres, points)], best (first matching) band wins per kind.
POINT_BANDS = {
    "hospital": ((100, 2), (250, 1)),
    "school": ((100, 2), (250, 1)),
    "emergency_service": ((100, 1),),
    "transit_hub": ((150, 1),),
    "utility": ((75, 2), (150, 1)),
}
ROAD_BANDS = ((25, 2), (60, 1))
ROAD_SENSITIVE = {"road", "accident", "fire", "electrical"}
PROXIMITY_CAP = 3
SEARCH_RADIUS_M = 250
KIND_LABEL = {
    "hospital": "hospital",
    "school": "school",
    "emergency_service": "fire/police station",
    "transit_hub": "transit hub",
    "utility": "power substation",
}


@dataclass
class SeverityResult:
    score: int
    level: str
    factors: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def level_for(score: int) -> str:
    if score >= 8:
        return "Critical"
    if score >= 6:
        return "High"
    if score >= 4:
        return "Medium"
    return "Low"


def priority_for(score: int) -> str:
    """Complaint priority band implied by a severity score."""
    return "High" if score >= 7 else "Medium" if score >= 4 else "Low"


def keyword_hits(text: str) -> list[tuple[str, int]]:
    """Matched (phrase, points), longest phrases first, overlapping and negated matches skipped."""
    lowered = text.lower()
    taken: list[tuple[int, int]] = []
    hits: list[tuple[str, int]] = []
    for phrase, points in sorted(KEYWORDS, key=lambda kp: -len(kp[0])):
        for match in re.finditer(rf"\b{re.escape(phrase)}\b", lowered):
            span = match.span()
            if any(s < span[1] and span[0] < e for s, e in taken):
                continue
            if NEGATIONS.search(lowered[: span[0]]):
                continue
            taken.append(span)
            hits.append((phrase, points))
            break
    return hits


def score_severity(
    *,
    category: str,
    text: str,
    detection_label: str | None = None,
    detection_confidence: float | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
) -> SeverityResult:
    factors: list[dict] = []

    base = CATEGORY_BASE.get(category, 2)
    factors.append({"signal": "category", "detail": f"{category} incident", "points": base})

    hits = keyword_hits(text or "")
    keyword_points = 0
    for phrase, points in sorted(hits, key=lambda h: -h[1]):
        add = min(points, KEYWORD_CAP - keyword_points)
        if add <= 0:
            break
        keyword_points += add
        factors.append({"signal": "keyword", "detail": f"mentions “{phrase}”", "points": add})

    if detection_label in RELEVANT_DETECTIONS and detection_confidence:
        points = 2 if detection_confidence >= 0.75 else 1 if detection_confidence >= 0.4 else 0
        if points:
            factors.append({
                "signal": "image",
                "detail": f"photo shows {detection_label.replace('_', ' ')} ({detection_confidence:.0%})",
                "points": points,
            })

    if latitude is not None and longitude is not None:
        index = get_index()
        proximity: list[dict] = []
        best_per_kind: dict[str, dict] = {}
        for near in index.nearest_points(latitude, longitude, SEARCH_RADIUS_M):
            for within, points in POINT_BANDS.get(near.kind, ()):
                if near.distance_m <= within:
                    current = best_per_kind.get(near.kind)
                    if current is None or points > current["points"]:
                        name = f" ({near.name})" if near.name else ""
                        best_per_kind[near.kind] = {
                            "signal": "proximity",
                            "detail": f"{near.distance_m:.0f} m from {KIND_LABEL[near.kind]}{name}",
                            "points": points,
                        }
                    break
        proximity.extend(best_per_kind.values())
        road = index.nearest_road(latitude, longitude, ROAD_BANDS[-1][0])
        if road is not None:
            for within, points in ROAD_BANDS:
                if road.distance_m <= within:
                    if category not in ROAD_SENSITIVE:
                        points -= 1
                    if points > 0:
                        name = f" ({road.name})" if road.name else ""
                        proximity.append({
                            "signal": "proximity",
                            "detail": f"{road.distance_m:.0f} m from a {road.kind} road{name}",
                            "points": points,
                        })
                    break
        used = 0
        for factor in sorted(proximity, key=lambda f: -f["points"]):
            add = min(factor["points"], PROXIMITY_CAP - used)
            if add <= 0:
                break
            used += add
            factors.append({**factor, "points": add})

    score = max(1, min(10, sum(f["points"] for f in factors)))
    return SeverityResult(score=score, level=level_for(score), factors=factors)


def bump_for_confirmations(result_score: int, factors: list[dict] | None, confirmations: int) -> tuple[int, list[dict]]:
    """Crowd signal: +1 once three or more other citizens have confirmed the same issue."""
    factors = list(factors or [])
    if confirmations >= 3 and not any(f.get("signal") == "crowd" for f in factors):
        factors.append({"signal": "crowd", "detail": f"confirmed by {confirmations} other citizens", "points": 1})
        return min(10, result_score + 1), factors
    return result_score, factors
