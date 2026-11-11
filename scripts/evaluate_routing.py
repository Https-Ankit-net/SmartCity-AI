"""End-to-end evaluation: does a citizen's photo reach the right department?

For every image in the held-out test split, run the backend's real classifier
(app.ai.incident_classifier.analyze_incident) with an empty description, so only the photo decides,
and compare the department it picks with the department the image's labelled issue belongs to.
Images with no labelled issue (background) should go to the general Municipal Control Room.

Runs once per model, so the stock COCO model and the fine-tuned civic model can be compared:

    .venv-train/Scripts/python.exe scripts/evaluate_routing.py \
        --weights backend/yolov8n.pt backend/app/ai/weights/civic-yolov8.pt

Writes runs/eval/routing.json and prints a per-class table.
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.ai import yolo_model  # noqa: E402
from app.ai.incident_classifier import IMAGE_RULES, analyze_incident  # noqa: E402

CLASSES = ["pothole", "overflowing_trash", "streetlight_failure", "fallen_tree"]
GENERAL = "Municipal Control Room"
EXPECTED = {c: IMAGE_RULES[c][2] for c in CLASSES}  # class -> department, from the backend's own rules


def expected_for(label_file: Path) -> tuple[str, str]:
    """(issue, department) for an image; the most frequent labelled class decides, none = background."""
    lines = [ln.split()[0] for ln in label_file.read_text(encoding="utf-8").splitlines() if ln.strip()]
    if not lines:
        return "background", GENERAL
    issue = CLASSES[int(Counter(lines).most_common(1)[0][0])]
    return issue, EXPECTED[issue]


def evaluate(weights: Path, split: str) -> dict:
    yolo_model.WEIGHTS = str(weights)          # the backend reads YOLO_WEIGHTS once; swap the model here
    yolo_model._model = None
    img_dir = ROOT / "datasets" / "civic" / "images" / split
    lbl_dir = ROOT / "datasets" / "civic" / "labels" / split
    per_issue = defaultdict(Counter)
    confusion = defaultdict(Counter)
    for img in sorted(img_dir.iterdir()):
        issue, want = expected_for(lbl_dir / f"{img.stem}.txt")
        got = analyze_incident("", str(img))["department"]
        per_issue[issue]["total"] += 1
        per_issue[issue]["correct"] += int(got == want)
        confusion[issue][got] += 1
    rows = {k: {"images": v["total"], "correct": v["correct"], "accuracy": round(v["correct"] / v["total"], 4)}
            for k, v in per_issue.items()}
    total = sum(v["total"] for v in per_issue.values())
    correct = sum(v["correct"] for v in per_issue.values())
    issues = [k for k in CLASSES if k in rows]
    macro = sum(rows[k]["accuracy"] for k in issues) / len(issues) if issues else 0.0
    return {"weights": str(weights.relative_to(ROOT)) if weights.is_relative_to(ROOT) else str(weights),
            "split": split, "overall_accuracy": round(correct / total, 4), "macro_accuracy_issues": round(macro, 4),
            "images": total, "per_issue": rows, "routed_to": {k: dict(v) for k, v in confusion.items()}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", nargs="+", type=Path, required=True)
    ap.add_argument("--split", default="test", choices=("val", "test"))
    a = ap.parse_args()
    results = [evaluate(w.resolve(), a.split) for w in a.weights]
    out = ROOT / "runs" / "eval" / "routing.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"expected_departments": EXPECTED, "results": results}, indent=2), encoding="utf-8")
    order = CLASSES + ["background"]
    print(f"\nPhoto -> department routing accuracy on the {a.split} split (description left empty)")
    print(f"{'issue':<22}" + "".join(f"{Path(r['weights']).name:>26}" for r in results))
    for k in order:
        if k in results[0]["per_issue"]:
            n = results[0]["per_issue"][k]["images"]
            print(f"{k + f' ({n})':<22}" + "".join(f"{r['per_issue'][k]['accuracy']:>26.1%}" for r in results))
    print(f"{'all images':<22}" + "".join(f"{r['overall_accuracy']:>26.1%}" for r in results))
    print(f"{'issues (macro avg)':<22}" + "".join(f"{r['macro_accuracy_issues']:>26.1%}" for r in results))
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
