"""Bring the database schema to the latest Alembic revision.

    python -m app.db.migrate          # from backend/: migrate + seed departments

Databases created before Alembic was introduced (tables exist, no ``alembic_version``)
are stamped at the revision matching their tables, then upgraded, so existing data is kept.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from app.db.database import engine

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]

# Columns the pre-Alembic startup code added to old PostgreSQL databases on the fly.
LEGACY_COMPLAINT_COLUMNS = {
    "image_filename": "VARCHAR(255)",
    "detection_label": "VARCHAR(100)",
    "detection_confidence": "DOUBLE PRECISION",
    "latitude": "NUMERIC(10, 8)",
    "longitude": "NUMERIC(11, 8)",
}


def _config(connection: Connection) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.attributes["connection"] = connection
    return config


def _legacy_revision(connection: Connection) -> str | None:
    """Revision an un-versioned legacy database already matches, or None for an empty one."""
    tables = set(inspect(connection).get_table_names())
    if "alembic_version" in tables or "complaints" not in tables:
        return None
    existing = {c["name"] for c in inspect(connection).get_columns("complaints")}
    for column, column_type in LEGACY_COMPLAINT_COLUMNS.items():
        if column not in existing:
            connection.execute(text(f"ALTER TABLE complaints ADD COLUMN {column} {column_type}"))
    return "0002" if "complaint_updates" in tables else "0001"


def run_migrations() -> None:
    with engine.begin() as connection:
        config = _config(connection)
        legacy = _legacy_revision(connection)
        if legacy:
            logger.info("Existing pre-Alembic database detected; stamping revision %s", legacy)
            command.stamp(config, legacy)
        command.upgrade(config, "head")


if __name__ == "__main__":
    from app.db.initialize import initialize_database

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    initialize_database(migrate=True)
    print("Database schema is at the latest revision; departments are seeded.")
