from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np


@dataclass
class BlindSpotZone:
    camera_side: Literal["left", "right"]
    polygon_norm: np.ndarray
    polygon_px: np.ndarray
    speed_kmh: float
    signal_active: bool
    imu_gz: float
    scale_used: float
    source: str = "template"
    mask_px: np.ndarray | None = None

    def contains_point(self, cx: float, cy: float) -> bool:
        if self.mask_px is not None:
            mask = np.asarray(self.mask_px)
            if mask.ndim == 2 and mask.size > 0:
                px = int(round(float(cx)))
                py = int(round(float(cy)))
                if 0 <= py < mask.shape[0] and 0 <= px < mask.shape[1]:
                    return bool(mask[py, px] > 0)
                return False
        contour = self.polygon_px.reshape((-1, 1, 2)).astype(np.int32)
        return cv2.pointPolygonTest(contour, (float(cx), float(cy)), False) >= 0

    def contains_bbox_bottom_center(self, x1: float, y1: float, x2: float, y2: float) -> bool:
        return self.contains_point((x1 + x2) / 2.0, float(y2))
