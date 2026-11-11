# Custom civic-issue detector: training and evaluation

SmartCity-AI routes a complaint to a department partly from its photo. The stock YOLOv8n model only knows the 80 everyday
COCO objects (cars, people, bottles…), so it cannot recognise a pothole or an overflowing bin. We fine-tuned YOLOv8n to
detect four civic issues; the backend maps each to a department (`backend/app/ai/incident_classifier.py`):

| Class | Department |
|---|---|
| `pothole` | Public Works Department |
| `overflowing_trash` | Sanitation Department |
| `streetlight_failure` | Electrical Department |
| `fallen_tree` | Public Works Department |

## 1. Dataset

5,233 images assembled from 10 public Roboflow Universe datasets (CC BY 4.0), with the source labels renamed to our
four classes; full credits, licences and per-source counts are in [dataset_sources.md](dataset_sources.md).
Built reproducibly by `scripts/build_civic_dataset.py`.

| Split | Images | of which background | pothole | overflowing_trash | streetlight_failure | fallen_tree |
|---|---|---|---|---|---|---|
| train | 4,388 | 658 | 3,418 | 2,516 | 81 | 795 |
| val | 481 | 72 | 441 | 135 | 28 | 64 |
| test | 364 | 54 | 408 | 98 | 34 | 55 |

(Columns per class are bounding boxes.)

**Leak-safe splits.** Several sources are frames from videos or CCTV cameras, where consecutive images are nearly
identical. A random split would put near-copies of training images into the test set and inflate the scores. Instead,
images are grouped (by video, camera and 10-minute window, or photo series) and a group never crosses splits; sources
that ship their own train/val/test keep it. For streetlights, the test images come from a **camera never seen in
training**.

**Background images** (no issue present, ~15 % of each split) teach the model what *not* to flag.

## 2. Training

| Setting | Value |
|---|---|
| Base model | YOLOv8n (3.0 M parameters, 6 MB), COCO-pretrained |
| Image size / batch | 640 px / 16 |
| Epochs | 100 max, early stopping (patience 20) → stopped after 89, best epoch 69 |
| Hardware / time | NVIDIA RTX 4050 Laptop GPU (6 GB), ~60 minutes |
| Augmentation | flips, scale, translation, HSV jitter, mosaic, light mixup |
| Software | PyTorch 2.3.0 (CUDA 12.1), Ultralytics 8.2.0 |

Training curves: `figures/model/training_curves.png`.

## 3. Detection accuracy (held-out test split)

| Class | Boxes | Precision | Recall | mAP50 | mAP50-95 |
|---|---|---|---|---|---|
| pothole | 408 | 0.807 | 0.627 | 0.719 | 0.383 |
| overflowing_trash | 98 | 0.679 | 0.673 | 0.684 | 0.414 |
| streetlight_failure | 34 | – | 0.000 | 0.000 | 0.000 |
| fallen_tree | 55 | 0.693 | 0.600 | 0.588 | 0.260 |
| **all** | **595** | **0.795** | **0.475** | **0.498** | **0.264** |

Figures: `figures/model/test_confusion_matrix.png`, `figures/model/test_pr_curve.png`,
`figures/model/test_predictions_sample.jpg`.

**Reading the confusion matrix:** the model never mistakes one issue for another; its errors are *missed* issues
(28 % of potholes, 27 % of overflowing bins, 35 % of fallen trees go undetected) and *false alarms* on images without
an issue, most of which are labelled `pothole` (64 % of false detections).

## 4. End-to-end: does a photo reach the right department?

`scripts/evaluate_routing.py` runs the backend's real classifier (`analyze_incident`) on every test image with an
**empty description**, so only the photo decides, and checks the department it picks. Images without an issue should
go to the general Municipal Control Room.

| Photo shows | Stock YOLOv8n | **Fine-tuned model** |
|---|---|---|
| pothole (153 images) | 0.0 % | **96.7 %** |
| overflowing_trash (71) | 0.0 % | **87.3 %** |
| streetlight_failure (34) | 0.0 % | 0.0 % |
| fallen_tree (52) | 0.0 % | **90.4 %** |
| no issue / background (54) | 77.8 % | **88.9 %** |
| **all test images (364)** | **11.5 %** | **83.8 %** |
| issues only, macro average | 0.0 % | 68.6 % |

The stock model routed none of the civic issues correctly (it labelled a pothole as a "baseball glove" and an
overflowing bin as a "person"); the fine-tuned model routes 84 % of all test photos correctly. In the app a complaint's
text is also analysed, so routing is better still whenever the citizen writes a description.

## 5. Limitations and future work

- **Streetlight failures are not detected yet.** Public data is scarce: only 81 training boxes from a single camera,
  and the model does not transfer to the unseen test camera. Collecting and labelling 50–100 local night-time photos
  of working and failed streetlights is the planned fix. Until then these complaints are routed from their text.
- **Fallen trees** have the fewest training images (~800 boxes); more varied urban examples would raise recall.
- **Recall over precision:** at the default confidence threshold the model is cautious (precision 0.80, recall
  0.48); lowering the threshold would catch more issues at the cost of more false alarms.
- A larger variant (YOLOv8s, 22 MB) usually gains several mAP points and still trains in a night on this GPU.

## 6. Reproduce

```bash
# dataset (needs ROBOFLOW_API_KEY in the git-ignored .env)
.venv-train/Scripts/python.exe scripts/build_civic_dataset.py
# train (GPU), exports to backend/app/ai/weights/civic-yolov8.pt (git-ignored)
.venv-train/Scripts/python.exe scripts/train_yolo.py --model backend/yolov8n.pt --device 0 --workers 4
# evaluate on the held-out test split
.venv-train/Scripts/python.exe scripts/train_yolo.py --val-only --split test --model backend/app/ai/weights/civic-yolov8.pt --device 0
.venv-train/Scripts/python.exe scripts/evaluate_routing.py --weights backend/yolov8n.pt backend/app/ai/weights/civic-yolov8.pt
```

The backend loads `backend/app/ai/weights/civic-yolov8.pt` automatically when it exists (override with `YOLO_WEIGHTS`);
otherwise it falls back to the stock `yolov8n.pt`.
