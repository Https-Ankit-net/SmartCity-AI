from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.routes import admin, ai, auth, complaint, department, notifications, staff, user, ws
from app.core.config import settings
from app.core.event_bus import event_bus
from app.db.database import engine
from app.db.initialize import initialize_database
from app.services.file_service import UPLOAD_DIRECTORY


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    await event_bus.start(settings.redis_url)
    yield
    await event_bus.stop()


app = FastAPI(title="SmartCity AI API", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # Development pages are opened from disk or ad-hoc dev servers; production pins CORS_ORIGINS.
    allow_origins=settings.cors_origin_list if settings.environment.lower() == "production" else ["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "SmartCity AI API is running", "docs": "/docs"}


@app.get("/health", tags=["Health"])
def health() -> dict[str, str | bool]:
    """Liveness/readiness probe used by Docker and the reverse proxy."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok", "event_bus": "redis" if event_bus.distributed else "in-process"}


app.include_router(user.router, prefix="/api", tags=["Users"])
app.include_router(complaint.router, prefix="/api", tags=["Complaints"])
app.include_router(complaint.map_router, tags=["Map"])
app.include_router(auth.router, prefix="/api", tags=["Authentication"])
app.include_router(admin.router, prefix="/api", tags=["Admin"])
app.include_router(staff.router, prefix="/api", tags=["Staff accounts"])
app.include_router(department.router, prefix="/api", tags=["Departments"])
app.include_router(ai.router, tags=["AI Detection"])
app.include_router(ws.router)
app.include_router(notifications.router)

# Serve complaint photos so the frontend can show them.
UPLOAD_DIRECTORY.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIRECTORY), name="uploads")
