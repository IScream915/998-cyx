from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
import json
import logging
import os
import traceback
import warnings
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Runtime cache inside module folder to avoid noisy permission warnings.
RUNTIME_CACHE_DIR = Path(__file__).resolve().parent / ".runtime_cache"
(RUNTIME_CACHE_DIR / "mpl").mkdir(parents=True, exist_ok=True)
(RUNTIME_CACHE_DIR / "ultralytics").mkdir(parents=True, exist_ok=True)
(RUNTIME_CACHE_DIR / "xdg").mkdir(parents=True, exist_ok=True)

# PyTorch 2.6+ changed torch.load default(weights_only=True), which may break
# loading older/third-party Ultralytics checkpoints.
os.environ.setdefault("TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD", "1")
# Workaround for duplicated OpenMP runtime in some conda/macOS environments.
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MPLCONFIGDIR", str((RUNTIME_CACHE_DIR / "mpl").resolve()))
os.environ.setdefault("YOLO_CONFIG_DIR", str((RUNTIME_CACHE_DIR / "ultralytics").resolve()))
os.environ.setdefault("XDG_CACHE_HOME", str((RUNTIME_CACHE_DIR / "xdg").resolve()))
warnings.filterwarnings(
    "ignore",
    message=r"Environment variable TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD detected.*",
    category=UserWarning,
)

import torch
from PIL import Image, ImageDraw, ImageFont
from ultralytics import YOLO
from ultralytics.utils import LOGGER as YOLO_LOGGER

# In some mixed conda/pip setups torch.from_numpy fails with:
# TypeError: expected np.ndarray (got numpy.ndarray)
# Fallback to torch.as_tensor keeps inference usable.
_TORCH_FROM_NUMPY_ORIG = torch.from_numpy


def _torch_from_numpy_compat(x):
    try:
        return _TORCH_FROM_NUMPY_ORIG(x)
    except TypeError as e:
        if "expected np.ndarray (got numpy.ndarray)" in str(e):
            return torch.as_tensor(x)
        raise


torch.from_numpy = _torch_from_numpy_compat

try:
    from torch.serialization import add_safe_globals
    from ultralytics.nn.tasks import (
        ClassificationModel,
        DetectionModel,
        OBBModel,
        PoseModel,
        SegmentationModel,
    )

    add_safe_globals(
        [
            DetectionModel,
            SegmentationModel,
            ClassificationModel,
            PoseModel,
            OBBModel,
        ]
    )
except Exception:
    # Keep runtime robust when torch/ultralytics internals differ by version.
    pass


def _patch_ultralytics_export_formats_for_inference() -> None:
    """
    Ultralytics may call pandas in export_formats() during model-type probing.
    In some environments (pandas/numpy ABI mismatch), that raises:
    TypeError: Cannot convert numpy.ndarray to numpy.ndarray
    For pure inference we only need the `Suffix` list, so patch a lightweight fallback.
    """
    try:
        import ultralytics.engine.exporter as exporter

        try:
            exporter.export_formats()
            return
        except Exception:
            pass

        def _safe_export_formats():
            class _Formats:
                Suffix = [
                    ".pt",
                    ".torchscript",
                    ".onnx",
                    "_openvino_model",
                    ".engine",
                    ".mlpackage",
                    "_saved_model",
                    ".pb",
                    ".tflite",
                    "_edgetpu.tflite",
                    "_web_model",
                    "_paddle_model",
                    "_ncnn_model",
                ]

            return _Formats()

        exporter.export_formats = _safe_export_formats
    except Exception:
        pass


VEHICLE_CLASS_MAP = {
    1: "bicycle",
    2: "car",
    3: "motorcycle",
    5: "bus",
    7: "truck",
}

SCENE_CLASSES = [0, 1, 2, 3, 5, 7]
VEHICLE_VIS_COLORS = {
    "bicycle": (0, 210, 210),
    "car": (0, 165, 255),
    "motorcycle": (0, 200, 255),
    "bus": (180, 50, 255),
    "truck": (60, 80, 255),
}


class CoreDetector:
    """
    Portable detector for:
    1) traffic signs
    2) pedestrians
    3) vehicles

    Input: jpg/jpeg image path
    Output: structured JSON-serializable dict
    """

    def __init__(
        self,
        sign_model_path: Optional[str] = None,
        scene_model_path: Optional[str] = None,
        conf: float = 0.25,
        iou: float = 0.45,
        img_size: int = 640,
        device: Optional[str] = None,
    ) -> None:
        self.conf = conf
        self.iou = iou
        self.img_size = img_size
        self.device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")

        # Keep CLI output JSON-only by reducing third-party log noise.
        os.environ.setdefault("YOLO_VERBOSE", "False")
        YOLO_LOGGER.setLevel(logging.ERROR)
        _patch_ultralytics_export_formats_for_inference()

        self.base_dir = Path(__file__).resolve().parent
        self.sign_model_path = self._resolve_sign_model_path(sign_model_path)
        self.scene_model_path = self._resolve_scene_model_path(scene_model_path)

        self.sign_model = YOLO(self.sign_model_path)
        self.scene_model = YOLO(self.scene_model_path)
        self.output_dir = self.base_dir / "outputs"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_sign_model_path(self, user_path: Optional[str]) -> str:
        if user_path:
            p = Path(user_path)
            if not p.exists():
                raise FileNotFoundError(f"sign model not found: {p}")
            return str(p)

        candidates = [
            self.base_dir / "weights" / "best.pt",
            self.base_dir / "weights" / "last.pt",
            self.base_dir / "weights" / "tsr_best.pt",
            self.base_dir / "weights" / "yolov8s.pt",
        ]

        for p in candidates:
            if p.exists():
                return str(p)

        local_weights = sorted((self.base_dir / "weights").glob("*.pt"))
        if local_weights:
            return str(local_weights[0])

        return "yolov8s.pt"

    def _resolve_scene_model_path(self, user_path: Optional[str]) -> str:
        if user_path:
            p = Path(user_path)
            if not p.exists():
                raise FileNotFoundError(f"scene model not found: {p}")
            return str(p)

        candidates = [
            self.base_dir / "weights" / "yolov8n.pt",
        ]
        for p in candidates:
            if p.exists():
                return str(p)

        return "yolov8n.pt"

    def _validate_image_path(self, image_path: str) -> Path:
        p = Path(image_path)
        if not p.exists():
            raise FileNotFoundError(f"image not found: {p}")

        if p.suffix.lower() not in {".jpg", ".jpeg"}:
            raise ValueError(f"only .jpg/.jpeg is supported, got: {p.suffix}")
        return p

    @staticmethod
    def _to_int_bbox(xyxy: List[float]) -> List[int]:
        return [int(round(v)) for v in xyxy]

    def _default_vis_path(self, image_path: Path) -> str:
        stem = image_path.stem
        return str((self.output_dir / f"{stem}_detected.jpg").resolve())

    @staticmethod
    def _draw_box(
        draw: ImageDraw.ImageDraw,
        font: ImageFont.ImageFont,
        bbox: List[int],
        label: str,
        color_rgb: Tuple[int, int, int],
    ) -> None:
        x1, y1, x2, y2 = bbox
        draw.rectangle([x1, y1, x2, y2], outline=color_rgb, width=3)

        text_bbox = draw.textbbox((0, 0), label, font=font)
        tw = text_bbox[2] - text_bbox[0]
        th = text_bbox[3] - text_bbox[1]
        y_text_top = max(0, y1 - th - 6)
        draw.rectangle([x1, y_text_top, x1 + tw + 6, y_text_top + th + 4], fill=color_rgb)
        draw.text((x1 + 3, y_text_top + 2), label, fill=(0, 0, 0), font=font)

    def _save_visualization(
        self,
        image_source: str,
        signs: List[Dict[str, Any]],
        pedestrians: List[Dict[str, Any]],
        vehicles: List[Dict[str, Any]],
        vis_output_path: str,
    ) -> str:
        with Image.open(image_source) as im:
            canvas = im.convert("RGB")
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.load_default()

        for d in signs:
            label = f"{d['class_name']} {d['confidence']:.2f}"
            self._draw_box(draw, font, d["bbox"], label, (255, 215, 0))

        for d in pedestrians:
            label = f"person {d['confidence']:.2f}"
            self._draw_box(draw, font, d["bbox"], label, (0, 220, 80))

        for d in vehicles:
            cls_name = d["class_name"]
            label = f"{cls_name} {d['confidence']:.2f}"
            color = VEHICLE_VIS_COLORS.get(cls_name, (255, 140, 0))
            self._draw_box(draw, font, d["bbox"], label, color)

        out_path = Path(vis_output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_path, format="JPEG", quality=92)
        return str(out_path)

    def _save_visualization_from_pil(
        self,
        image: Image.Image,
        signs: List[Dict[str, Any]],
        pedestrians: List[Dict[str, Any]],
        vehicles: List[Dict[str, Any]],
        vis_output_path: str,
    ) -> str:
        canvas = image.convert("RGB")
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.load_default()

        for d in signs:
            label = f"{d['class_name']} {d['confidence']:.2f}"
            self._draw_box(draw, font, d["bbox"], label, (255, 215, 0))

        for d in pedestrians:
            label = f"person {d['confidence']:.2f}"
            self._draw_box(draw, font, d["bbox"], label, (0, 220, 80))

        for d in vehicles:
            cls_name = d["class_name"]
            label = f"{cls_name} {d['confidence']:.2f}"
            color = VEHICLE_VIS_COLORS.get(cls_name, (255, 140, 0))
            self._draw_box(draw, font, d["bbox"], label, color)

        out_path = Path(vis_output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_path, format="JPEG", quality=92)
        return str(out_path)

    def _parse_signs(self, results: Any) -> List[Dict[str, Any]]:
        detections: List[Dict[str, Any]] = []
        for r in results:
            if r.boxes is None:
                continue
            for i in range(len(r.boxes)):
                cls_id = int(r.boxes.cls[i])
                detections.append(
                    {
                        "bbox": self._to_int_bbox(r.boxes.xyxy[i].cpu().tolist()),
                        "confidence": round(float(r.boxes.conf[i]), 4),
                        "class_id": cls_id,
                        "class_name": r.names[cls_id],
                    }
                )
        return detections

    def _parse_scene(self, results: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        pedestrians: List[Dict[str, Any]] = []
        vehicles: List[Dict[str, Any]] = []

        for r in results:
            if r.boxes is None:
                continue
            for i in range(len(r.boxes)):
                cls_id = int(r.boxes.cls[i])
                bbox = self._to_int_bbox(r.boxes.xyxy[i].cpu().tolist())
                conf_v = round(float(r.boxes.conf[i]), 4)

                if cls_id == 0:
                    pedestrians.append(
                        {
                            "bbox": bbox,
                            "confidence": conf_v,
                            "class_id": 0,
                            "class_name": "person",
                        }
                    )
                elif cls_id in VEHICLE_CLASS_MAP:
                    vehicles.append(
                        {
                            "bbox": bbox,
                            "confidence": conf_v,
                            "class_id": cls_id,
                            "class_name": VEHICLE_CLASS_MAP[cls_id],
                        }
                    )

        return pedestrians, vehicles

    def _run_detection(self, source: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        sign_results = self.sign_model.predict(
            source=source,
            conf=self.conf,
            iou=self.iou,
            imgsz=self.img_size,
            device=self.device,
            verbose=False,
        )

        scene_results = self.scene_model.predict(
            source=source,
            conf=self.conf,
            iou=self.iou,
            imgsz=self.img_size,
            classes=SCENE_CLASSES,
            device=self.device,
            verbose=False,
        )

        signs = self._parse_signs(sign_results)
        pedestrians, vehicles = self._parse_scene(scene_results)
        return signs, pedestrians, vehicles

    def detect(
        self,
        image_path: str,
        save_visualization: bool = True,
        vis_output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        p = self._validate_image_path(image_path)
        image_source = str(p.resolve())

        with Image.open(image_source) as im:
            width, height = im.size

        signs, pedestrians, vehicles = self._run_detection(image_source)
        if save_visualization:
            vis_path = vis_output_path or self._default_vis_path(p)
            self._save_visualization(
                image_source=image_source,
                signs=signs,
                pedestrians=pedestrians,
                vehicles=vehicles,
                vis_output_path=vis_path,
            )

        return {
            "success": True,
            "image_size": {"width": int(width), "height": int(height)},
            "traffic_signs": signs,
            "num_traffic_signs": len(signs),
            "pedestrians": pedestrians,
            "num_pedestrians": len(pedestrians),
            "vehicles": vehicles,
            "num_vehicles": len(vehicles),
        }

    def detect_base64(
        self,
        image_base64: str,
        save_visualization: bool = False,
        vis_output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not isinstance(image_base64, str) or not image_base64.strip():
            raise ValueError("image_base64 must be a non-empty string")

        try:
            image_bytes = base64.b64decode(image_base64, validate=True)
        except (binascii.Error, ValueError) as e:
            raise ValueError("invalid base64 image data") from e

        if len(image_bytes) < 4:
            raise ValueError("decoded image bytes too short")
        if not (image_bytes.startswith(b"\xff\xd8") and image_bytes.endswith(b"\xff\xd9")):
            raise ValueError("decoded image is not JPEG format")

        with Image.open(BytesIO(image_bytes)) as im:
            image = im.convert("RGB")
            width, height = image.size

        signs, pedestrians, vehicles = self._run_detection(image)

        if save_visualization:
            if vis_output_path:
                out = vis_output_path
            else:
                out = str((self.output_dir / "base64_detected.jpg").resolve())
            self._save_visualization_from_pil(
                image=image,
                signs=signs,
                pedestrians=pedestrians,
                vehicles=vehicles,
                vis_output_path=out,
            )

        return {
            "success": True,
            "image_size": {"width": int(width), "height": int(height)},
            "traffic_signs": signs,
            "num_traffic_signs": len(signs),
            "pedestrians": pedestrians,
            "num_pedestrians": len(pedestrians),
            "vehicles": vehicles,
            "num_vehicles": len(vehicles),
        }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CoreDetector: jpg path -> signs/pedestrians/vehicles")
    parser.add_argument("--image", required=True, help="Input jpg/jpeg path")
    parser.add_argument("--sign-model", default=None, help="Traffic sign model path (.pt)")
    parser.add_argument("--scene-model", default=None, help="Scene model path (.pt), default yolov8n.pt")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold")
    parser.add_argument("--iou", type=float, default=0.45, help="NMS IoU threshold")
    parser.add_argument("--img-size", type=int, default=640, help="Inference image size")
    parser.add_argument("--device", default=None, help="cuda:0 / cpu")
    parser.add_argument("--no-vis", action="store_true", help="Disable output visualization image")
    parser.add_argument("--vis-out", default=None, help="Visualization image output path (.jpg)")
    parser.add_argument("--out", default=None, help="Optional output json file path")
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    try:
        with open(os.devnull, "w", encoding="utf-8") as devnull, contextlib.redirect_stderr(devnull):
            detector = CoreDetector(
                sign_model_path=args.sign_model,
                scene_model_path=args.scene_model,
                conf=args.conf,
                iou=args.iou,
                img_size=args.img_size,
                device=args.device,
            )
            result = detector.detect(
                args.image,
                save_visualization=not args.no_vis,
                vis_output_path=args.vis_out,
            )
    except Exception as e:
        result = {
            "success": False,
            "error_type": type(e).__name__,
            "error": str(e),
            "traceback": traceback.format_exc(),
        }

    text = json.dumps(result, ensure_ascii=False, indent=2)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")

    print(text)


if __name__ == "__main__":
    main()
