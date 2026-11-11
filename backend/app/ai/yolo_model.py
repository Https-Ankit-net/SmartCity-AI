"""Lazy YOLOv8 image detection service."""

import os
from pathlib import Path
from threading import Lock

# Which weights to load: YOLO_WEIGHTS if set; otherwise the fine-tuned civic model exported by
# scripts/train_yolo.py when it exists (it is git-ignored, so fresh clones and CI don't have it);
# otherwise the stock COCO model.
CIVIC_WEIGHTS = Path(__file__).resolve().parent / "weights" / "civic-yolov8.pt"
WEIGHTS = os.getenv("YOLO_WEIGHTS") or (str(CIVIC_WEIGHTS) if CIVIC_WEIGHTS.is_file() else "yolov8n.pt")

_model = None
_model_lock = Lock()


def _get_model():
    """Load the model once, on the first detection request."""
    global _model

    if _model is None:
        with _model_lock:
            if _model is None:
                try:
                    from ultralytics import YOLO
                except ImportError as exc:
                    raise RuntimeError(
                        "YOLO is not installed. Run: pip install -r requirements.txt"
                    ) from exc
                _model = YOLO(WEIGHTS)
    return _model


def detect_image(image_path: str) -> dict[str, str | float]:
    """Return the highest-confidence YOLO prediction for an image file."""
    if not Path(image_path).is_file():
        raise FileNotFoundError(f"Image not found: {image_path}")

    result = _get_model()(image_path, verbose=False)[0]
    if result.boxes is None or len(result.boxes) == 0:
        return {"label": "no_detection", "confidence": 0.0}

    confidences = result.boxes.conf.tolist()
    best_index = max(range(len(confidences)), key=confidences.__getitem__)
    class_id = int(result.boxes.cls[best_index].item())
    label = result.names[class_id]

    return {"label": str(label), "confidence": round(float(confidences[best_index]), 4)}
