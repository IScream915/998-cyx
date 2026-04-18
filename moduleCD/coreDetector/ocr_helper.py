from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional

import cv2
import numpy as np
import torch

OCR_NUMERIC_PREFIXES = ("pl", "il", "pm", "ph", "pr")
DEFAULT_OCR_MIN_CONF = 0.4

_OCR_READER = None


def should_run_ocr(class_name: str) -> bool:
    if not isinstance(class_name, str):
        return False
    return any(class_name.lower().startswith(p) for p in OCR_NUMERIC_PREFIXES)


def get_ocr_reader(device: Optional[str] = None):
    """
    Initialize EasyOCR reader lazily.
    Raises RuntimeError when dependency/model initialization fails.
    """
    global _OCR_READER
    if _OCR_READER is not None:
        return _OCR_READER

    try:
        import easyocr
    except Exception as exc:
        raise RuntimeError("EasyOCR 不可用，请安装 easyocr>=1.7.0") from exc

    use_gpu = torch.cuda.is_available()
    if isinstance(device, str):
        use_gpu = use_gpu and device.lower().startswith("cuda")

    try:
        # First run may download OCR model files; failures should fail startup.
        _OCR_READER = easyocr.Reader(["en"], gpu=use_gpu, verbose=False)
    except Exception as exc:
        raise RuntimeError("EasyOCR 初始化失败（可能是模型下载失败或缓存权限问题）") from exc

    return _OCR_READER


def _crop_sign_region(image_rgb: np.ndarray, bbox: List[float], padding: float = 0.10) -> np.ndarray:
    h, w = image_rgb.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in bbox]
    pw = max(4, int((x2 - x1) * padding))
    ph = max(4, int((y2 - y1) * padding))
    x1 = max(0, x1 - pw)
    y1 = max(0, y1 - ph)
    x2 = min(w, x2 + pw)
    y2 = min(h, y2 + ph)
    return image_rgb[y1:y2, x1:x2].copy()


def _preprocess_for_ocr(crop_rgb: np.ndarray) -> np.ndarray:
    h, w = crop_rgb.shape[:2]
    if min(h, w) < 80:
        scale = 80.0 / float(min(h, w))
        crop_rgb = cv2.resize(
            crop_rgb,
            (int(w * scale), int(h * scale)),
            interpolation=cv2.INTER_CUBIC,
        )
    gray = cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(4, 4))
    return clahe.apply(gray)


def _extract_sign_text(image_rgb: np.ndarray, bbox: List[float], reader: Any) -> Dict[str, Any]:
    try:
        crop = _crop_sign_region(image_rgb, bbox)
        if crop.size == 0 or min(crop.shape[:2]) < 5:
            return {
                "raw_text": "",
                "numbers": [],
                "ocr_conf": 0.0,
                "success": False,
                "error": "crop too small",
            }

        processed = _preprocess_for_ocr(crop)
        ocr_results = reader.readtext(
            processed,
            detail=1,
            paragraph=False,
            allowlist="0123456789.",
        )
        if not ocr_results:
            return {
                "raw_text": "",
                "numbers": [],
                "ocr_conf": 0.0,
                "success": True,
                "error": None,
            }

        texts: List[str] = []
        confs: List[float] = []
        for (_, text, conf) in ocr_results:
            text = str(text).strip()
            if text:
                texts.append(text)
                confs.append(float(conf))

        raw_text = " ".join(texts)
        avg_conf = float(np.mean(confs)) if confs else 0.0
        numbers = [float(m) for m in re.findall(r"\d+\.?\d*", raw_text)]
        return {
            "raw_text": raw_text,
            "numbers": numbers,
            "ocr_conf": round(avg_conf, 4),
            "success": True,
            "error": None,
        }
    except Exception as exc:
        return {
            "raw_text": "",
            "numbers": [],
            "ocr_conf": 0.0,
            "success": False,
            "error": str(exc),
        }


def _normalize_known_classes(known_classes: Optional[Iterable[str]]) -> Optional[set[str]]:
    if known_classes is None:
        return None
    normalized = {str(x) for x in known_classes if str(x)}
    return normalized or None


def apply_ocr_primary_inplace(
    image_rgb: np.ndarray,
    detections: List[Dict[str, Any]],
    known_classes: Optional[Iterable[str]] = None,
    ocr_min_conf: float = DEFAULT_OCR_MIN_CONF,
    reader: Any = None,
) -> List[Dict[str, Any]]:
    """
    Apply OCR-primary correction on numeric sign classes in-place.
    """
    if not detections:
        return detections

    if reader is None:
        reader = get_ocr_reader()

    known = _normalize_known_classes(known_classes)
    min_conf = max(0.0, min(1.0, float(ocr_min_conf)))

    for det in detections:
        class_name = str(det.get("class_name", ""))
        bbox = det.get("bbox", [])

        if not should_run_ocr(class_name):
            continue
        if not bbox:
            continue

        ocr_result = _extract_sign_text(image_rgb, bbox, reader)
        if not ocr_result.get("success"):
            continue

        numbers = ocr_result.get("numbers", [])
        conf = float(ocr_result.get("ocr_conf", 0.0))
        if not numbers or conf < min_conf:
            continue

        prefix_match = re.match(r"^[a-zA-Z]+", class_name)
        if not prefix_match:
            continue

        prefix = prefix_match.group(0)
        ocr_num = float(numbers[0])
        candidate_int = f"{prefix}{int(ocr_num)}"
        candidate_float = f"{prefix}{ocr_num}".rstrip("0").rstrip(".")

        corrected = None
        if known is None:
            corrected = candidate_int
        elif candidate_int in known:
            corrected = candidate_int
        elif candidate_float in known:
            corrected = candidate_float

        if corrected and corrected != class_name:
            det["class_name"] = corrected

    return detections
