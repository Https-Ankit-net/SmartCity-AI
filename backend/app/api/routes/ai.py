from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.ai.yolo_model import detect_image
from app.schemas.ai import DetectionResponse
from app.services.file_service import save_image

router = APIRouter()


@router.post("/detect-image", response_model=DetectionResponse, status_code=status.HTTP_200_OK)
async def detect_uploaded_image(image: UploadFile = File(...)) -> DetectionResponse:
    filename, destination = await save_image(image)
    try:
        result = detect_image(str(destination))
        return DetectionResponse(
            filename=filename,
            prediction=str(result["label"]),
            confidence=float(result["confidence"]),
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The uploaded file could not be processed as an image",
        ) from exc
