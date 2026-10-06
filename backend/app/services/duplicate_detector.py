"""Find an existing active complaint that a new report duplicates.

A new report duplicates an existing complaint when all of these hold:

* same AI category (``complaint_type``),
* the existing complaint is still active (Pending or In Progress),
* it was filed within the last ``DUPLICATE_WINDOW`` (24 h),
* it lies within ``DUPLICATE_RADIUS_M`` (50 m) of the new report.

Reports without coordinates are never treated as duplicates.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.complaint import Complaint
from app.services.infrastructure import haversine_m

DUPLICATE_RADIUS_M = 50.0
DUPLICATE_WINDOW = timedelta(hours=24)
ACTIVE_STATUSES = ("pending", "in progress")


@dataclass(frozen=True)
class DuplicateMatch:
    complaint: Complaint
    distance_m: float


def find_duplicate(
    db: Session,
    *,
    category: str,
    latitude: float | None,
    longitude: float | None,
    now: datetime | None = None,
    radius_m: float = DUPLICATE_RADIUS_M,
    window: timedelta = DUPLICATE_WINDOW,
) -> DuplicateMatch | None:
    if latitude is None or longitude is None:
        return None
    now = now or datetime.now(timezone.utc)

    # Cheap bounding-box prefilter in SQL, exact great-circle distance in Python.
    dlat = radius_m / 111_320
    dlng = radius_m / (111_320 * max(math.cos(math.radians(latitude)), 1e-6))
    candidates = db.scalars(
        select(Complaint).where(
            Complaint.complaint_type == category,
            func.lower(Complaint.status).in_(ACTIVE_STATUSES),
            Complaint.created_at >= now - window,
            Complaint.latitude.is_not(None),
            Complaint.longitude.is_not(None),
            Complaint.latitude.between(latitude - dlat, latitude + dlat),
            Complaint.longitude.between(longitude - dlng, longitude + dlng),
        )
    ).all()

    best: DuplicateMatch | None = None
    for complaint in candidates:
        distance = haversine_m(latitude, longitude, float(complaint.latitude), float(complaint.longitude))
        if distance <= radius_m and (best is None or distance < best.distance_m):
            best = DuplicateMatch(complaint, distance)
    return best
