"""Operational commands.

    python -m app.manage create-admin --email admin@city.gov --name "City Admin"
    python -m app.manage backfill-severity        # score complaints filed before severity scoring existed
    docker compose exec backend python -m app.manage create-admin --email admin@city.gov --name "City Admin"

The password is read from the ADMIN_PASSWORD environment variable or prompted for.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from sqlalchemy import func, select

from app.core.security import hash_password
from app.db.initialize import initialize_database
from app.db.session import SessionLocal
from app.models.user import User


def create_user(full_name: str, email: str, password: str, role: str = "admin", department_id: int | None = None) -> User:
    email = email.strip().lower()
    with SessionLocal() as db:
        if db.scalar(select(User).where(func.lower(User.email) == email)) is not None:
            raise ValueError(f"A user with email {email} already exists")
        user = User(
            full_name=full_name, email=email, password_hash=hash_password(password), role=role, department_id=department_id
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user


def backfill_severity() -> int:
    """Score complaints that predate severity scoring (text, photo label already stored, location)."""
    from app.models.complaint import Complaint
    from app.services.complaint_service import PRIORITIES
    from app.services.severity_service import priority_for, score_severity

    updated = 0
    with SessionLocal() as db:
        for complaint in db.scalars(select(Complaint).where(Complaint.severity_score.is_(None))):
            result = score_severity(
                category=complaint.complaint_type,
                text=f"{complaint.title}. {complaint.description}",
                detection_label=complaint.detection_label,
                detection_confidence=complaint.detection_confidence,
                latitude=float(complaint.latitude) if complaint.latitude is not None else None,
                longitude=float(complaint.longitude) if complaint.longitude is not None else None,
            )
            complaint.severity_score, complaint.severity_factors = result.score, result.factors
            # Only ever raise priority; staff may have set it deliberately.
            current = PRIORITIES.index(complaint.priority) if complaint.priority in PRIORITIES else 0
            complaint.priority = PRIORITIES[max(current, PRIORITIES.index(priority_for(result.score)))]
            updated += 1
        db.commit()
    return updated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    admin = commands.add_parser("create-admin", help="Create an administrator account")
    admin.add_argument("--email", required=True)
    admin.add_argument("--name", required=True)
    commands.add_parser("backfill-severity", help="Score complaints that have no severity yet")
    args = parser.parse_args()

    if args.command == "backfill-severity":
        initialize_database()
        print(f"Scored {backfill_severity()} complaint(s).")
        return 0

    password = os.getenv("ADMIN_PASSWORD") or getpass.getpass("Password (min 8 characters): ")
    if len(password) < 8:
        print("Password must be at least 8 characters.", file=sys.stderr)
        return 1
    initialize_database()
    try:
        user = create_user(args.name, args.email, password)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Created admin #{user.user_id} <{user.email}>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
