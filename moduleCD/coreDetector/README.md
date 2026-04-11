# coreDetector

A portable detector module that can be copied into another project and run directly with a jpg path.

## What it detects
- traffic signs
- pedestrians (`person`)
- vehicles (`bicycle`, `car`, `motorcycle`, `bus`, `truck`)

## Quick start

Install dependencies first:

```bash
pip install -r coreDetector/requirements.txt
```

```bash
python3 coreDetector/core_detector.py --image /path/to/your.jpg
```

Default behavior:
- run detection
- save visualization image to `coreDetector/outputs/<image_name>_detected.jpg`
- print JSON result containing `visualized_image_path`

Optional parameters:

```bash
python3 coreDetector/core_detector.py \
  --image /path/to/your.jpg \
  --sign-model /path/to/best.pt \
  --scene-model /path/to/yolov8n.pt \
  --conf 0.25 \
  --iou 0.45 \
  --img-size 640 \
  --device cuda:0 \
  --vis-out /path/to/detected.jpg \
  --out /path/to/result.json
```

Disable visualization image output:

```bash
python3 coreDetector/core_detector.py --image /path/to/your.jpg --no-vis
```

## Python API

```python
from coreDetector import CoreDetector

detector = CoreDetector(
    sign_model_path="/path/to/best.pt",   # optional
    scene_model_path="/path/to/yolov8n.pt" # optional
)

result = detector.detect("/path/to/your.jpg")
print(result)
```

直接使用 base64 编码 jpg（不需要二次落盘）：

```python
result = detector.detect_base64(image_base64_str)
print(result)
```

## Default model path priority

If no model path is passed:

1. Sign model (traffic signs)
- `coreDetector/weights/best.pt`
- `coreDetector/weights/last.pt`
- `coreDetector/weights/tsr_best.pt`
- `coreDetector/weights/yolov8s.pt`
- fallback: `yolov8s.pt`

2. Scene model (pedestrian + vehicle)
- `coreDetector/weights/yolov8n.pt`
- fallback: `yolov8n.pt`

## Files to copy to a new project

Minimum:
- `coreDetector/core_detector.py`
- `coreDetector/__init__.py`
- `coreDetector/weights/best.pt`
- `coreDetector/weights/yolov8n.pt`

Recommended (for best traffic-sign performance):
- `coreDetector/weights/yolov8s.pt` (fallback traffic-sign model)

## Dependencies

At minimum, install:
- `ultralytics`
- `torch`
- `opencv-python-headless`
- `numpy`

These are already listed in this repo's `requirements.txt`.

## Compatibility note (PyTorch 2.6+)

If you see errors related to `weights_only` / `add_safe_globals` when loading `.pt`:
- prefer using a matching pair of versions (for example, newer `ultralytics` with your current `torch`)
- or pin `torch<=2.5.1` in your target project

`core_detector.py` already includes a compatibility fallback for this issue.
