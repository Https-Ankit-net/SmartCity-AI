"""Build the civic-issue detection dataset (YOLO format) from public Roboflow Universe datasets.

    # needs ROBOFLOW_API_KEY in the environment or in <repo>/.env (git-ignored)
    .venv/Scripts/python.exe scripts/build_civic_dataset.py              # download (cached) + build
    .venv/Scripts/python.exe scripts/build_civic_dataset.py --skip-download

Output: datasets/civic/{images,labels}/{train,val,test} (git-ignored), with classes in the fixed order
of scripts/civic_dataset.yaml, plus docs/dataset_sources.md (licence + attribution table for the report).

Rules
- Each source's own train/valid/test assignment is kept. Roboflow exports often contain augmented copies
  of training images; re-splitting would leak near-duplicates of training images into val/test.
  A source without a test split gives half of its valid split to test (and vice versa).
- Only the listed source labels are kept, renamed to our class names; every other box is dropped. An image
  left with no boxes is kept as a background image (it teaches the model what NOT to flag), capped at
  BACKGROUND_SHARE of each split.
- Exact duplicate images (same bytes) across sources are kept once.
"""
import argparse
import hashlib
import io
import json
import os
import random
import re
import shutil
import urllib.request
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datasets" / "raw"
OUT = ROOT / "datasets" / "civic"
CLASSES = ["pothole", "overflowing_trash", "streetlight_failure", "fallen_tree"]  # fixed order
BACKGROUND_SHARE = 0.15
SEED = 1234

# (workspace/project, version or None for latest, {source label: our class}, why it is included)
SOURCES = [
    ("yeeun-kim-fyvoj/pothole-vhmow", None, {"pothole": "pothole"}, "street-level road potholes"),
    ("yolo-2khre/pothole-sl4sr", None, {"pothole": "pothole"}, "street-level road potholes"),
    ("yinizhixia/bin-b4qnf", None, {"overflow": "overflowing_trash"}, "CCTV views of bins; other bins/people become background"),
    ("mariswary-deepak-4ajr0/garbage-can-overflow", None, {"Trash over flow": "overflowing_trash"}, "street bins; only 'Trash over flow' (spilling) counts"),
    ("ratris-workspace/overflow-bhiop", None, {"OVERFLOW": "overflowing_trash"}, "overflowing bins"),
    ("fengjianyi/streetlight-off-and-on", None, {"off": "streetlight_failure"}, "night street scenes; lit lamps become background"),
    ("erykvtymons/fallen-trees-detection-v1", None, {"fallen_tree": "fallen_tree"}, "trees fallen across roads"),
    ("hazard-detection-kzrtt/fallen-tree-e4f2f", None, {"fallen_tree": "fallen_tree"}, "trees fallen across roads"),
    ("jeevithas-workspace-vv8nz/fallen-tree-detection-8alls", None, {"Fallen-Tree-Detection": "fallen_tree"}, "fallen trees, street level"),
    ("forestrestorativerobotics/of-a-fallen-tree", None, {"of a fallen tree": "fallen_tree"}, "fallen trunks on paths"),
]
# Considered and excluded (see docs/dataset_sources.md): ctanxy/street-light-gwqbo (stock photos; 'off'
# includes lamps in daylight, which is normal, not a failure), cattle-h5tjf/bro-tree (watermarked stock
# photos), pothole-detection-4wxdf/pothole-detection-u53qg (mostly augmented copies of 150 boxes).
EXCLUDED = [
    ("ctanxy/street-light-gwqbo", "stock photos; its 'off' label includes lamps photographed in daylight (normal, not a failure)"),
    ("cattle-h5tjf/bro-tree", "watermarked stock photos (iStock), licence of the images doubtful"),
    ("pothole-detection-4wxdf/pothole-detection-u53qg", "mostly augmented copies of only 150 real boxes"),
]


def api_key() -> str:
    key = os.environ.get("ROBOFLOW_API_KEY")
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("ROBOFLOW_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise SystemExit("Set ROBOFLOW_API_KEY (environment or <repo>/.env)")
    return key


def get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.load(r)


def download(slug: str, version: int | None, key: str) -> tuple[Path, dict]:
    meta = get_json(f"https://api.roboflow.com/{slug}?api_key={key}")
    versions = meta.get("versions", [])
    if not versions:
        raise RuntimeError(f"{slug} has no exportable versions")
    v = int(str(version or max(versions, key=lambda x: x.get("created", 0))["id"]).split("/")[-1])
    dest = RAW / f"{slug.replace('/', '__')}__v{v}"
    info = {"slug": slug, "version": v, "license": meta["project"].get("license", "?"),
            "name": meta["project"].get("name", slug), "url": f"https://universe.roboflow.com/{slug}/dataset/{v}"}
    if (dest / "data.yaml").exists():
        return dest, info
    export = get_json(f"https://api.roboflow.com/{slug}/{v}/yolov8?api_key={key}")
    link = export.get("export", {}).get("link")
    if not link:
        raise RuntimeError(f"{slug} v{v}: no export link ({str(export)[:200]})")
    print(f"  downloading {slug} v{v} ...", flush=True)
    data = urllib.request.urlopen(link, timeout=600).read()
    dest.mkdir(parents=True, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(data)).extractall(dest)
    return dest, info


def norm(name: str) -> str:
    """Class names differ between a project's page and its export ('Trash flow' vs 'Trash over flow',
    'of a fallen tree' vs 'of-a-fallen-tree'): compare case- and separator-insensitively."""
    return "".join(ch for ch in name.lower() if ch.isalnum())


def to_box(parts: list[str]) -> tuple[int, float, float, float, float] | None:
    """A YOLO line is either a box (cls cx cy w h) or a polygon (cls x1 y1 x2 y2 ...); polygons become
    their bounding box."""
    if len(parts) < 5:
        return None
    cls, vals = int(parts[0]), [float(v) for v in parts[1:]]
    if len(vals) == 4:
        cx, cy, w, h = vals
    elif len(vals) % 2 == 0 and len(vals) >= 6:
        xs, ys = vals[0::2], vals[1::2]
        x0, x1, y0, y1 = max(0, min(xs)), min(1, max(xs)), max(0, min(ys)), min(1, max(ys))
        cx, cy, w, h = (x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0
    else:
        return None
    return (cls, cx, cy, w, h) if w > 0.002 and h > 0.002 else None


def split_dirs(src: Path) -> dict[str, Path]:
    found = {}
    for name, ours in (("train", "train"), ("valid", "val"), ("val", "val"), ("test", "test")):
        if (src / name / "images").exists():
            found[ours] = src / name
    return found


def group_of(slug: str, filename: str) -> str:
    """Images that are near-copies of each other (frames of one video, one camera in one time window, one
    photo series) share a group, and a group never straddles two splits, so the test set measures the
    model on scenes it has not seen."""
    base = re.sub(r"\.rf\.[0-9a-f]+\.\w+$", "", filename)
    base = re.sub(r"_(jpg|jpeg|png)$", "", base, flags=re.I)
    if slug.startswith("fengjianyi/"):                       # '3C183' = camera 3C, frame 183
        m = re.match(r"(\d+C)(\d+)", base)
        if m:
            return f"{m[1]}-{int(m[2]) // 50}"
    if slug.startswith("yinizhixia/"):                       # '0_finish_<ms timestamp>_<n>' = camera 0
        m = re.match(r"(\d+)_finish_(\d+)", base)
        if m:
            return f"cam{m[1]}-{int(m[2]) // 600_000}"        # 10-minute windows
    if slug.startswith("ratris-workspace/"):                 # 'v_157' = frame 157 of one video
        m = re.search(r"(\d+)$", base)
        if m:
            return f"v-{int(m[1]) // 25}"
    return re.sub(r"[-_ ]?\d+$", "", base) or base


# Sources whose listed groups must stay in train; all their other groups go to val/test.
# fengjianyi: only cameras 3C and 5C ever show an unlit lamp, so camera 5C is held out entirely.
FORCE_TRAIN = {"fengjianyi/streetlight-off-and-on": ("3C", "4C")}


def carve_by_group(slug: str, imgs: list[Path], rng: random.Random) -> dict[str, list[Path]]:
    groups: dict[str, list[Path]] = defaultdict(list)
    for img in imgs:
        groups[group_of(slug, img.name)].append(img)
    keys = sorted(groups)
    rng.shuffle(keys)
    out = {"train": [], "val": [], "test": []}
    forced = FORCE_TRAIN.get(slug)
    if forced:
        held = [k for k in keys if not k.startswith(forced)]
        for k in keys:
            if k.startswith(forced):
                out["train"] += groups[k]
        for i, k in enumerate(sorted(held)):                 # alternate held-out blocks between val and test
            out["val" if i % 2 == 0 else "test"] += groups[k]
        return out
    target = max(1, len(imgs) // 10)
    for k in keys:
        g = groups[k]
        if len(out["test"]) + len(g) <= target * 1.3 and len(out["test"]) < target:
            out["test"] += g
        elif len(out["val"]) + len(g) <= target * 1.3 and len(out["val"]) < target:
            out["val"] += g
        else:
            out["train"] += g
    return out


def build(sources: list[tuple[Path, dict, dict]]) -> dict:
    rng = random.Random(SEED)
    if OUT.exists():
        shutil.rmtree(OUT)
    for split in ("train", "val", "test"):
        (OUT / "images" / split).mkdir(parents=True)
        (OUT / "labels" / split).mkdir(parents=True)
    seen_hashes: set[str] = set()
    stats = defaultdict(lambda: Counter())
    backgrounds = defaultdict(list)
    per_source = {}
    for src, info, mapping in sources:
        names = yaml.safe_load((src / "data.yaml").read_text(encoding="utf-8"))["names"]
        names = names if isinstance(names, list) else [names[k] for k in sorted(names)]
        wanted = {norm(k): v for k, v in mapping.items()}
        remap = {i: CLASSES.index(wanted[norm(n)]) for i, n in enumerate(names) if norm(n) in wanted}
        if not remap:
            raise RuntimeError(f"{info['slug']}: none of {list(mapping)} found in export names {names}")
        dirs = split_dirs(src)
        items = {s: sorted((d / "images").iterdir()) for s, d in dirs.items()}
        if "test" not in items and "val" in items:                         # give half of val to test
            rng.shuffle(items["val"]); half = len(items["val"]) // 2
            items["test"], items["val"] = items["val"][:half], items["val"][half:]
        if "val" not in items and "test" in items:
            rng.shuffle(items["test"]); half = len(items["test"]) // 2
            items["val"], items["test"] = items["test"][:half], items["test"][half:]
        if "val" not in items and "test" not in items:                     # only a train split: carve ~10/10
            items = carve_by_group(info["slug"], items["train"], rng)
        src_counts = Counter()
        for split, imgs in items.items():
            label_dir = (dirs.get(split) or dirs.get("val") or dirs.get("test") or dirs["train"]) / "labels"
            for img in imgs:
                if img.suffix.lower() not in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
                    continue
                raw = img.read_bytes()
                h = hashlib.sha1(raw).hexdigest()
                if h in seen_hashes:
                    stats[split]["duplicates_skipped"] += 1
                    continue
                seen_hashes.add(h)
                lbl = None
                for d in {dirs[s] / "labels" for s in dirs}:              # label may sit in the original split dir
                    if (d / f"{img.stem}.txt").exists():
                        lbl = d / f"{img.stem}.txt"; break
                lines = []
                if lbl:
                    for ln in lbl.read_text(encoding="utf-8").splitlines():
                        box = to_box(ln.split())
                        if box and box[0] in remap:
                            lines.append(" ".join([str(remap[box[0]])] + [f"{v:.6f}" for v in box[1:]]))
                stem = f"{info['slug'].split('/')[1][:24]}__{h[:12]}"
                if not lines:
                    backgrounds[split].append((stem, img.suffix.lower(), raw))
                    continue
                (OUT / "images" / split / f"{stem}{img.suffix.lower()}").write_bytes(raw)
                (OUT / "labels" / split / f"{stem}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
                stats[split]["images"] += 1
                for ln in lines:
                    stats[split][CLASSES[int(ln.split()[0])]] += 1
                    src_counts[CLASSES[int(ln.split()[0])]] += 1
        per_source[info["slug"]] = {**info, "mapping": mapping, "boxes": dict(src_counts)}
    for split, bgs in backgrounds.items():                                  # cap background images
        rng.shuffle(bgs)
        cap = int(stats[split]["images"] * BACKGROUND_SHARE / (1 - BACKGROUND_SHARE))
        for stem, ext, raw in bgs[:cap]:
            (OUT / "images" / split / f"{stem}{ext}").write_bytes(raw)
            (OUT / "labels" / split / f"{stem}.txt").write_text("", encoding="utf-8")
            stats[split]["background_images"] += 1
    return {"splits": {s: dict(c) for s, c in stats.items()}, "sources": per_source}


def write_docs(summary: dict) -> None:
    lines = ["# Civic detection dataset: sources and licences", "",
             "Built by `scripts/build_civic_dataset.py` from public Roboflow Universe datasets. "
             "Each source keeps its own train/val/test split; only the labels below are kept, renamed to our classes.", "",
             "| Source | Version | Licence | Labels used → our class | Boxes used |", "|---|---|---|---|---|"]
    for s in summary["sources"].values():
        m = ", ".join(f"`{a}` → `{b}`" for a, b in s["mapping"].items())
        b = ", ".join(f"{k}: {v}" for k, v in s["boxes"].items()) or "0"
        lines.append(f"| [{s['slug']}]({s['url']}) | {s['version']} | {s['license']} | {m} | {b} |")
    lines += ["", "## Excluded after inspection", ""] + [f"- `{slug}`: {why}" for slug, why in EXCLUDED]
    lines += ["", "## Resulting dataset", "", "| Split | Images | Background | " + " | ".join(CLASSES) + " |",
              "|---|---|---|" + "---|" * len(CLASSES)]
    for split in ("train", "val", "test"):
        c = summary["splits"].get(split, {})
        lines.append(f"| {split} | {c.get('images', 0)} | {c.get('background_images', 0)} | "
                     + " | ".join(str(c.get(k, 0)) for k in CLASSES) + " |")
    lines += ["", "Attribution: all sources above are published on Roboflow Universe under the licence shown "
              "(CC BY 4.0 requires crediting the authors, which this table does)."]
    (ROOT / "docs" / "dataset_sources.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-download", action="store_true")
    a = ap.parse_args()
    key = None if a.skip_download else api_key()
    sources = []
    for slug, version, mapping, _why in SOURCES:
        if a.skip_download:
            cands = sorted(RAW.glob(f"{slug.replace('/', '__')}__v*"))
            if not cands:
                raise SystemExit(f"{slug} not downloaded yet")
            src = cands[-1]; info = json.loads((src / "_source.json").read_text())
        else:
            src, info = download(slug, version, key)
            (src / "_source.json").write_text(json.dumps(info), encoding="utf-8")
        sources.append((src, info, mapping))
    summary = build(sources)
    (OUT / "build_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_docs(summary)
    for split, c in summary["splits"].items():
        print(f"{split:5} {c}")
    print(f"wrote {OUT} and docs/dataset_sources.md")


if __name__ == "__main__":
    main()
