"""SQLAlchemy session factory and FastAPI dependency."""

from collections.abc import Generator

from sqlalchemy.orm import Session, sessionmaker

from app.db.database import engine


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """Provide one database session per request and always close it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
