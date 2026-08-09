from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

UPLOAD_DIRECTORY = Path(__file__).resolve().parents[2] / "uploads"
MAX_IMAGE_SIZE_BYTES = 10 * 1024 * 1024
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
                        detail="Image must not exceed 10 MB",
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
