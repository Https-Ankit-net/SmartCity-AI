from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, ai, auth, complaint, notifications, user, ws
from app.db.initialize import initialize_database


@asynccontextmanager
async def lifespan(_: FastAPI):
    initialize_database()
    yield


app = FastAPI(title="SmartCity AI API", version="2.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root() -> dict[str, str]:
    return {"message": "SmartCity AI API is running", "docs": "/docs"}


app.include_router(user.router, prefix="/api", tags=["Users"])
app.include_router(complaint.router, prefix="/api", tags=["Complaints"])
app.include_router(complaint.map_router, tags=["Map"])
app.include_router(auth.router, prefix="/api", tags=["Authentication"])
app.include_router(admin.router, prefix="/api", tags=["Admin"])
app.include_router(ai.router, tags=["AI Detection"])
app.include_router(ws.router)
app.include_router(notifications.router)
