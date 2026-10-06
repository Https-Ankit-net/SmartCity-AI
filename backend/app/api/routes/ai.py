from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.ai.yolo_model import detect_image
from app.core.dependencies import get_current_user
from app.models.user import User
from app.schemas.ai import DetectionResponse, TextAnalysisRequest, TextAnalysisResponse
from app.core.rate_limit import limiter
from app.services.file_service import remove_upload, save_image
from app.services.text_analysis import analyze_text

router = APIRouter()


@router.post("/detect-image", response_model=DetectionResponse, status_code=status.HTTP_200_OK)
async def detect_uploaded_image(
    image: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> DetectionResponse:
    """Run YOLO on a photo and return the top detection. The photo is not kept."""
    limiter.check(f"detect:user:{current_user.user_id}", limit=20, window_s=60)
    original_name = image.filename or "upload"
    filename, destination = await save_image(image)
    try:
        result = await run_in_threadpool(detect_image, str(destination))
        return DetectionResponse(
            filename=original_name,
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
    finally:
        remove_upload(filename)


@router.post("/api/ai/analyze-text", response_model=TextAnalysisResponse)
async def analyze_complaint_text(
    data: TextAnalysisRequest,
    current_user: User = Depends(get_current_user),
) -> TextAnalysisResponse:
    """Extract entities, key issues, intent and a severity estimate from complaint text or a voice transcription."""
    limiter.check(f"analyze:user:{current_user.user_id}", limit=60, window_s=60)
    return TextAnalysisResponse.model_validate(
        await run_in_threadpool(analyze_text, data.text, data.latitude, data.longitude)
    )
