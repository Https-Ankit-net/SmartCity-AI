import os
import re
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import HTTPException, UploadFile, status

load_dotenv()

BACKEND_DIR = Path(__file__).resolve().parents[2]
# UPLOAD_DIR may be absolute (a Docker volume) or relative to backend/.
UPLOAD_DIRECTORY = (BACKEND_DIR / os.getenv("UPLOAD_DIR", "uploads")).resolve()
MAX_IMAGE_SIZE_BYTES = int(os.getenv("MAX_UPLOAD_SIZE_MB", "10")) * 1024 * 1024
# Names save_image() generates: 32 hex chars + an allowed extension. Nothing else is ever opened.
STORED_NAME = re.compile(r"^[0-9a-f]{32}\.(jpg|jpeg|png|webp)$")


def upload_path(filename: str) -> Path:
    """Absolute path of a stored upload; refuses anything that isn't one of our generated names."""
    if not STORED_NAME.fullmatch(filename or ""):
        raise ValueError("Invalid upload reference")
    path = (UPLOAD_DIRECTORY / filename).resolve()
    if path.parent != UPLOAD_DIRECTORY:
        raise ValueError("Invalid upload reference")
    return path


def remove_upload(filename: str | None) -> None:
    """Delete a stored upload (e.g. after a failed or duplicate submission). Never raises."""
    if not filename:
        return
    try:
        upload_path(filename).unlink(missing_ok=True)
    except (ValueError, OSError):
        pass
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


async def save_image(image: UploadFile) -> tuple[str, Path]:
    if not image.content_type or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Upload an image file")
    suffix = Path(image.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Supported image formats: jpg, jpeg, png, webp",
        )

    UPLOAD_DIRECTORY.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid4().hex}{suffix}"
    destination = UPLOAD_DIRECTORY / filename
    written = 0
    try:
        with destination.open("wb") as output_file:
            while chunk := await image.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_IMAGE_SIZE_BYTES:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"Image must not exceed {MAX_IMAGE_SIZE_BYTES // (1024 * 1024)} MB",
                    )
                output_file.write(chunk)
        if written == 0:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Image is empty")
        return filename, destination
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await image.close()
