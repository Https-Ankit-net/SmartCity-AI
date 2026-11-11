"""Seed realistic demo data for a presentation: citizens, department staff and ~45 complaints across Bhubaneswar.

Every complaint goes through the backend's real pipeline pieces: the photo is classified by the
fine-tuned civic model (``analyze_incident``), severity comes from ``score_severity`` (including the
OpenStreetMap proximity data), and staff actions are written as ``ComplaintUpdate`` history, so the
dashboard's response/resolution times, trends and escalations are all computed from the data.
Photos come from the held-out test split of the civic dataset (CC BY 4.0, see docs/dataset_sources.md).

    # from the repo root, with the app's virtualenv (the server should be stopped)
    .venv/Scripts/python.exe scripts/seed_demo_data.py --replace-test-complaints

* The SQLite database is backed up first (backend/backups/, git-ignored as *.db).
* Demo accounts use the e-mail domain @demo.example.com; re-running replaces earlier demo data.
* ``--replace-test-complaints`` also deletes the old identical "Live Complaint" test rows.
* All demo accounts share one password: DEMO_PASSWORD from the environment, default ``Demo@2026``.
* ``--remove`` deletes the demo data (and its photos) and stops.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)  # the app resolves sqlite:///./smartcity_ai.db and .env relative to backend/

from sqlalchemy import delete, select  # noqa: E402

from app.ai.incident_classifier import analyze_incident  # noqa: E402
from app.ai.yolo_model import detect_image  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.initialize import initialize_database  # noqa: E402
from app.db.database import engine  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.complaint import Complaint  # noqa: E402
from app.models.complaint_confirmation import ComplaintConfirmation  # noqa: E402
from app.models.complaint_update import ComplaintUpdate  # noqa: E402
from app.models.department import Department  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.complaint_service import _priority  # noqa: E402
from app.services.file_service import UPLOAD_DIRECTORY, remove_upload  # noqa: E402
from app.services.severity_service import bump_for_confirmations, score_severity  # noqa: E402

DOMAIN = "@demo.example.com"
SEED = 2026
CLASSES = ["pothole", "overflowing_trash", "streetlight_failure", "fallen_tree"]
TEST_IMAGES = ROOT / "datasets" / "civic" / "images" / "test"
TEST_LABELS = ROOT / "datasets" / "civic" / "labels" / "test"
# Sources that look out of place in a city demo: CCTV frames with on-screen Chinese captions, forest/stock trees.
SKIP_SOURCES = ("bin-b4qnf__", "of-a-fallen-tree__")

LOCALITIES = {
    "Master Canteen Square": (20.2700, 85.8433), "Jaydev Vihar": (20.2985, 85.8188), "Patia": (20.3540, 85.8195),
    "Saheed Nagar": (20.2895, 85.8445), "Khandagiri": (20.2560, 85.7790), "Old Town": (20.2382, 85.8338),
    "Nayapalli": (20.2950, 85.8100), "Rasulgarh": (20.2930, 85.8620), "Chandrasekharpur": (20.3250, 85.8160),
    "Unit-1 Market": (20.2690, 85.8340), "Vani Vihar": (20.2970, 85.8400), "Acharya Vihar": (20.2960, 85.8270),
    "Baramunda": (20.2830, 85.7980), "Pokhariput": (20.2470, 85.8020), "Sailashree Vihar": (20.3330, 85.8180),
    "Infocity": (20.3460, 85.8050), "Kalinga Nagar": (20.2700, 85.7780), "Bomikhal": (20.2850, 85.8560),
}

CITIZENS = [
    "Subhashree Mohanty", "Rakesh Panda", "Priyanka Nayak", "Bikash Sahoo", "Sneha Mishra", "Debasish Pradhan",
    "Ananya Patnaik", "Satyajit Behera", "Lipsa Rout", "Manoj Swain", "Itishree Das", "Soumya Ranjan Jena",
]
# (name, department, account status). Pending ones show up in the admin's staff-approval queue.
STAFF = [
    ("Prakash Mohapatra", "Public Works Department", "active"),
    ("Smita Tripathy", "Sanitation Department", "active"),
    ("Ajay Kumar Dash", "Electrical Department", "active"),
    ("Rashmi Samal", "Water Department", "active"),
    ("Sanjay Barik", "Traffic Police", "active"),
    ("Pradeep Senapati", "Fire Services", "active"),
    ("Ritika Acharya", "Water Department", "pending"),
    ("Abhishek Parida", "Electrical Department", "pending"),
]

# (photo class or None, title, description). The description is what the citizen typed.
REPORTS = [
    ("pothole", "Deep pothole near the bus stop", "Big pothole right where buses stop, two-wheelers are swerving into traffic. Dangerous at night."),
    ("pothole", "Road broken after rain", "The road surface has broken up after last week's rain, many potholes in a row."),
    ("pothole", "Pothole in front of school gate", "There is a pothole in front of the school gate, children have to walk around it."),
    ("pothole", "Please repair this road", "Please check this road, it is getting worse every day."),
    ("pothole", "Huge crater on service road", "A huge crater has formed on the service road, cars are getting damaged."),
    ("pothole", "Pothole filled with water", "Pothole full of rainwater, you can't see how deep it is."),
    ("pothole", "Road damage near market", "Road damage near the market entrance, auto-rickshaws keep getting stuck."),
    ("pothole", "Bike rider fell here yesterday", "A bike rider fell here yesterday because of this pothole. Please fix urgently."),
    ("pothole", "Uneven road surface", "Look at the state of this road."),
    ("pothole", "Potholes on the main road", "Several potholes on the main road, traffic slows down here every evening."),
    ("overflowing_trash", "Garbage bin overflowing for 3 days", "The community bin has not been cleared for three days, waste is spilling onto the road."),
    ("overflowing_trash", "Waste dumped next to the bin", "People are dumping waste next to the overflowing bin, stray dogs are spreading it."),
    ("overflowing_trash", "Bad smell from the dustbin", "Terrible stench from this bin, mosquitoes everywhere."),
    ("overflowing_trash", "Trash not collected", "Trash not collected this week in our lane."),
    ("overflowing_trash", "Overflowing dustbin near hospital", "Overflowing dustbin just outside the hospital gate, not hygienic at all."),
    ("overflowing_trash", "Please clear this", "Please send someone to clean this."),
    ("overflowing_trash", "Garbage pile at the corner", "A pile of garbage at the corner, it has been there since Diwali."),
    ("fallen_tree", "Tree fell on the road", "A big tree fell on the road after the storm, one side of the road is blocked."),
    ("fallen_tree", "Uprooted tree blocking footpath", "An uprooted tree is blocking the footpath, pedestrians are walking on the road."),
    ("fallen_tree", "Tree branches across the lane", "Branches have come down across our lane, cars cannot pass."),
    ("fallen_tree", "Fallen tree near the park", "Please see the photo, near the park entrance."),
    ("fallen_tree", "Tree has fallen on parked bikes", "The tree has fallen on parked bikes and is leaning on the boundary wall."),
    (None, "Streetlight not working", "The streetlight on our street has not been working for a week, the whole stretch is dark at night."),
    (None, "Street lights off on the main road", "All the street lights between the two squares are off, it is unsafe for women walking home."),
    (None, "Lamp post flickering", "The lamp post outside our apartment keeps flickering and then goes off."),
    (None, "Sparking from transformer", "Sparks coming from the transformer near the market. Please send someone immediately."),
    (None, "Loose electric wire hanging low", "An electric wire is hanging very low over the road, trucks could pull it down. Dangerous."),
    (None, "Power cable lying on footpath", "A power cable is lying on the footpath after the road digging work."),
    (None, "Water pipeline leaking", "The water pipeline is leaking at the junction, clean water has been flowing onto the road since morning."),
    (None, "Waterlogging after rain", "Waterlogging on the whole road after one hour of rain, the drain is blocked."),
    (None, "Sewage overflowing on road", "Sewage is overflowing from the manhole onto the road, the smell is unbearable."),
    (None, "Drain blocked with plastic", "The drain is completely blocked with plastic, water is entering shops."),
    (None, "No water supply leak in main line", "Water leak in the main line, our area has had low pressure for two days."),
    (None, "Accident at the square", "Accident at the square between a car and a scooter, one person injured. Traffic is jammed."),
    (None, "Signal not working causes near collision", "Traffic signal is off and there was nearly a collision this morning."),
    (None, "Smoke from garbage burning", "Someone is burning garbage near the colony, thick smoke is entering houses."),
    (None, "Fire in electrical shop", "Small fire seen in an electrical shop on the ground floor, smoke coming out."),
    (None, "Stray cattle on the road", "Stray cattle sitting in the middle of the road every evening."),
    (None, "Illegal parking blocking lane", "Cars are parked on both sides so ambulances cannot get through."),
    (None, "Broken footpath tiles", "Footpath tiles are broken and loose near the bus stand."),
    (None, "Open manhole cover missing", "A manhole cover is missing on the footpath, someone could fall in. Hazard."),
    (None, "Noise from construction at night", "Construction work going on after midnight near the residential area."),
    (None, "Mosquito breeding in vacant plot", "Stagnant water in the vacant plot is breeding mosquitoes."),
    (None, "Pothole near the flyover", "Pothole near the flyover exit, bikes are skidding."),
    (None, "Garbage dumped on the riverbank", "Garbage dumped along the riverbank, please arrange cleaning."),
]

PUBLIC_RESPONSES = {
    "In Progress": "Thank you for reporting. A field team has been assigned and will visit the site.",
    "Resolved": {
        "Public Works Department": "The road has been repaired. Thank you for helping keep the city safe.",
        "Sanitation Department": "The area has been cleaned and the bin cleared. Collection frequency has been increased.",
        "Electrical Department": "The fault has been fixed and the supply/lighting restored.",
        "Water Department": "The leak/blockage has been repaired by our maintenance team.",
        "Traffic Police": "Traffic has been cleared and the situation handled on site.",
        "Fire Services": "The fire crew attended and the situation is under control.",
        "Municipal Control Room": "The issue has been handled by the concerned ward office.",
    },
    "Rejected": "This location is private property outside the corporation's jurisdiction; please contact the owner.",
}


def backup_sqlite() -> Path | None:
    if engine.url.get_backend_name() != "sqlite" or not engine.url.database:
        print("Not SQLite: take a database backup yourself (pg_dump) before seeding.")
        return None
    source = Path(engine.url.database).resolve()
    target = BACKEND / "backups" / f"{source.stem}-{datetime.now():%Y%m%d-%H%M%S}.db"
    target.parent.mkdir(exist_ok=True)
    engine.dispose()
    shutil.copy2(source, target)
    return target


def remove_demo(db) -> int:
    demo_users = db.scalars(select(User).where(User.email.like(f"%{DOMAIN}"))).all()
    ids = [u.user_id for u in demo_users]
    if not ids:
        return 0
    complaints = db.scalars(select(Complaint).where(Complaint.user_id.in_(ids))).all()
    for c in complaints:
        remove_upload(c.image_filename)
    db.execute(delete(ComplaintConfirmation).where(ComplaintConfirmation.user_id.in_(ids)))
    db.execute(delete(ComplaintUpdate).where(ComplaintUpdate.complaint_id.in_([c.complaint_id for c in complaints])))
    db.execute(delete(Complaint).where(Complaint.user_id.in_(ids)))
    db.execute(delete(User).where(User.user_id.in_(ids)))
    db.commit()
    return len(complaints)


def pick_photos(per_class: int) -> dict[str, list[Path]]:
    """Test-split photos the civic model recognises confidently, best first."""
    chosen: dict[str, list[tuple[float, Path]]] = {c: [] for c in CLASSES}
    for img in sorted(TEST_IMAGES.iterdir()):
        if img.name.startswith(SKIP_SOURCES):
            continue
        lines = [ln.split()[0] for ln in (TEST_LABELS / f"{img.stem}.txt").read_text().splitlines() if ln.strip()]
        if not lines:
            continue
        cls = CLASSES[int(Counter(lines).most_common(1)[0][0])]
        if len(chosen[cls]) >= per_class * 4:
            continue
        found = detect_image(str(img))
        if found["label"] == cls and found["confidence"] >= 0.55:
            chosen[cls].append((float(found["confidence"]), img))
    return {c: [p for _, p in sorted(v, reverse=True)[:per_class]] for c, v in chosen.items()}


def store_photo(path: Path) -> str:
    name = f"{uuid4().hex}{path.suffix.lower()}"
    UPLOAD_DIRECTORY.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, UPLOAD_DIRECTORY / name)
    return name


def make_user(db, name: str, role: str, password_hash: str, department_id=None, status="active", created=None) -> User:
    email = name.lower().replace(" ", ".") + DOMAIN
    user = User(full_name=name, email=email, password_hash=password_hash, role=role, department_id=department_id,
                account_status=status, created_at=created)
    db.add(user)
    db.flush()
    return user


def seed(db, password: str) -> dict:
    rng = random.Random(SEED)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    departments = {d.department_name: d for d in db.scalars(select(Department))}
    admin = db.scalar(select(User).where(User.role == "admin").order_by(User.user_id))
    pw = hash_password(password)

    citizens = [make_user(db, n, "citizen", pw, created=now - timedelta(days=rng.randint(32, 60))) for n in CITIZENS]
    staff = {}
    for name, dept, status in STAFF:
        created = now - timedelta(days=rng.randint(35, 50)) if status == "active" else now - timedelta(hours=rng.randint(3, 20))
        user = make_user(db, name, "department", pw, departments[dept].department_id, status, created)
        if status == "active":
            staff[dept] = user
            if admin:
                user.reviewed_by_user_id, user.reviewed_at = admin.user_id, created + timedelta(hours=5)

    photos = pick_photos(per_class=10) if TEST_IMAGES.is_dir() else {c: [] for c in CLASSES}
    photo_iter = {c: iter(v) for c, v in photos.items()}
    places = list(LOCALITIES.items())

    # Outcome plan: older reports are mostly resolved; the last day's are still pending.
    plan = (["Resolved"] * 25 + ["In Progress"] * 9 + ["Pending"] * 8 + ["Rejected"] * 2 + ["Escalated"] * 1)
    reports = list(REPORTS)
    rng.shuffle(reports)
    rng.shuffle(plan)
    created_rows = []
    for i, (cls, title, description) in enumerate(reports):
        outcome = plan[i % len(plan)]
        if any(word in description.lower() for word in ("fire", "accident", "smoke")):
            outcome = "Resolved"  # emergencies are handled the same day, never left open for days
        place, (lat, lng) = places[i % len(places)]
        lat += rng.uniform(-0.004, 0.004)
        lng += rng.uniform(-0.004, 0.004)
        if outcome == "Resolved":
            created = now - timedelta(days=rng.uniform(3, 29))
        elif outcome == "In Progress":
            created = now - timedelta(hours=rng.uniform(6, 60))
        elif outcome == "Pending":
            created = now - timedelta(hours=rng.uniform(0.5, 20))
        elif outcome == "Escalated":  # open, waited too long: shows the escalation feature
            created = now - timedelta(hours=rng.uniform(80, 100))
        else:
            created = now - timedelta(days=rng.uniform(4, 15))

        photo = next(photo_iter[cls], None) if cls else None
        image_name = store_photo(photo) if photo else None
        analysis = analyze_incident(description, str(UPLOAD_DIRECTORY / image_name) if image_name else None)
        text = f"{title}. {description}"
        severity = score_severity(
            category=str(analysis["incident_type"]), text=text, detection_label=analysis["detection_label"],
            detection_confidence=analysis["detection_confidence"], latitude=lat, longitude=lng,
        )
        dept = departments.get(str(analysis["department"]))
        citizen = citizens[i % len(citizens)]
        complaint = Complaint(
            user_id=citizen.user_id, department_id=dept.department_id if dept else None,
            title=f"{title} ({place})", description=description, complaint_type=str(analysis["incident_type"]),
            priority=_priority(str(analysis["priority"]), severity.score, text), status="Pending",
            image_filename=image_name, detection_label=analysis["detection_label"],
            detection_confidence=analysis["detection_confidence"], severity_score=severity.score,
            severity_factors=severity.factors, latitude=round(lat, 6), longitude=round(lng, 6), created_at=created,
        )
        db.add(complaint)
        db.flush()

        # General reports land in the control room; the admin forwards them to the right department.
        if dept and dept.department_name == "Municipal Control Room" and admin and outcome != "Pending":
            target = departments["Public Works Department" if "foot" in text.lower() or "manhole" in text.lower()
                                 else "Traffic Police" if "park" in text.lower() or "cattle" in text.lower()
                                 else "Sanitation Department"]
            at = created + timedelta(hours=rng.uniform(1, 4))
            db.add(ComplaintUpdate(complaint_id=complaint.complaint_id, actor_user_id=admin.user_id, kind="assignment",
                                   from_department_id=dept.department_id, to_department_id=target.department_id,
                                   internal_note="Forwarded from the control room.", created_at=at))
            complaint.department_id, dept = target.department_id, target
        actor = staff.get(dept.department_name if dept else "", admin)
        actor_id = actor.user_id if actor else None

        final = {"Escalated": "In Progress"}.get(outcome, outcome)
        if final in ("In Progress", "Resolved"):
            started = created + timedelta(hours=rng.uniform(1.5, 14 if final == "Resolved" else 5))
            db.add(ComplaintUpdate(complaint_id=complaint.complaint_id, actor_user_id=actor_id, kind="status",
                                   from_status="Pending", to_status="In Progress",
                                   public_response=PUBLIC_RESPONSES["In Progress"], created_at=min(started, now)))
        if final == "Resolved":
            done = started + timedelta(hours=rng.uniform(10, 70))
            db.add(ComplaintUpdate(complaint_id=complaint.complaint_id, actor_user_id=actor_id, kind="status",
                                   from_status="In Progress", to_status="Resolved",
                                   internal_note="Site visited, work completed and photographed.",
                                   public_response=PUBLIC_RESPONSES["Resolved"].get(dept.department_name if dept else "",
                                                                                   "The issue has been resolved."),
                                   created_at=min(done, now - timedelta(minutes=30))))
        if final == "Rejected":
            db.add(ComplaintUpdate(complaint_id=complaint.complaint_id, actor_user_id=actor_id, kind="status",
                                   from_status="Pending", to_status="Rejected", public_response=PUBLIC_RESPONSES["Rejected"],
                                   created_at=created + timedelta(hours=rng.uniform(3, 20))))
        complaint.status = final
        created_rows.append((complaint, citizen, outcome))

    # Crowd confirmations: other citizens reported the same open issue nearby.
    open_rows = [r for r in created_rows if r[0].status in ("Pending", "In Progress") and r[0].complaint_type != "general"]
    for complaint, owner, _ in open_rows[:4]:
        others = [c for c in citizens if c.user_id != owner.user_id]
        for n, other in enumerate(rng.sample(others, rng.randint(2, 4))):
            db.add(ComplaintConfirmation(
                complaint_id=complaint.complaint_id, user_id=other.user_id, title="Same issue here",
                description="I am facing the same problem at this spot.",
                latitude=float(complaint.latitude) + rng.uniform(-0.0002, 0.0002),
                longitude=float(complaint.longitude) + rng.uniform(-0.0002, 0.0002),
                distance_m=round(rng.uniform(5, 45), 1),
                created_at=min(complaint.created_at + timedelta(hours=n + 1), now),
            ))
            complaint.confirmation_count += 1
        complaint.severity_score, complaint.severity_factors = bump_for_confirmations(
            complaint.severity_score, list(complaint.severity_factors or []), complaint.confirmation_count)

    db.commit()
    rows = [r[0] for r in created_rows]
    return {
        "citizens": len(citizens), "staff_active": len(staff), "staff_pending": sum(s[2] == "pending" for s in STAFF),
        "complaints": len(rows), "with_photo": sum(bool(c.image_filename) for c in rows),
        "by_status": dict(Counter(c.status for c in rows)),
        "by_department": dict(Counter(c.department_name for c in rows)),
        "photo_detections": dict(Counter(c.detection_label for c in rows if c.image_filename)),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--replace-test-complaints", action="store_true",
                    help='delete the identical "Live Complaint" test rows too')
    ap.add_argument("--remove", action="store_true", help="only delete the demo data")
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    initialize_database()
    if not args.no_backup:
        backup = backup_sqlite()
        if backup:
            print(f"Backed up the database to {backup}")
    with SessionLocal() as db:
        removed = remove_demo(db)
        if removed:
            print(f"Removed {removed} earlier demo complaint(s).")
        if args.replace_test_complaints:
            test_rows = db.scalars(select(Complaint).where(Complaint.title == "Live Complaint",
                                                           Complaint.description == "Auto detected location")).all()
            for c in test_rows:
                db.delete(c)
            db.commit()
            print(f'Deleted {len(test_rows)} "Live Complaint" test row(s).')
        if args.remove:
            return 0
        if not TEST_IMAGES.is_dir():
            print("datasets/civic is missing, so the complaints get no photos "
                  "(build it with scripts/build_civic_dataset.py to include them).")
        summary = seed(db, os.getenv("DEMO_PASSWORD", "Demo@2026"))
    for key, value in summary.items():
        print(f"{key:>17}: {value}")
    print(f"\nDemo accounts: <firstname>.<lastname>{DOMAIN}, password {os.getenv('DEMO_PASSWORD') and '$DEMO_PASSWORD' or 'Demo@2026'}"
          f"\n  citizen e.g. subhashree.mohanty{DOMAIN} · staff e.g. prakash.mohapatra{DOMAIN} (Public Works)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
