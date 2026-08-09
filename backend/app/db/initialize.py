"""Create tables and apply the small additive schema upgrades used by this project."""

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.db.database import Base, engine
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


def initialize_database() -> None:
    Base.metadata.create_all(bind=engine)

    if engine.dialect.name == "postgresql":
        existing_columns = {column["name"] for column in inspect(engine).get_columns("complaints")}
        additions = {
            "image_filename": "VARCHAR(255)",
            "detection_label": "VARCHAR(100)",
            "detection_confidence": "DOUBLE PRECISION",
            "latitude": "NUMERIC(10, 8)",
            "longitude": "NUMERIC(11, 8)",
        }
        with engine.begin() as connection:
            for column, column_type in additions.items():
                if column not in existing_columns:
                    connection.execute(text(f"ALTER TABLE complaints ADD COLUMN {column} {column_type}"))

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
