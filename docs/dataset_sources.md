# Civic detection dataset: sources and licences

Built by `scripts/build_civic_dataset.py` from public Roboflow Universe datasets. Each source keeps its own train/val/test split; only the labels below are kept, renamed to our classes.

| Source | Version | Licence | Labels used → our class | Boxes used |
|---|---|---|---|---|
| [yeeun-kim-fyvoj/pothole-vhmow](https://universe.roboflow.com/yeeun-kim-fyvoj/pothole-vhmow/dataset/18) | 18 | CC BY 4.0 | `pothole` → `pothole` | pothole: 2107 |
| [yolo-2khre/pothole-sl4sr](https://universe.roboflow.com/yolo-2khre/pothole-sl4sr/dataset/1) | 1 | CC BY 4.0 | `pothole` → `pothole` | pothole: 2160 |
| [yinizhixia/bin-b4qnf](https://universe.roboflow.com/yinizhixia/bin-b4qnf/dataset/3) | 3 | CC BY 4.0 | `overflow` → `overflowing_trash` | overflowing_trash: 637 |
| [mariswary-deepak-4ajr0/garbage-can-overflow](https://universe.roboflow.com/mariswary-deepak-4ajr0/garbage-can-overflow/dataset/4) | 4 | CC BY 4.0 | `Trash over flow` → `overflowing_trash` | overflowing_trash: 1593 |
| [ratris-workspace/overflow-bhiop](https://universe.roboflow.com/ratris-workspace/overflow-bhiop/dataset/1) | 1 | CC BY 4.0 | `OVERFLOW` → `overflowing_trash` | overflowing_trash: 519 |
| [fengjianyi/streetlight-off-and-on](https://universe.roboflow.com/fengjianyi/streetlight-off-and-on/dataset/2) | 2 | CC BY 4.0 | `off` → `streetlight_failure` | streetlight_failure: 143 |
| [erykvtymons/fallen-trees-detection-v1](https://universe.roboflow.com/erykvtymons/fallen-trees-detection-v1/dataset/29) | 29 | CC BY 4.0 | `fallen_tree` → `fallen_tree` | fallen_tree: 675 |
| [hazard-detection-kzrtt/fallen-tree-e4f2f](https://universe.roboflow.com/hazard-detection-kzrtt/fallen-tree-e4f2f/dataset/3) | 3 | CC BY 4.0 | `fallen_tree` → `fallen_tree` | fallen_tree: 16 |
| [jeevithas-workspace-vv8nz/fallen-tree-detection-8alls](https://universe.roboflow.com/jeevithas-workspace-vv8nz/fallen-tree-detection-8alls/dataset/984831624) | 984831624 | CC BY 4.0 | `Fallen-Tree-Detection` → `fallen_tree` | fallen_tree: 69 |
| [forestrestorativerobotics/of-a-fallen-tree](https://universe.roboflow.com/forestrestorativerobotics/of-a-fallen-tree/dataset/3) | 3 | CC BY 4.0 | `of a fallen tree` → `fallen_tree` | fallen_tree: 154 |

## Excluded after inspection

- `ctanxy/street-light-gwqbo`: stock photos; its 'off' label includes lamps photographed in daylight (normal, not a failure)
- `cattle-h5tjf/bro-tree`: watermarked stock photos (iStock), licence of the images doubtful
- `pothole-detection-4wxdf/pothole-detection-u53qg`: mostly augmented copies of only 150 real boxes

## Resulting dataset

| Split | Images | Background | pothole | overflowing_trash | streetlight_failure | fallen_tree |
|---|---|---|---|---|---|---|
| train | 3730 | 658 | 3418 | 2516 | 81 | 795 |
| val | 409 | 72 | 441 | 135 | 28 | 64 |
| test | 310 | 54 | 408 | 98 | 34 | 55 |

Attribution: all sources above are published on Roboflow Universe under the licence shown (CC BY 4.0 requires crediting the authors, which this table does).
