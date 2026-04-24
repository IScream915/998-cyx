from __future__ import annotations

import argparse
import base64
import binascii
import contextlib
from concurrent.futures import ThreadPoolExecutor
import cv2
import json
import logging
import numpy as np
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

try:
    from .ocr_helper import (
        DEFAULT_OCR_MIN_CONF,
        apply_ocr_primary_inplace,
        get_ocr_reader,
    )
except ImportError:
    # Support running as script: python3 coreDetector/core_detector.py
    from ocr_helper import (  # type: ignore
        DEFAULT_OCR_MIN_CONF,
        apply_ocr_primary_inplace,
        get_ocr_reader,
    )

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

SCENE_CLASSES = [0, 1, 2, 3, 5, 7, 9]
VEHICLE_VIS_COLORS = {
    "bicycle": (0, 210, 210),
    "car": (0, 165, 255),
    "motorcycle": (0, 200, 255),
    "bus": (180, 50, 255),
    "truck": (60, 80, 255),
}

TRAFFIC_LIGHT_COLORS = {"red", "yellow", "green", "unknown"}


def _default_num_threads() -> int:
    cpu_count = os.cpu_count() or 1
    return max(1, min(4, cpu_count))


class CoreDetector:
    """
    Portable detector for:
    1) traffic signs
    2) pedestrians
    3) vehicles
    4) traffic lights (red/yellow/green/unknown)

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
        num_threads: Optional[int] = None,
        num_interop_threads: int = 1,
        enable_parallel_infer: bool = True,
        enable_ocr: bool = True,
        ocr_min_conf: float = DEFAULT_OCR_MIN_CONF,
    ) -> None:
        self.conf = conf
        self.iou = iou
        self.img_size = img_size
        self.device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        self.num_threads = self._normalize_positive_int(num_threads, _default_num_threads())
        self.num_interop_threads = self._normalize_positive_int(num_interop_threads, 1)
        self.enable_parallel_infer = bool(enable_parallel_infer)
        self.enable_ocr = bool(enable_ocr)
        self.ocr_min_conf = self._normalize_unit_float(ocr_min_conf, DEFAULT_OCR_MIN_CONF)
        self._parallel_executor: Optional[ThreadPoolExecutor] = None
        self._ocr_reader = None

        # Keep CLI output JSON-only by reducing third-party log noise.
        os.environ.setdefault("YOLO_VERBOSE", "False")
        YOLO_LOGGER.setLevel(logging.ERROR)
        _patch_ultralytics_export_formats_for_inference()
        self._configure_torch_threads()

        self.base_dir = Path(__file__).resolve().parent
        self.sign_model_path = self._resolve_sign_model_path(sign_model_path)
        self.scene_model_path = self._resolve_scene_model_path(scene_model_path)

        self.sign_model = YOLO(self.sign_model_path)
        self.scene_model = YOLO(self.scene_model_path)
        self._sign_class_names = self._extract_sign_class_names()

        if self.enable_ocr:
            # Startup precheck: fail fast if EasyOCR is unavailable or cannot initialize.
            self._ocr_reader = get_ocr_reader(device=self.device)

        if self.enable_parallel_infer:
            self._parallel_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cd-infer")
        self.output_dir = self.base_dir / "outputs"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _normalize_positive_int(value: Optional[int], default: int) -> int:
        if value is None:
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    @staticmethod
    def _normalize_unit_float(value: Optional[float], default: float) -> float:
        if value is None:
            return default
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return max(0.0, min(1.0, parsed))

    def _configure_torch_threads(self) -> None:
        # Align OpenMP and torch thread pool to improve CPU inference throughput.
        os.environ["OMP_NUM_THREADS"] = str(self.num_threads)
        try:
            torch.set_num_threads(self.num_threads)
        except Exception as exc:
            logging.warning("设置 torch 线程数失败，继续运行: %s", exc)

        try:
            torch.set_num_interop_threads(self.num_interop_threads)
        except RuntimeError:
            # torch 只允许在生命周期早期设置 interop 线程，失败时保持当前值即可。
            pass
        except Exception as exc:
            logging.warning("设置 torch interop 线程数失败，继续运行: %s", exc)

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

    def _extract_sign_class_names(self) -> Optional[List[str]]:
        names = getattr(self.sign_model, "names", None)
        if isinstance(names, dict):
            return [str(v) for v in names.values()]
        if isinstance(names, (list, tuple)):
            return [str(v) for v in names]
        return None

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

    @staticmethod
    def _detect_traffic_light_color(image_rgb: np.ndarray, bbox: List[int]) -> str:
        x1, y1, x2, y2 = [int(v) for v in bbox]
        h, w = image_rgb.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w, x2), min(h, y2)
        if x2 <= x1 or y2 <= y1:
            return "unknown"

        crop = image_rgb[y1:y2, x1:x2]
        if crop.size == 0:
            return "unknown"

        hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
        bright_mask = (hsv[:, :, 1] > 80) & (hsv[:, :, 2] > 80)

        def _count(lower: List[int], upper: List[int]) -> int:
            mask = cv2.inRange(hsv, np.array(lower), np.array(upper))
            return int(np.sum((mask > 0) & bright_mask))

        red1 = _count([0, 100, 100], [10, 255, 255])
        red2 = _count([160, 100, 100], [180, 255, 255])
        yellow = _count([18, 80, 100], [38, 255, 255])
        green = _count([40, 80, 80], [90, 255, 255])

        counts = {"red": red1 + red2, "yellow": yellow, "green": green}
        best_color = max(counts, key=counts.get)
        if counts[best_color] <= 20:
            return "unknown"
        return best_color

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

    def _render_visualization_image(
        self,
        image: Image.Image,
        signs: List[Dict[str, Any]],
        pedestrians: List[Dict[str, Any]],
        vehicles: List[Dict[str, Any]],
    ) -> Image.Image:
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

        return canvas

    @staticmethod
    def _save_visualization_canvas(canvas: Image.Image, vis_output_path: str) -> str:
        out_path = Path(vis_output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_path, format="JPEG", quality=92)
        return str(out_path)

    @staticmethod
    def _encode_jpeg_base64_from_pil(canvas: Image.Image, quality: int = 88) -> str:
        buffer = BytesIO()
        canvas.convert("RGB").save(buffer, format="JPEG", quality=quality)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def _save_visualization(
        self,
        image_source: str,
        signs: List[Dict[str, Any]],
        pedestrians: List[Dict[str, Any]],
        vehicles: List[Dict[str, Any]],
        vis_output_path: str,
    ) -> str:
        with Image.open(image_source) as im:
            canvas = self._render_visualization_image(im, signs, pedestrians, vehicles)
        return self._save_visualization_canvas(canvas, vis_output_path)

    def _save_visualization_from_pil(
        self,
        image: Image.Image,
        signs: List[Dict[str, Any]],
        pedestrians: List[Dict[str, Any]],
        vehicles: List[Dict[str, Any]],
        vis_output_path: str,
    ) -> str:
        canvas = self._render_visualization_image(image, signs, pedestrians, vehicles)
        return self._save_visualization_canvas(canvas, vis_output_path)

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

    def _parse_scene(
        self,
        results: Any,
        image_rgb: np.ndarray,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        pedestrians: List[Dict[str, Any]] = []
        vehicles: List[Dict[str, Any]] = []
        traffic_lights: List[Dict[str, Any]] = []

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
                elif cls_id == 9:
                    light_color = self._detect_traffic_light_color(image_rgb, bbox)
                    if light_color not in TRAFFIC_LIGHT_COLORS:
                        light_color = "unknown"
                    traffic_lights.append(
                        {
                            "light_color": light_color,
                            "confidence": conf_v,
                        }
                    )

        return pedestrians, vehicles, traffic_lights

    def _predict_sign(self, source: Any) -> Any:
        return self.sign_model.predict(
            source=source,
            conf=self.conf,
            iou=self.iou,
            imgsz=self.img_size,
            device=self.device,
            verbose=False,
            agnostic_nms=True,  # Avoid duplicate multi-class boxes for the same traffic sign.
        )

    def _predict_scene(self, source: Any) -> Any:
        return self.scene_model.predict(
            source=source,
            conf=self.conf,
            iou=self.iou,
            imgsz=self.img_size,
            classes=SCENE_CLASSES,
            device=self.device,
            verbose=False,
        )

    def _run_detection_serial(
        self,
        source: Any,
        image_rgb: np.ndarray,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        sign_results = self._predict_sign(source)
        scene_results = self._predict_scene(source)

        signs = self._parse_signs(sign_results)
        pedestrians, vehicles, traffic_lights = self._parse_scene(scene_results, image_rgb)
        return signs, pedestrians, vehicles, traffic_lights

    def _run_detection(
        self,
        source: Any,
        image_rgb: np.ndarray,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        if self._parallel_executor is not None:
            try:
                sign_future = self._parallel_executor.submit(self._predict_sign, source)
                scene_future = self._parallel_executor.submit(self._predict_scene, source)
                sign_results = sign_future.result()
                scene_results = scene_future.result()
                signs = self._parse_signs(sign_results)
                pedestrians, vehicles, traffic_lights = self._parse_scene(scene_results, image_rgb)
                return signs, pedestrians, vehicles, traffic_lights
            except Exception as exc:
                # 并行链路异常时退回串行，优先保证服务连续性与结果稳定性。
                logging.exception("并行推理失败，回退串行模式: %s", exc)
                self.enable_parallel_infer = False
                self.close()
                self._parallel_executor = None

        return self._run_detection_serial(source, image_rgb)

    def close(self) -> None:
        if self._parallel_executor is not None:
            self._parallel_executor.shutdown(wait=True, cancel_futures=False)
            self._parallel_executor = None

    def __del__(self) -> None:
        self.close()

    def _apply_ocr_to_signs(self, image_rgb: np.ndarray, signs: List[Dict[str, Any]]) -> None:
        if not self.enable_ocr or not signs:
            return
        apply_ocr_primary_inplace(
            image_rgb=image_rgb,
            detections=signs,
            known_classes=self._sign_class_names,
            ocr_min_conf=self.ocr_min_conf,
            reader=self._ocr_reader,
        )

    def detect(
        self,
        image_path: str,
        save_visualization: bool = True,
        vis_output_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        p = self._validate_image_path(image_path)
        image_source = str(p.resolve())

        with Image.open(image_source) as im:
            rgb_image = im.convert("RGB")
            width, height = rgb_image.size
            image_rgb = np.array(rgb_image)

        signs, pedestrians, vehicles, traffic_lights = self._run_detection(image_source, image_rgb)
        self._apply_ocr_to_signs(image_rgb, signs)
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
            "traffic_lights": traffic_lights,
        }

    def detect_base64(
        self,
        image_base64: str,
        save_visualization: bool = False,
        vis_output_path: Optional[str] = None,
        return_visualization_base64: bool = False,
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
            image_rgb = np.array(image)

        signs, pedestrians, vehicles, traffic_lights = self._run_detection(image, image_rgb)
        self._apply_ocr_to_signs(image_rgb, signs)

        visualization_base64: Optional[str] = None
        visualization_error: Optional[str] = None

        if save_visualization or return_visualization_base64:
            try:
                canvas = self._render_visualization_image(image, signs, pedestrians, vehicles)
                if save_visualization:
                    if vis_output_path:
                        out = vis_output_path
                    else:
                        out = str((self.output_dir / "base64_detected.jpg").resolve())
                    self._save_visualization_canvas(canvas, out)
                if return_visualization_base64:
                    visualization_base64 = self._encode_jpeg_base64_from_pil(canvas)
            except Exception as exc:
                visualization_error = str(exc)
                if save_visualization:
                    raise

        result = {
            "success": True,
            "image_size": {"width": int(width), "height": int(height)},
            "traffic_signs": signs,
            "num_traffic_signs": len(signs),
            "pedestrians": pedestrians,
            "num_pedestrians": len(pedestrians),
            "vehicles": vehicles,
            "num_vehicles": len(vehicles),
            "traffic_lights": traffic_lights,
        }
        if return_visualization_base64 and isinstance(visualization_base64, str) and visualization_base64:
            result["visualization_base64"] = visualization_base64
        if return_visualization_base64 and visualization_error:
            result["visualization_error"] = visualization_error
        return result


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CoreDetector: jpg path -> signs/pedestrians/vehicles/traffic_lights")
    parser.add_argument("--image", required=True, help="Input jpg/jpeg path")
    parser.add_argument("--sign-model", default=None, help="Traffic sign model path (.pt)")
    parser.add_argument("--scene-model", default=None, help="Scene model path (.pt), default yolov8n.pt")
    parser.add_argument("--conf", type=float, default=0.25, help="Confidence threshold")
    parser.add_argument("--iou", type=float, default=0.45, help="NMS IoU threshold")
    parser.add_argument("--img-size", type=int, default=640, help="Inference image size")
    parser.add_argument("--device", default=None, help="cuda:0 / cpu")
    parser.add_argument("--num-threads", type=int, default=None, help="torch/OpenMP 线程数，默认按 CPU 自动取值")
    parser.add_argument("--num-interop-threads", type=int, default=1, help="torch interop 线程数")
    parser.add_argument("--disable-parallel-infer", action="store_true", help="禁用双模型并行推理，退回串行")
    parser.add_argument("--disable-ocr", action="store_true", help="禁用数字类交通标志 OCR 主识别")
    parser.add_argument("--ocr-min-conf", type=float, default=DEFAULT_OCR_MIN_CONF, help="OCR 主识别最低置信度阈值")
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
                num_threads=args.num_threads,
                num_interop_threads=args.num_interop_threads,
                enable_parallel_infer=not args.disable_parallel_infer,
                enable_ocr=not args.disable_ocr,
                ocr_min_conf=args.ocr_min_conf,
            )
            result = detector.detect(
                args.image,
                save_visualization=not args.no_vis,
                vis_output_path=args.vis_out,
            )
            detector.close()
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
