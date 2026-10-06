"""Schema migrations (Alembic) and default department seeding, run at startup."""

import os

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.migrate import run_migrations
from app.db.session import SessionLocal
from app.models.department import Department

DEFAULT_DEPARTMENTS = (
    "Fire Services",
    "Traffic Police",
    "Sanitation Department",
    "Water Department",
    "Electrical Department",
    "Public Works Department",
    "Municipal Control Room",
)


def initialize_database(migrate: bool | None = None) -> None:
    # Containers migrate once in the entrypoint and set AUTO_MIGRATE=false so that
    # several workers don't race each other; local development migrates on startup.
    if migrate is None:
        migrate = os.getenv("AUTO_MIGRATE", "true").lower() not in {"0", "false", "no"}
    if migrate:
        run_migrations()

    db: Session = SessionLocal()
    try:
        existing_names = set(db.scalars(text("SELECT department_name FROM departments")).all())
        for name in DEFAULT_DEPARTMENTS:
            if name not in existing_names:
                db.add(Department(department_name=name))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
