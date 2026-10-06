"""Fine-tune YOLOv8 on the SmartCity-AI civic issue dataset.

The detector learns four classes, in this fixed order (the backend maps the names to
complaint categories, so never reorder or rename them):

    0 pothole   1 overflowing_trash   2 streetlight_failure   3 fallen_tree

Preparing a dataset (YOLO format):

    datasets/civic/
      images/train/*.jpg   images/val/*.jpg   (images/test/ optional)
      labels/train/*.txt   labels/val/*.txt   (one .txt per image, same stem)

    Each label line is "<class_id> <x_center> <y_center> <width> <height>", the box values
    normalised to [0, 1]. An image without a label file (or with an empty one) is treated as a
    background image. Point scripts/civic_dataset.yaml at the folder (its default `path:` is
    ../datasets/civic, i.e. <repo>/datasets/civic, which is git-ignored).

Where to find data: public pothole, garbage/litter and fallen-tree detection datasets are
published on Roboflow Universe and Kaggle (search for e.g. "pothole detection", "garbage
overflow", "fallen tree", "street light"); export or convert them to YOLOv8 format, remap their
class ids to the order above, and merge them into one folder. Check each dataset's licence.
Streetlight failures are rarely covered publicly, so plan to label your own night-time photos
(Label Studio, CVAT or Roboflow can all export YOLO format).

Usage (run from the repo root with the project venv):

    # Check the dataset and print the training plan without training
    .venv/Scripts/python.exe scripts/train_yolo.py --dry-run

    # Fine-tune (GPU picked automatically when available, otherwise CPU)
    .venv/Scripts/python.exe scripts/train_yolo.py --model backend/yolov8n.pt --epochs 100
    .venv/Scripts/python.exe scripts/train_yolo.py --model yolov8s.pt --batch -1 --device 0

    # Resume an interrupted run
    .venv/Scripts/python.exe scripts/train_yolo.py --resume runs/civic/civic-yolov8n/weights/last.pt

    # Evaluate exported weights on the val split, and also export ONNX after training
    .venv/Scripts/python.exe scripts/train_yolo.py --val-only --model backend/app/ai/weights/civic-yolov8.pt
    .venv/Scripts/python.exe scripts/train_yolo.py --epochs 50 --export-onnx

After training, the best weights are copied to --export-to (default
backend/app/ai/weights/civic-yolov8.pt) with a JSON summary next to them; point the backend at
them with the YOLO_WEIGHTS environment variable.

Offline note: pass a local --model file (e.g. backend/yolov8n.pt) so no weights are downloaded.
Ultralytics also fetches Arial.ttf into its settings folder the first time it draws plots; if
that fails offline, copy Arial.ttf there by hand.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CLASSES: tuple[str, ...] = ("pothole", "overflowing_trash", "streetlight_failure", "fallen_tree")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = REPO_ROOT / "scripts" / "civic_dataset.yaml"
DEFAULT_PROJECT = REPO_ROOT / "runs" / "civic"
DEFAULT_EXPORT = REPO_ROOT / "backend" / "app" / "ai" / "weights" / "civic-yolov8.pt"
DEFAULT_MODEL = "yolov8n.pt"
DEFAULT_IMGSZ = 640
DEFAULT_BATCH = 16

# Same suffixes Ultralytics accepts as images (ultralytics.data.utils.IMG_FORMATS).
IMG_FORMATS = {"bmp", "dng", "jpeg", "jpg", "mpo", "png", "tif", "tiff", "webp", "pfm"}
MAX_LISTED = 10  # how many offending files to print before summarising

# Augmentation for street-level civic photos: left/right mirroring is realistic, upside-down
# is not; small rotations/shear for handheld phone shots; colour jitter for day/night/weather.
AUGMENTATION: dict[str, float] = {
    "fliplr": 0.5,
    "flipud": 0.0,
    "degrees": 5.0,
    "translate": 0.1,
    "scale": 0.5,
    "shear": 2.0,
    "perspective": 0.0,
    "hsv_h": 0.015,
    "hsv_s": 0.6,
    "hsv_v": 0.4,
    "mosaic": 1.0,
    "mixup": 0.05,
    "close_mosaic": 10,
}


class DatasetError(Exception):
    """The dataset or its YAML cannot be used for training."""


@dataclass
class SplitReport:
    name: str
    sources: list[Path]
    images: list[Path] = field(default_factory=list)
    missing_labels: list[Path] = field(default_factory=list)
    empty_labels: int = 0
    boxes_per_class: list[int] = field(default_factory=lambda: [0] * len(CLASSES))
    bad_files: dict[Path, list[str]] = field(default_factory=dict)


@dataclass
class Dataset:
    yaml_path: Path
    root: Path
    splits: dict[str, list[Path]]
    names: list[str]
    reports: dict[str, SplitReport] = field(default_factory=dict)


# --------------------------------------------------------------------------- dataset checks


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError as exc:
        raise DatasetError("PyYAML is missing. Install it with: pip install pyyaml") from exc
    if not path.is_file():
        raise DatasetError(f"Dataset YAML not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise DatasetError(f"Dataset YAML {path} does not parse:\n{exc}") from exc
    if not isinstance(data, dict):
        raise DatasetError(f"Dataset YAML {path} must be a mapping with path/train/val/names keys.")
    return data


def normalize_names(raw: Any) -> list[str]:
    """Accept `names` as a list or as an {index: name} map, as Ultralytics does."""
    if isinstance(raw, list):
        return [str(n) for n in raw]
    if isinstance(raw, dict):
        try:
            keys = sorted(int(k) for k in raw)
        except (TypeError, ValueError) as exc:
            raise DatasetError(f"`names` keys must be integers, got {list(raw)}") from exc
        if keys != list(range(len(keys))):
            raise DatasetError(f"`names` keys must be 0..{len(keys) - 1} without gaps, got {keys}")
        by_index = {int(k): str(v) for k, v in raw.items()}
        return [by_index[i] for i in keys]
    raise DatasetError("`names` is missing or is neither a list nor an index map.")


def check_class_names(names: list[str], source: str) -> None:
    if names != list(CLASSES):
        expected = ", ".join(f"{i}: {n}" for i, n in enumerate(CLASSES))
        found = ", ".join(f"{i}: {n}" for i, n in enumerate(names)) or "(none)"
        raise DatasetError(
            f"Class names in {source} do not match the civic classes.\n"
            f"  expected: {expected}\n"
            f"  found:    {found}\n"
            "Keep exactly these names in this order; the backend maps them to complaint categories."
        )


def resolve_dataset(yaml_path: Path) -> Dataset:
    """Resolve `path:` against the YAML's folder, then train/val/test against `path:`."""
    data = load_yaml(yaml_path)
    for key in ("train", "val"):
        if not data.get(key):
            raise DatasetError(f"Dataset YAML {yaml_path} has no '{key}:' entry.")
    names = normalize_names(data.get("names"))
    if "nc" in data and int(data["nc"]) != len(names):
        raise DatasetError(f"`nc: {data['nc']}` does not match the {len(names)} entries in `names`.")
    check_class_names(names, str(yaml_path))

    root = Path(data.get("path") or yaml_path.parent)
    if not root.is_absolute():
        root = yaml_path.parent / root
    root = root.resolve()

    splits: dict[str, list[Path]] = {}
    for key in ("train", "val", "test"):
        value = data.get(key)
        if not value:
            continue
        entries = value if isinstance(value, list) else [value]
        splits[key] = [(root / str(entry)).resolve() for entry in entries]
    return Dataset(yaml_path=yaml_path, root=root, splits=splits, names=names)


def list_images(source: Path) -> list[Path]:
    """Images under a folder (recursively) or listed in a .txt file, like Ultralytics."""
    if source.is_dir():
        return sorted(p for p in source.rglob("*.*") if p.suffix[1:].lower() in IMG_FORMATS)
    if source.is_file() and source.suffix.lower() == ".txt":
        images = []
        for line in source.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            path = Path(line)
            if not path.is_absolute():
                path = source.parent / path
            if path.suffix[1:].lower() in IMG_FORMATS:
                images.append(path.resolve())
        return images
    raise DatasetError(f"Image source does not exist (expected a folder or a .txt list): {source}")


def label_path_for(image: Path) -> Path:
    """Mirror ultralytics.data.utils.img2label_paths: last /images/ -> /labels/, suffix -> .txt."""
    text = str(image)
    sa, sb = f"{os.sep}images{os.sep}", f"{os.sep}labels{os.sep}"
    return Path(sb.join(text.rsplit(sa, 1)).rsplit(".", 1)[0] + ".txt")


def check_label_lines(path: Path, report: SplitReport) -> None:
    """Record class counts, or problems with this label file, in the report."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        report.bad_files[path] = [f"cannot read: {exc}"]
        return
    rows = [(n, line.split()) for n, line in enumerate(lines, start=1) if line.strip()]
    if not rows:
        report.empty_labels += 1
        return

    problems: list[str] = []
    counts = [0] * len(CLASSES)
    for number, parts in rows:
        if len(parts) != 5:
            problems.append(f"line {number}: expected 5 values (class x y w h), got {len(parts)}")
            continue
        try:
            values = [float(v) for v in parts]
        except ValueError:
            problems.append(f"line {number}: non-numeric value in {' '.join(parts)!r}")
            continue
        cls, coords = values[0], values[1:]
        if not cls.is_integer() or not 0 <= cls < len(CLASSES):
            problems.append(f"line {number}: class id {parts[0]} not in 0..{len(CLASSES) - 1}")
            continue
        if any(not 0.0 <= v <= 1.0 for v in coords):
            problems.append(f"line {number}: box values must be normalised to [0, 1], got {' '.join(parts[1:])}")
            continue
        if coords[2] <= 0 or coords[3] <= 0:
            problems.append(f"line {number}: box width/height must be > 0")
            continue
        counts[int(cls)] += 1

    if problems:
        report.bad_files[path] = problems
    else:
        report.boxes_per_class = [a + b for a, b in zip(report.boxes_per_class, counts)]


def scan_split(name: str, sources: list[Path]) -> SplitReport:
    report = SplitReport(name=name, sources=sources)
    for source in sources:
        if not source.exists():
            raise DatasetError(
                f"{name} image path does not exist: {source}\n"
                "Create the dataset there (layout in scripts/civic_dataset.yaml) or pass --data <your.yaml>."
            )
        report.images.extend(list_images(source))
    if not report.images:
        raise DatasetError(f"No images found for the {name} split in: {', '.join(map(str, sources))}")

    for image in report.images:
        label = label_path_for(image)
        if label.is_file():
            check_label_lines(label, report)
        else:
            report.missing_labels.append(image)
    return report


def print_split_report(report: SplitReport) -> None:
    labelled = len(report.images) - len(report.missing_labels)
    boxes = sum(report.boxes_per_class)
    print(f"  {report.name:<5} {len(report.images):>6} images  {labelled:>6} label files  {boxes:>7} boxes")
    for source in report.sources:
        print(f"        images: {source}")
    print("        boxes per class: " + ", ".join(f"{n}={c}" for n, c in zip(CLASSES, report.boxes_per_class)))
    background = len(report.missing_labels) + report.empty_labels
    if report.missing_labels:
        empty = f", plus {report.empty_labels} with an empty label file" if report.empty_labels else ""
        print(
            f"  WARNING: {len(report.missing_labels)} {report.name} image(s) have no label file and will be "
            f"used as background images{empty} ({background} background images in total)."
        )
        for image in report.missing_labels[:MAX_LISTED]:
            print(f"        no label for {image}  (expected {label_path_for(image)})")
        if len(report.missing_labels) > MAX_LISTED:
            print(f"        ... and {len(report.missing_labels) - MAX_LISTED} more")
    elif report.empty_labels:
        print(f"        {report.empty_labels} empty label file(s) = background images")
    if boxes == 0 and not report.bad_files:
        print(f"  WARNING: the {report.name} split has no boxes at all; metrics on it will be meaningless.")


def validate_dataset(yaml_path: Path) -> Dataset:
    dataset = resolve_dataset(yaml_path)
    print(f"Dataset YAML : {dataset.yaml_path}")
    print(f"Dataset root : {dataset.root}")
    print(f"Classes      : {', '.join(f'{i}={n}' for i, n in enumerate(dataset.names))}")

    for name, sources in dataset.splits.items():
        report = scan_split(name, sources)
        dataset.reports[name] = report
        print_split_report(report)

    bad = {path: issues for r in dataset.reports.values() for path, issues in r.bad_files.items()}
    if bad:
        lines = [f"{len(bad)} malformed label file(s); fix them before training:"]
        for path, issues in list(bad.items())[:MAX_LISTED]:
            lines.append(f"  {path}")
            lines.extend(f"    {issue}" for issue in issues[:3])
            if len(issues) > 3:
                lines.append(f"    ... {len(issues) - 3} more problem line(s)")
        if len(bad) > MAX_LISTED:
            lines.append(f"  ... and {len(bad) - MAX_LISTED} more file(s)")
        raise DatasetError("\n".join(lines))
    print("Dataset check: OK")
    return dataset


def resolved_yaml_path(yaml_path: Path, project: Path) -> Path:
    stem = yaml_path.stem.removesuffix(".resolved")  # resuming passes in an already-resolved copy
    return project / f"{stem}.resolved.yaml"


def write_resolved_yaml(dataset: Dataset, project: Path) -> Path:
    """Write a copy of the dataset YAML with absolute paths for Ultralytics to read.

    Ultralytics resolves a relative `path:` against its global `datasets_dir` setting, not the
    YAML's folder, so we hand it absolute paths to make both resolutions agree.
    """
    import yaml

    project.mkdir(parents=True, exist_ok=True)
    out = resolved_yaml_path(dataset.yaml_path, project)
    content: dict[str, Any] = {"path": dataset.root.as_posix()}
    for key, sources in dataset.splits.items():
        paths = [s.as_posix() for s in sources]
        content[key] = paths[0] if len(paths) == 1 else paths
    content["names"] = dict(enumerate(dataset.names))
    header = f"# Generated by scripts/train_yolo.py from {dataset.yaml_path}. Edit that file instead.\n"
    out.write_text(header + yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
    return out


# --------------------------------------------------------------------------- training helpers


def import_yolo() -> Any:
    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "Ultralytics is not installed in this Python environment.\n"
            "Install it with: pip install ultralytics  (or: pip install -r backend/requirements.txt)"
        ) from exc
    return YOLO


def pick_device(requested: str) -> str:
    if requested != "auto":
        return requested
    try:
        import torch
    except ImportError:
        return "cpu"
    return "0" if torch.cuda.is_available() else "cpu"


def uses_cuda(device: str) -> bool:
    return device.lower() not in ("cpu", "mps")


def resolve_model(value: str) -> str:
    """Use a local file when it exists; fall back to the repo's backend/ copy of a bare name."""
    path = Path(value)
    if path.is_file():
        return str(path.resolve())
    if len(path.parts) == 1:
        for folder in (REPO_ROOT / "backend", REPO_ROOT):
            candidate = folder / path.name
            if candidate.is_file():
                print(f"Using local base weights {candidate}")
                return str(candidate)
    return value  # an official name such as yolov8s.pt: Ultralytics downloads it


def resolve_resume(value: str) -> Path:
    path = Path(value)
    if path.is_dir():
        path = path / "weights" / "last.pt"
    if not path.is_file():
        raise DatasetError(f"Resume checkpoint not found: {path}")
    return path.resolve()


def read_run_args(checkpoint: Path) -> dict[str, Any]:
    """args.yaml of the run a checkpoint (<run>/weights/last.pt) belongs to, if present."""
    args_file = checkpoint.parent.parent / "args.yaml"
    if not args_file.is_file():
        return {}
    try:
        return load_yaml(args_file)
    except DatasetError:
        return {}


def train_kwargs(args: argparse.Namespace, data_yaml: Path, device: str) -> dict[str, Any]:
    cuda = uses_cuda(device)
    return {
        "data": str(data_yaml),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "device": device,
        "workers": args.workers,
        "patience": args.patience,
        "project": str(args.project),
        "name": args.name,
        "seed": args.seed,
        "deterministic": True,
        "cos_lr": True,
        "amp": cuda,
        "plots": True,
        "exist_ok": False,
        **AUGMENTATION,
    }


def print_plan(kwargs: dict[str, Any], model: str, export_to: Path, export_onnx: bool) -> None:
    print("\nTraining plan")
    print(f"  base model   : {model}")
    for key in ("data", "epochs", "imgsz", "batch", "device", "workers", "patience", "seed", "cos_lr", "amp"):
        if key in kwargs:
            print(f"  {key:<13}: {kwargs[key]}")
    if "project" in kwargs:
        print(f"  run folder   : {Path(kwargs['project']) / kwargs['name']} (auto-incremented if it exists)")
    augment = ", ".join(f"{k}={kwargs[k]}" for k in AUGMENTATION if k in kwargs)
    if augment:
        print(f"  augmentation : {augment}")
    print(f"  export to    : {export_to}" + (" (+ .onnx)" if export_onnx else ""))


def epochs_run(save_dir: Path) -> int | None:
    results = save_dir / "results.csv"
    if not results.is_file():
        return None
    with results.open(newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def metric_row(precision: float, recall: float, map50: float, map50_95: float, boxes: int) -> dict[str, float]:
    return {
        "boxes": boxes,
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "mAP50": round(float(map50), 4),
        "mAP50-95": round(float(map50_95), 4),
    }


def validate_weights(
    yolo_cls: Any,
    weights: Path,
    data_yaml: Path,
    args: argparse.Namespace,
    device: str,
    project: Path,
    name: str,
    val_report: SplitReport,
) -> dict[str, Any]:
    model = yolo_cls(str(weights))
    model_names = [model.names[i] for i in sorted(model.names)]
    if model_names != list(CLASSES):
        raise DatasetError(
            f"{weights} predicts {len(model_names)} classes ({', '.join(model_names[:6])}"
            f"{', ...' if len(model_names) > 6 else ''}), not the civic classes {', '.join(CLASSES)}.\n"
            "Validate a model fine-tuned by this script, not a base COCO checkpoint."
        )
    metrics = model.val(
        data=str(data_yaml),
        split="val",
        imgsz=args.imgsz,
        batch=args.batch if args.batch > 0 else DEFAULT_BATCH,
        device=device,
        workers=args.workers,
        project=str(project),
        name=name,
        exist_ok=True,
        plots=False,
        verbose=False,
    )
    box = metrics.box
    counts = val_report.boxes_per_class
    # Ultralytics skips the metric computation when there is no true positive at all, so a
    # class with val boxes but no entry in ap_class_index scored zero. Classes without val
    # boxes cannot be scored and stay None.
    per_class: dict[str, dict[str, float] | None] = {
        n: metric_row(0, 0, 0, 0, counts[i]) if counts[i] else None for i, n in enumerate(CLASSES)
    }
    for i, class_id in enumerate(box.ap_class_index):
        per_class[CLASSES[int(class_id)]] = metric_row(*box.class_result(i), boxes=counts[int(class_id)])
    return {
        "weights": str(weights),
        "overall": metric_row(box.mp, box.mr, box.map50, box.map, boxes=sum(counts)),
        "per_class": per_class,
        "val_dir": str(metrics.save_dir),
    }


def print_metrics(summary: dict[str, Any]) -> None:
    header = f"{'class':<20} {'boxes':>6} {'precision':>10} {'recall':>8} {'mAP50':>8} {'mAP50-95':>9}"
    print(f"\nValidation of {summary['weights']}")
    print(header)
    print("-" * len(header))
    for name in CLASSES:
        row = summary["per_class"][name]
        if row is None:
            print(f"{name:<20} {0:>6}   (no val boxes, not scored)")
        else:
            print(
                f"{name:<20} {row['boxes']:>6} {row['precision']:>10.3f} {row['recall']:>8.3f} "
                f"{row['mAP50']:>8.3f} {row['mAP50-95']:>9.3f}"
            )
    o = summary["overall"]
    print("-" * len(header))
    print(
        f"{'all':<20} {o['boxes']:>6} {o['precision']:>10.3f} {o['recall']:>8.3f} "
        f"{o['mAP50']:>8.3f} {o['mAP50-95']:>9.3f}"
    )
    print(f"Overall mAP50 = {o['mAP50']:.4f}   mAP50-95 = {o['mAP50-95']:.4f}")


def export_onnx(yolo_cls: Any, weights: Path, imgsz: int) -> Path | None:
    import importlib.util

    if importlib.util.find_spec("onnx") is None:
        # Ultralytics would otherwise try to pip-install onnx into the environment by itself.
        print("Skipping ONNX export: the 'onnx' package is not installed (pip install onnx).")
        return None
    exported = yolo_cls(str(weights)).export(format="onnx", imgsz=imgsz)
    return Path(exported) if exported else None


def ultralytics_version() -> str:
    try:
        import ultralytics

        return str(ultralytics.__version__)
    except Exception:
        return "unknown"


# --------------------------------------------------------------------------- commands


def run_val_only(args: argparse.Namespace, dataset: Dataset, device: str) -> int:
    yolo_cls = import_yolo()
    data_yaml = write_resolved_yaml(dataset, args.project)
    weights = Path(resolve_model(args.model))
    if not weights.is_file():
        raise DatasetError(f"--val-only needs an existing weights file, got: {args.model}")
    name = args.name or f"val-{weights.stem}"
    summary = validate_weights(
        yolo_cls, weights, data_yaml, args, device, args.project, name, dataset.reports["val"]
    )
    print_metrics(summary)
    print(f"Validation outputs: {summary['val_dir']}")
    return 0


def run_training(args: argparse.Namespace, dataset: Dataset, device: str, resume: Path | None) -> int:
    yolo_cls = import_yolo()
    data_yaml = write_resolved_yaml(dataset, args.project)
    if resume:
        previous = read_run_args(resume)
        base_model = str(previous.get("model", resume))
        model = yolo_cls(str(resume))
        kwargs: dict[str, Any] = {"resume": str(resume), "data": str(data_yaml), "device": device}
        if args.imgsz_given:
            kwargs["imgsz"] = args.imgsz
        if args.batch_given:
            kwargs["batch"] = args.batch
        print(f"\nResuming {resume}")
    else:
        base_model = resolve_model(args.model)
        kwargs = train_kwargs(args, data_yaml, device)
        print_plan(kwargs, base_model, args.export_to, args.export_onnx)
        model = yolo_cls(base_model)

    try:
        model.train(**kwargs)
    except KeyboardInterrupt:
        trainer = getattr(model, "trainer", None)
        last = Path(trainer.save_dir) / "weights" / "last.pt" if trainer else None
        target = f'"{last}"' if last and last.is_file() else f'"{args.project / "<run>" / "weights" / "last.pt"}"'
        extra = f' --export-to "{args.export_to}"' if args.export_to != DEFAULT_EXPORT else ""
        print("\nTraining interrupted. Progress up to the last finished epoch is saved.")
        print(f"Resume with: python scripts/train_yolo.py --resume {target}{extra}")
        return 130

    trainer = model.trainer
    save_dir = Path(trainer.save_dir)
    best = Path(trainer.best)
    if not best.is_file():
        best = Path(trainer.last)
        print(f"WARNING: best.pt missing, using {best}")
    # On resume the checkpoint's own settings win, so report what was actually used.
    args.imgsz = int(trainer.args.imgsz)
    args.batch = int(trainer.args.batch)

    summary = validate_weights(
        yolo_cls, best, data_yaml, args, device, save_dir, "val-best", dataset.reports["val"]
    )
    print_metrics(summary)

    args.export_to.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(best, args.export_to)
    print(f"\nExported best weights: {args.export_to}")
    onnx_path = export_onnx(yolo_cls, args.export_to, args.imgsz) if args.export_onnx else None
    if onnx_path:
        print(f"Exported ONNX: {onnx_path}")

    report = {
        "classes": list(CLASSES),
        "metrics": summary["overall"],
        "per_class": summary["per_class"],
        "base_model": base_model,
        "epochs_requested": int(trainer.args.epochs),
        "epochs_run": epochs_run(save_dir),
        "imgsz": args.imgsz,
        "batch": args.batch,
        "device": device,
        "dataset": str(dataset.yaml_path),
        "dataset_root": str(dataset.root),
        "train_images": len(dataset.reports["train"].images),
        "val_images": len(dataset.reports["val"].images),
        "run_dir": str(save_dir),
        "weights": str(args.export_to),
        "onnx": str(onnx_path) if onnx_path else None,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ultralytics_version": ultralytics_version(),
    }
    summary_path = args.export_to.with_suffix(".json")
    summary_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Summary: {summary_path}")
    print(f"Run folder: {save_dir}")

    weights = args.export_to.resolve()
    print("\nUse it in the backend by setting YOLO_WEIGHTS before starting it:")
    print(f'  PowerShell: $env:YOLO_WEIGHTS = "{weights}"')
    print(f'  bash      : export YOLO_WEIGHTS="{weights.as_posix()}"')
    return 0


# --------------------------------------------------------------------------- CLI


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fine-tune YOLOv8 on the civic issue dataset (" + ", ".join(CLASSES) + ").",
    )
    add = parser.add_argument
    add("--data", type=Path, default=None, help=f"dataset YAML (default: {DEFAULT_DATA})")
    add("--model", default=DEFAULT_MODEL, help="base weights, local file or official name (default: %(default)s)")
    add("--epochs", type=int, default=100, help="maximum epochs (default: %(default)s)")
    add("--imgsz", type=int, default=None, help=f"training image size (default: {DEFAULT_IMGSZ})")
    add("--batch", type=int, default=None, help=f"batch size, -1 = auto on CUDA (default: {DEFAULT_BATCH})")
    add("--device", default="auto", help="auto, cpu, 0, 0,1 ... (default: auto = GPU 0 if CUDA, else cpu)")
    add("--workers", type=int, default=min(8, os.cpu_count() or 1), help="dataloader workers (default: %(default)s)")
    add("--patience", type=int, default=20, help="early-stopping patience in epochs (default: %(default)s)")
    add("--project", type=Path, default=DEFAULT_PROJECT, help="folder for training runs (default: %(default)s)")
    add("--name", default=None, help="run name (default: civic-<model stem>)")
    add("--resume", default=None, help="resume from a last.pt (or its run folder)")
    add("--seed", type=int, default=0, help="random seed (default: %(default)s)")
    add("--export-to", type=Path, default=DEFAULT_EXPORT, help="where to copy best.pt (default: %(default)s)")
    add("--export-onnx", action="store_true", help="also export ONNX next to --export-to")
    add("--val-only", action="store_true", help="only evaluate --model on the val split")
    add("--dry-run", action="store_true", help="check the dataset and print the plan; no training")
    args = parser.parse_args(argv)

    if args.resume and args.val_only:
        parser.error("--resume and --val-only cannot be combined")
    args.data_given = args.data is not None
    args.imgsz_given = args.imgsz is not None
    args.batch_given = args.batch is not None
    args.data = (args.data or DEFAULT_DATA).resolve()
    args.imgsz = args.imgsz or DEFAULT_IMGSZ
    args.batch = DEFAULT_BATCH if args.batch is None else args.batch
    if args.batch == 0 or args.batch < -1:
        parser.error("--batch must be a positive number or -1 (auto)")
    if args.epochs < 1:
        parser.error("--epochs must be at least 1")
    args.project = args.project.resolve()
    args.export_to = args.export_to.resolve()
    args.name = args.name or (None if args.val_only else f"civic-{Path(args.model).stem}")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        resume = resolve_resume(args.resume) if args.resume else None
        if resume:
            # Keep everything next to the run being resumed: <project>/<run>/weights/last.pt.
            args.project = resume.parent.parent.parent
            previous = read_run_args(resume).get("data")
            if not args.data_given and previous and Path(previous).is_file():
                args.data = Path(previous)  # check the dataset the interrupted run was using
        dataset = validate_dataset(args.data)
        device = pick_device(args.device)

        if args.dry_run:
            if resume:
                print(f"\nDry run: would resume {resume} on device {device}.")
            else:
                kwargs = train_kwargs(args, resolved_yaml_path(args.data, args.project), device)
                print_plan(kwargs, resolve_model(args.model), args.export_to, args.export_onnx)
                print("\nDry run: dataset is valid, nothing was trained.")
            return 0
        if args.val_only:
            return run_val_only(args, dataset, device)
        return run_training(args, dataset, device, resume)
    except DatasetError as exc:
        sys.stdout.flush()
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
