"""Database engine and declarative base used by all SQLAlchemy models."""

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase


# Loading here makes `uvicorn app.main:app --reload` use the project's .env file.
load_dotenv()

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite:///./smartcity_ai.db",
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)


class Base(DeclarativeBase):
    """Base class for every ORM model."""
