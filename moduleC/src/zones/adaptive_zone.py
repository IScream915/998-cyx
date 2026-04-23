from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from src.zones.zone_model import BlindSpotZone


def _mask_base_source(source: str) -> str | None:
    normalized = str(source)
    for suffix in ("_imu", "_hold"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)]
    if normalized in {"segmentation", "external_mask"}:
        return normalized
    return None


@dataclass
class ZoneTemporalStabilizer:
    segmentation_alpha: float
    template_alpha: float
    hold_frames: int
    _previous_polygon_norm: np.ndarray | None = None
    _previous_mask_px: np.ndarray | None = None
    _hold_remaining: int = 0
    _last_mask_source: str | None = None

    def stabilize(
        self,
        zone: BlindSpotZone,
        *,
        template_zone: BlindSpotZone,
        image_w: int,
        image_h: int,
    ) -> BlindSpotZone:
        candidate = zone
        zone_mask_source = _mask_base_source(zone.source)
        if zone_mask_source is not None:
            self._hold_remaining = max(0, int(self.hold_frames))
            self._last_mask_source = zone_mask_source
        elif self._hold_remaining > 0 and self._previous_polygon_norm is not None:
            candidate = BlindSpotZone(
                camera_side=template_zone.camera_side,
                polygon_norm=self._previous_polygon_norm.copy(),
                polygon_px=template_zone.polygon_px.copy(),
                speed_kmh=float(template_zone.speed_kmh),
                signal_active=bool(template_zone.signal_active),
                imu_gz=float(template_zone.imu_gz),
                scale_used=float(template_zone.scale_used),
                source=f"{self._last_mask_source or 'segmentation'}_hold",
                mask_px=None if self._previous_mask_px is None else self._previous_mask_px.copy(),
            )
            self._hold_remaining -= 1
        else:
            self._hold_remaining = 0

        polygon_norm = np.asarray(candidate.polygon_norm, dtype=np.float32)
        if self._previous_polygon_norm is not None and self._previous_polygon_norm.shape == polygon_norm.shape:
            alpha = (
                float(self.segmentation_alpha)
                if _mask_base_source(candidate.source) is not None
                else float(self.template_alpha)
            )
            alpha = float(np.clip(alpha, 0.0, 1.0))
            polygon_norm = (1.0 - alpha) * self._previous_polygon_norm + alpha * polygon_norm
            polygon_norm = np.clip(polygon_norm.astype(np.float32), 0.0, 1.0)

        self._previous_polygon_norm = polygon_norm.copy()
        self._previous_mask_px = (
            None if candidate.mask_px is None else np.asarray(candidate.mask_px).copy()
        )
        polygon_px = np.column_stack(
            (
                np.round(polygon_norm[:, 0] * max(1, image_w - 1)),
                np.round(polygon_norm[:, 1] * max(1, image_h - 1)),
            )
        ).astype(np.int32)
        return BlindSpotZone(
            camera_side=candidate.camera_side,
            polygon_norm=polygon_norm,
            polygon_px=polygon_px,
            speed_kmh=float(candidate.speed_kmh),
            signal_active=bool(candidate.signal_active),
            imu_gz=float(candidate.imu_gz),
            scale_used=float(candidate.scale_used),
            source=candidate.source,
            mask_px=None if candidate.mask_px is None else np.asarray(candidate.mask_px).copy(),
        )


class AdaptiveZoneController:
    def __init__(self, config: dict):
        self.config = config["zones"]
        self.breakpoints = np.asarray(self.config["speed_scale"]["breakpoints"], dtype=float)
        self.scales = np.asarray(self.config["speed_scale"]["scales"], dtype=float)
        self.nominal_speed_kmh = float(np.interp(1.0, self.scales, self.breakpoints))
        self.signal_bonus = float(self.config["signal_expansion"]["value"])
        self.gz_thresh = float(self.config["imu_expansion"]["gz_thresh"])
        self.gz_max = float(self.config["imu_expansion"]["gz_max"])
        self.imu_max = float(self.config["imu_expansion"]["max_value"])
        segmentation_cfg = dict(self.config.get("segmentation", {}))
        self.segmentation_enabled = bool(segmentation_cfg.get("enabled", True))
        self.segmentation_threshold = float(segmentation_cfg.get("threshold", 0.5))
        self.segmentation_min_area_ratio = float(segmentation_cfg.get("min_area_ratio", 0.01))
        self.segmentation_max_area_ratio = float(segmentation_cfg.get("max_area_ratio", 0.60))
        self.segmentation_min_bottom_y_ratio = float(
            segmentation_cfg.get("min_bottom_y_ratio", 0.85)
        )
        self.segmentation_side_split_x = float(segmentation_cfg.get("side_split_x", 0.50))
        self.segmentation_open_kernel = self._odd_kernel_size(
            segmentation_cfg.get("open_kernel", 5)
        )
        self.segmentation_close_kernel = self._odd_kernel_size(
            segmentation_cfg.get("close_kernel", 9)
        )
        self.segmentation_edge_quantile = float(segmentation_cfg.get("edge_quantile", 0.08))
        self.segmentation_top_quantile = float(segmentation_cfg.get("top_quantile", 0.10))
        self.segmentation_band_height_ratio = float(
            segmentation_cfg.get("band_height_ratio", 0.12)
        )
        external_mask_cfg = dict(self.config.get("external_mask", {}))
        self.external_mask_polygon_points = max(
            8,
            int(external_mask_cfg.get("polygon_points", 128)),
        )
        self.external_mask_simplify_epsilon_ratio = float(
            external_mask_cfg.get("simplify_epsilon_ratio", 0.003)
        )
        self.external_mask_min_length_ratio = float(
            external_mask_cfg.get("min_length_ratio", 0.30)
        )
        self.external_mask_length_gain = float(
            external_mask_cfg.get("length_gain", 1.0)
        )
        self.external_mask_max_length_ratio = float(
            external_mask_cfg.get("max_length_ratio", 0.16)
        )
        stability_cfg = dict(self.config.get("stability", {}))
        self.stability_enabled = bool(stability_cfg.get("enabled", True))
        self.stability_segmentation_alpha = float(
            stability_cfg.get("segmentation_alpha", 0.78)
        )
        self.stability_template_alpha = float(stability_cfg.get("template_alpha", 0.04))
        self.stability_hold_frames = int(stability_cfg.get("hold_frames", 1))
        imu_segmentation_cfg = dict(self.config.get("imu_segmentation_bias", {}))
        self.imu_segmentation_enabled = bool(imu_segmentation_cfg.get("enabled", True))
        self.imu_segmentation_top_y_strength = float(
            imu_segmentation_cfg.get("top_y_strength", 0.22)
        )
        self.imu_segmentation_outer_edge_strength = float(
            imu_segmentation_cfg.get("outer_edge_strength", 0.28)
        )
        self.imu_segmentation_max_top_y_ratio = float(
            imu_segmentation_cfg.get("max_top_y_ratio", 0.06)
        )
        self.imu_segmentation_max_outer_edge_ratio = float(
            imu_segmentation_cfg.get("max_outer_edge_ratio", 0.08)
        )

    def compute_zone(
        self,
        speed_kmh: float,
        left_signal: bool,
        right_signal: bool,
        gz: float,
        img_w: int,
        img_h: int,
        side: Literal["left", "right"],
    ) -> BlindSpotZone:
        side_cfg = self.config[side]
        signal_active = left_signal if side == "left" else right_signal
        scale = self._speed_scale(speed_kmh)
        if signal_active:
            scale += self.signal_bonus
        scale += self._imu_expansion(gz, side)

        top_y = max(0.05, float(side_cfg["top_y_base"]) - (scale - 1.0) * 0.20)
        bot_half_w = min(0.48, float(side_cfg["bot_half_w_base"]) * scale)
        top_half_w = min(0.40, float(side_cfg["top_half_w_base"]) * scale)
        center_x = float(side_cfg["center_x"])

        polygon_norm = np.array(
            [
                [center_x - top_half_w, top_y],
                [center_x + top_half_w, top_y],
                [center_x + bot_half_w, 1.0],
                [center_x - bot_half_w, 1.0],
            ],
            dtype=np.float32,
        )
        polygon_norm[:, 0] = np.clip(polygon_norm[:, 0], 0.0, 1.0)
        polygon_norm[:, 1] = np.clip(polygon_norm[:, 1], 0.0, 1.0)

        polygon_px = np.column_stack(
            (
                np.round(polygon_norm[:, 0] * (img_w - 1)),
                np.round(polygon_norm[:, 1] * (img_h - 1)),
            )
        ).astype(np.int32)

        return BlindSpotZone(
            camera_side=side,
            polygon_norm=polygon_norm,
            polygon_px=polygon_px,
            speed_kmh=float(speed_kmh),
            signal_active=signal_active,
            imu_gz=float(gz),
            scale_used=float(scale),
            source="template",
            mask_px=None,
        )

    def refine_zone_from_mask(
        self,
        zone_mask: np.ndarray | None,
        fallback_zone: BlindSpotZone,
        *,
        source_name: str = "segmentation",
    ) -> BlindSpotZone:
        if not self.segmentation_enabled or zone_mask is None:
            return fallback_zone

        mask = np.asarray(zone_mask, dtype=np.float32)
        if mask.ndim != 2 or mask.size == 0:
            return fallback_zone

        image_h, image_w = mask.shape[:2]
        binary = (mask >= self.segmentation_threshold).astype(np.uint8)
        if not np.any(binary):
            return fallback_zone

        if self.segmentation_open_kernel > 1:
            kernel = np.ones(
                (self.segmentation_open_kernel, self.segmentation_open_kernel),
                dtype=np.uint8,
            )
            binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        if self.segmentation_close_kernel > 1:
            kernel = np.ones(
                (self.segmentation_close_kernel, self.segmentation_close_kernel),
                dtype=np.uint8,
            )
            binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        if not np.any(binary):
            return fallback_zone

        contours, _ = cv2.findContours(binary * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return fallback_zone

        contour = max(contours, key=cv2.contourArea)
        contour_area = float(cv2.contourArea(contour))
        image_area = float(max(1, image_w * image_h))
        area_ratio = contour_area / image_area
        if area_ratio < self.segmentation_min_area_ratio:
            return fallback_zone
        if area_ratio > self.segmentation_max_area_ratio:
            return fallback_zone
        zone_binary = binary
        if source_name == "external_mask":
            zone_binary = self._adjust_external_mask_length(binary, fallback_zone)
            polygon_px = self._external_mask_polygon(zone_binary, fallback_zone.camera_side)
        else:
            polygon_px = self._segmentation_quadrilateral(binary, fallback_zone.camera_side)
        if polygon_px is None:
            return fallback_zone
        if float(np.max(polygon_px[:, 1])) < (image_h - 1) * self.segmentation_min_bottom_y_ratio:
            return fallback_zone

        if source_name != "external_mask":
            centroid_x = float(np.mean(polygon_px[:, 0])) / max(1.0, float(image_w - 1))
            if fallback_zone.camera_side == "left" and centroid_x >= self.segmentation_side_split_x:
                return fallback_zone
            if fallback_zone.camera_side == "right" and centroid_x <= self.segmentation_side_split_x:
                return fallback_zone

        polygon_norm = np.column_stack(
            (
                polygon_px[:, 0].astype(np.float32) / max(1.0, float(image_w - 1)),
                polygon_px[:, 1].astype(np.float32) / max(1.0, float(image_h - 1)),
            )
        ).astype(np.float32)
        polygon_norm[:, 0] = np.clip(polygon_norm[:, 0], 0.0, 1.0)
        polygon_norm[:, 1] = np.clip(polygon_norm[:, 1], 0.0, 1.0)

        return BlindSpotZone(
            camera_side=fallback_zone.camera_side,
            polygon_norm=polygon_norm,
            polygon_px=polygon_px,
            speed_kmh=float(fallback_zone.speed_kmh),
            signal_active=bool(fallback_zone.signal_active),
            imu_gz=float(fallback_zone.imu_gz),
            scale_used=float(fallback_zone.scale_used),
            source=str(source_name),
            mask_px=(zone_binary.copy() if source_name == "external_mask" else None),
        )

    def apply_imu_bias_to_segmentation(
        self,
        zone: BlindSpotZone,
        *,
        template_zone: BlindSpotZone,
        image_w: int,
        image_h: int,
    ) -> BlindSpotZone:
        if not self.imu_segmentation_enabled or zone.source != "segmentation":
            return zone
        zone_mask_source = "segmentation"

        side = template_zone.camera_side
        imu_extra = self._imu_expansion(template_zone.imu_gz, side)
        if imu_extra <= 0.0 or self.imu_max <= 0.0:
            return zone
        imu_ratio = float(np.clip(imu_extra / self.imu_max, 0.0, 1.0))
        if imu_ratio <= 0.0:
            return zone

        neutral_zone = self.compute_zone(
            template_zone.speed_kmh,
            template_zone.signal_active if side == "left" else False,
            template_zone.signal_active if side == "right" else False,
            0.0,
            image_w,
            image_h,
            side,
        )

        polygon_norm = np.asarray(zone.polygon_norm, dtype=np.float32).copy()
        zone_height = max(1e-3, float(polygon_norm[3, 1] - polygon_norm[0, 1]))
        top_width = max(1e-3, float(polygon_norm[1, 0] - polygon_norm[0, 0]))
        bottom_width = max(1e-3, float(polygon_norm[2, 0] - polygon_norm[3, 0]))
        top_y_shift = max(
            0.0,
            float(neutral_zone.polygon_norm[0, 1] - template_zone.polygon_norm[0, 1]),
        )
        top_y_shift = min(
            top_y_shift * imu_ratio * self.imu_segmentation_top_y_strength,
            zone_height * max(0.0, self.imu_segmentation_max_top_y_ratio),
        )
        polygon_norm[0, 1] = np.clip(
            polygon_norm[0, 1] - top_y_shift,
            0.0,
            1.0,
        )
        polygon_norm[1, 1] = polygon_norm[0, 1]

        if side == "left":
            top_outer_shift = max(
                0.0,
                float(neutral_zone.polygon_norm[0, 0] - template_zone.polygon_norm[0, 0]),
            )
            bottom_outer_shift = max(
                0.0,
                float(neutral_zone.polygon_norm[3, 0] - template_zone.polygon_norm[3, 0]),
            )
            top_outer_shift = min(
                top_outer_shift * imu_ratio * self.imu_segmentation_outer_edge_strength,
                top_width * max(0.0, self.imu_segmentation_max_outer_edge_ratio),
            )
            bottom_outer_shift = min(
                bottom_outer_shift * imu_ratio * self.imu_segmentation_outer_edge_strength,
                bottom_width * max(0.0, self.imu_segmentation_max_outer_edge_ratio),
            )
            polygon_norm[0, 0] = np.clip(
                polygon_norm[0, 0] - top_outer_shift,
                0.0,
                min(1.0, polygon_norm[1, 0] - 1e-3),
            )
            polygon_norm[3, 0] = np.clip(
                polygon_norm[3, 0] - bottom_outer_shift,
                0.0,
                min(1.0, polygon_norm[2, 0] - 1e-3),
            )
        else:
            top_outer_shift = max(
                0.0,
                float(template_zone.polygon_norm[1, 0] - neutral_zone.polygon_norm[1, 0]),
            )
            bottom_outer_shift = max(
                0.0,
                float(template_zone.polygon_norm[2, 0] - neutral_zone.polygon_norm[2, 0]),
            )
            top_outer_shift = min(
                top_outer_shift * imu_ratio * self.imu_segmentation_outer_edge_strength,
                top_width * max(0.0, self.imu_segmentation_max_outer_edge_ratio),
            )
            bottom_outer_shift = min(
                bottom_outer_shift * imu_ratio * self.imu_segmentation_outer_edge_strength,
                bottom_width * max(0.0, self.imu_segmentation_max_outer_edge_ratio),
            )
            polygon_norm[1, 0] = np.clip(
                polygon_norm[1, 0] + top_outer_shift,
                max(0.0, polygon_norm[0, 0] + 1e-3),
                1.0,
            )
            polygon_norm[2, 0] = np.clip(
                polygon_norm[2, 0] + bottom_outer_shift,
                max(0.0, polygon_norm[3, 0] + 1e-3),
                1.0,
            )

        polygon_px = np.column_stack(
            (
                np.round(polygon_norm[:, 0] * max(1, image_w - 1)),
                np.round(polygon_norm[:, 1] * max(1, image_h - 1)),
            )
        ).astype(np.int32)
        return BlindSpotZone(
            camera_side=zone.camera_side,
            polygon_norm=polygon_norm,
            polygon_px=polygon_px,
            speed_kmh=float(zone.speed_kmh),
            signal_active=bool(zone.signal_active),
            imu_gz=float(zone.imu_gz),
            scale_used=float(zone.scale_used),
            source=f"{zone_mask_source}_imu",
            mask_px=None if zone.mask_px is None else np.asarray(zone.mask_px).copy(),
        )

    def build_stabilizer(self) -> ZoneTemporalStabilizer:
        if not self.stability_enabled:
            return ZoneTemporalStabilizer(
                segmentation_alpha=1.0,
                template_alpha=1.0,
                hold_frames=0,
            )
        return ZoneTemporalStabilizer(
            segmentation_alpha=self.stability_segmentation_alpha,
            template_alpha=self.stability_template_alpha,
            hold_frames=self.stability_hold_frames,
        )

    def _speed_scale(self, speed_kmh: float) -> float:
        return float(np.interp(speed_kmh, self.breakpoints, self.scales))

    def _imu_expansion(self, gz: float, side: str) -> float:
        gz_this_side = float(gz) if side == "left" else -float(gz)
        if gz_this_side <= self.gz_thresh:
            return 0.0
        scaled = (gz_this_side / self.gz_max) * self.imu_max
        return float(np.clip(scaled, 0.0, self.imu_max))

    def _segmentation_quadrilateral(
        self,
        binary: np.ndarray,
        side: str,
    ) -> np.ndarray | None:
        ys, xs = np.where(binary > 0)
        if ys.size == 0 or xs.size == 0:
            return None
        image_h, image_w = binary.shape[:2]
        y_min = int(np.min(ys))
        y_max = int(np.max(ys))
        if y_max <= y_min:
            return None

        top_y = int(round(np.quantile(ys.astype(np.float32), self.segmentation_top_quantile)))
        band_height = max(4, int(round((y_max - y_min + 1) * self.segmentation_band_height_ratio)))
        top_band_limit = min(y_max, top_y + band_height)
        bottom_band_limit = max(y_min, y_max - band_height)

        top_band_xs = xs[ys <= top_band_limit]
        bottom_band_xs = xs[ys >= bottom_band_limit]
        if top_band_xs.size < 4 or bottom_band_xs.size < 4:
            return None

        left_q = float(np.clip(self.segmentation_edge_quantile, 0.0, 0.49))
        right_q = 1.0 - left_q
        top_left_x = int(round(np.quantile(top_band_xs.astype(np.float32), left_q)))
        top_right_x = int(round(np.quantile(top_band_xs.astype(np.float32), right_q)))
        bottom_left_x = int(round(np.quantile(bottom_band_xs.astype(np.float32), left_q)))
        bottom_right_x = int(round(np.quantile(bottom_band_xs.astype(np.float32), right_q)))
        bottom_y = image_h - 1

        if top_right_x <= top_left_x or bottom_right_x <= bottom_left_x:
            return None
        polygon_px = np.array(
            [
                [top_left_x, top_y],
                [top_right_x, top_y],
                [bottom_right_x, bottom_y],
                [bottom_left_x, bottom_y],
            ],
            dtype=np.int32,
        )
        polygon_px[:, 0] = np.clip(polygon_px[:, 0], 0, max(0, image_w - 1))
        polygon_px[:, 1] = np.clip(polygon_px[:, 1], 0, max(0, image_h - 1))

        centroid_x = float(np.mean(polygon_px[:, 0])) / max(1.0, float(image_w - 1))
        if side == "left" and centroid_x >= self.segmentation_side_split_x:
            return None
        if side == "right" and centroid_x <= self.segmentation_side_split_x:
            return None
        return polygon_px

    def _external_mask_polygon(
        self,
        binary: np.ndarray,
        side: str,
    ) -> np.ndarray | None:
        contours, _ = cv2.findContours(binary * 255, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None
        contour = max(contours, key=cv2.contourArea)
        if contour is None or len(contour) < 3:
            return None
        arc_length = float(cv2.arcLength(contour, True))
        epsilon = max(1.0, arc_length * max(0.0, self.external_mask_simplify_epsilon_ratio))
        simplified = cv2.approxPolyDP(contour, epsilon, True)
        contour_points = simplified.reshape(-1, 2).astype(np.float32)
        if contour_points.shape[0] < 3:
            contour_points = contour.reshape(-1, 2).astype(np.float32)
        polygon_px = self._resample_closed_polygon(
            contour_points,
            target_points=self.external_mask_polygon_points,
        )
        if polygon_px is None:
            return None
        image_h, image_w = binary.shape[:2]
        polygon_px[:, 0] = np.clip(polygon_px[:, 0], 0, max(0, image_w - 1))
        polygon_px[:, 1] = np.clip(polygon_px[:, 1], 0, max(0, image_h - 1))
        polygon_px = self._canonicalize_polygon(polygon_px.astype(np.int32), side)
        return polygon_px.astype(np.int32)

    def _adjust_external_mask_length(
        self,
        binary: np.ndarray,
        fallback_zone: BlindSpotZone,
    ) -> np.ndarray:
        length_ratio = self._external_mask_length_ratio(fallback_zone)
        adjusted = np.zeros_like(binary)
        active_columns = np.where(np.any(binary > 0, axis=0))[0]
        if active_columns.size == 0:
            return binary
        max_extension_px = self._external_mask_max_extension_px(
            fallback_zone,
            image_w=binary.shape[1],
            image_h=binary.shape[0],
        )
        for column in active_columns.tolist():
            active_rows = np.where(binary[:, column] > 0)[0]
            if active_rows.size == 0:
                continue
            top = int(active_rows.min())
            bottom = int(active_rows.max())
            original_height = max(1, bottom - top + 1)
            desired_height = float(original_height) * max(0.0, length_ratio)
            if desired_height > original_height:
                desired_height = min(
                    desired_height,
                    float(original_height + max_extension_px),
                )
            desired_height_px = max(1, int(round(desired_height)))
            new_top = max(0, bottom - desired_height_px + 1)
            adjusted[new_top : bottom + 1, column] = 1
        if np.any(adjusted):
            return adjusted
        return binary

    def _external_mask_length_ratio(
        self,
        fallback_zone: BlindSpotZone,
    ) -> float:
        min_scale = float(np.min(self.scales))
        current_scale = float(fallback_zone.scale_used)
        min_ratio = float(np.clip(self.external_mask_min_length_ratio, 0.0, 1.0))
        if current_scale <= 1.0:
            if 1.0 - min_scale <= 1e-6:
                return 1.0
            alpha = float(np.clip((current_scale - min_scale) / (1.0 - min_scale), 0.0, 1.0))
            return float(min_ratio + alpha * (1.0 - min_ratio))
        return float(max(1.0, 1.0 + (current_scale - 1.0) * self.external_mask_length_gain))

    def _external_mask_max_extension_px(
        self,
        fallback_zone: BlindSpotZone,
        *,
        image_w: int,
        image_h: int,
    ) -> int:
        max_extension_px = max(
            0.0,
            max(1.0, float(image_h - 1)) * max(0.0, self.external_mask_max_length_ratio),
        )
        return int(round(max_extension_px))

    @staticmethod
    def _resample_closed_polygon(
        points: np.ndarray,
        *,
        target_points: int,
    ) -> np.ndarray | None:
        polygon = np.asarray(points, dtype=np.float32)
        if polygon.ndim != 2 or polygon.shape[0] < 3 or polygon.shape[1] != 2:
            return None
        closed = np.vstack([polygon, polygon[0]])
        segment_vectors = np.diff(closed, axis=0)
        segment_lengths = np.linalg.norm(segment_vectors, axis=1)
        perimeter = float(np.sum(segment_lengths))
        if perimeter <= 1e-6:
            return None
        cumulative = np.concatenate([[0.0], np.cumsum(segment_lengths)])
        samples = np.linspace(0.0, perimeter, num=target_points, endpoint=False, dtype=np.float32)
        resampled: list[np.ndarray] = []
        segment_index = 0
        for distance in samples:
            while segment_index + 1 < len(cumulative) and distance > cumulative[segment_index + 1]:
                segment_index += 1
            seg_len = max(1e-6, float(segment_lengths[min(segment_index, len(segment_lengths) - 1)]))
            start = closed[segment_index]
            end = closed[segment_index + 1]
            offset = float(distance - cumulative[segment_index])
            alpha = float(np.clip(offset / seg_len, 0.0, 1.0))
            resampled.append((1.0 - alpha) * start + alpha * end)
        if len(resampled) < 3:
            return None
        return np.round(np.asarray(resampled, dtype=np.float32)).astype(np.int32)

    @staticmethod
    def _canonicalize_polygon(
        polygon: np.ndarray,
        side: str,
    ) -> np.ndarray:
        points = np.asarray(polygon, dtype=np.int32)
        if points.ndim != 2 or points.shape[0] < 3:
            return points
        area = 0.0
        for idx in range(points.shape[0]):
            x1, y1 = points[idx]
            x2, y2 = points[(idx + 1) % points.shape[0]]
            area += float(x1 * y2 - x2 * y1)
        if area < 0.0:
            points = points[::-1].copy()
        if side == "right":
            anchor_index = min(
                range(points.shape[0]),
                key=lambda idx: (int(points[idx, 1]), -int(points[idx, 0])),
            )
        else:
            anchor_index = min(
                range(points.shape[0]),
                key=lambda idx: (int(points[idx, 1]), int(points[idx, 0])),
            )
        return np.roll(points, -anchor_index, axis=0)

    @staticmethod
    def _odd_kernel_size(value: object) -> int:
        try:
            size = int(value)
        except (TypeError, ValueError):
            return 0
        if size <= 1:
            return 0
        return size if size % 2 == 1 else size + 1
