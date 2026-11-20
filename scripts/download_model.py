"""Download the fine-tuned civic-issue detector (YOLOv8n) from the GitHub release.

The weights are not stored in git (they are a 6 MB binary). Without them the backend falls back to the
stock COCO model, which cannot recognise potholes, garbage, streetlights or fallen trees.

    python scripts/download_model.py           # from the repository root (any Python 3, no extra packages)
    python scripts/download_model.py --force   # download again even if the file exists

The file is checked against its SHA-256 before it is put in place.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

URL = "https://github.com/Https-Ankit-net/SmartCity-AI/releases/download/model-v1/civic-yolov8.pt"
SHA256 = "17e4a8ee5b5fc2187dfe0c7f78427785d0f0bdc0008665f500ac5cd128f9cbd5"
DEST = Path(__file__).resolve().parents[1] / "backend" / "app" / "ai" / "weights" / "civic-yolov8.pt"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="download even if the weights already exist")
    args = ap.parse_args()

    if DEST.is_file() and not args.force:
        if sha256(DEST) == SHA256:
            print(f"Model already present: {DEST}")
            return 0
        print("Existing model file differs from the release; keeping it (use --force to replace).")
        return 0

    DEST.parent.mkdir(parents=True, exist_ok=True)
    tmp = DEST.with_suffix(".part")
    print(f"Downloading the civic detector (6 MB)\n  from {URL}")
    try:
        with urllib.request.urlopen(URL, timeout=60) as resp, tmp.open("wb") as out:
            while chunk := resp.read(1 << 16):
                out.write(chunk)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        print(f"Download failed: {exc}\nThe backend will use the stock COCO model until the file is in place.",
              file=sys.stderr)
        return 1

    if sha256(tmp) != SHA256:
        tmp.unlink(missing_ok=True)
        print("Checksum mismatch: the downloaded file was discarded.", file=sys.stderr)
        return 1
    tmp.replace(DEST)
    print(f"Saved {DEST}\nRestart the backend if it is running; it loads this file automatically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
