from pathlib import Path

from app.ai.yolo_model import detect_image

RULES = (
    (("fire", "smoke", "burning", "flame"), "fire", "High", "Fire Services"),
    (("accident", "collision", "crash", "hit and run"), "accident", "High", "Traffic Police"),
    (("garbage", "waste", "trash", "dump"), "garbage", "Medium", "Sanitation Department"),
    (("water leak", "waterlogging", "sewage", "drain", "pipeline"), "water", "Medium", "Water Department"),
    (("electric", "power", "wire", "transformer"), "electrical", "High", "Electrical Department"),
    (("pothole", "road damage", "broken road"), "road", "Medium", "Public Works Department"),
)

IMAGE_RULES = {
    "car": ("accident", "Medium", "Traffic Police"),
    "bus": ("accident", "Medium", "Traffic Police"),
    "truck": ("accident", "Medium", "Traffic Police"),
    "motorcycle": ("accident", "Medium", "Traffic Police"),
    "fire hydrant": ("fire", "High", "Fire Services"),
}


def analyze_incident(description: str, image_path: str | None = None) -> dict[str, str | float | None]:
    text = description.lower()
    detection_label: str | None = None
    detection_confidence: float | None = None

    if image_path and Path(image_path).is_file():
        try:
            result = detect_image(image_path)
            detection_label = str(result["label"])
            detection_confidence = float(result["confidence"])
        except Exception:
            detection_label = None
            detection_confidence = None

    for keywords, incident_type, priority, department in RULES:
        if any(keyword in text for keyword in keywords):
            return {
                "incident_type": incident_type,
                "priority": priority,
                "department": department,
                "detection_label": detection_label,
                "detection_confidence": detection_confidence,
            }

    if detection_label in IMAGE_RULES:
        incident_type, priority, department = IMAGE_RULES[detection_label]
        return {
            "incident_type": incident_type,
            "priority": priority,
            "department": department,
            "detection_label": detection_label,
            "detection_confidence": detection_confidence,
        }

    return {
        "incident_type": "general",
        "priority": "Low",
        "department": "Municipal Control Room",
        "detection_label": detection_label,
        "detection_confidence": detection_confidence,
    }
