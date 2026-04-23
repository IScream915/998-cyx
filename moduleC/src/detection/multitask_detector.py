from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import torch

from src.utils.runtime import get_device
from training.multitask_yolo import MultitaskYOLO


@dataclass
class DetectionResult:
    bboxes: list[tuple[int, int, int, int]]
    confidences: list[float]
    class_ids: list[int]
    track_inputs: np.ndarray
    zone_mask: np.ndarray


@dataclass(frozen=True)
class DetectionTemporalPrior:
    bbox: tuple[int, int, int, int]
    confidence: float = 1.0


class MultitaskDetector:
    def __init__(self, model_path: str, config: dict, force_ultralytics: bool = False):
        self.config = config
        self.training_cfg = config["training"]
        self.det_cfg = config["detection"]
        runtime_device = str(self.det_cfg.get("device", self.training_cfg.get("device", "auto")))
        self.device = torch.device(get_device(runtime_device))
        self.input_size = int(self.training_cfg["imgsz"])
        self.conf_thresh = float(self.det_cfg["conf_thresh"])
        self.custom_conf_thresh = float(self.det_cfg.get("custom_conf_thresh", self.conf_thresh))
        self.iou_thresh = float(self.det_cfg.get("iou_thresh", 0.45))
        self.target_classes = list(self.det_cfg["target_classes"])
        self.class_names = list(self.training_cfg.get("class_names", ["person", "bicycle", "car", "motorcycle", "bus", "truck"]))
        self.box_format = str(self.training_cfg.get("box_format", "legacy_cxcywh"))
        self.reg_max = int(self.training_cfg.get("reg_max", 0))
        self.custom_quality_ranking_enabled = bool(
            self.det_cfg.get("custom_quality_ranking_enabled", False)
        )
        self.custom_quality_ranking_strength = float(
            self.det_cfg.get("custom_quality_ranking_strength", 0.35)
        )
        self.custom_quality_ranking_floor = float(
            self.det_cfg.get("custom_quality_ranking_floor", 0.65)
        )
        self.backend = "custom"
        self.model: Any
        self.yolo_model = None

        weight_path = Path(model_path)
        if force_ultralytics:
            self._init_ultralytics(self._resolve_existing_weight(weight_path, include_pretrained=True))
        else:
            self._init_custom(weight_path)

    def _det_opt(self, key: str, default: Any) -> Any:
        return getattr(self, "det_cfg", {}).get(key, default)

    @torch.no_grad()
    def detect(self, frame: np.ndarray, camera_side: str | None = None) -> DetectionResult:
        return self.detect_batch([frame], camera_sides=[camera_side])[0]

    @torch.no_grad()
    def detect_batch(
        self,
        frames: list[np.ndarray],
        camera_sides: list[str | None] | None = None,
        temporal_priors: list[list[DetectionTemporalPrior]] | None = None,
        zone_priors: list[np.ndarray | None] | None = None,
    ) -> list[DetectionResult]:
        if not frames:
            return []
        if camera_sides is None:
            camera_sides = [None] * len(frames)
        if len(camera_sides) != len(frames):
            raise ValueError("camera_sides must match frames length.")
        if temporal_priors is not None and len(temporal_priors) != len(frames):
            raise ValueError("temporal_priors must match frames length.")
        if zone_priors is not None and len(zone_priors) != len(frames):
            raise ValueError("zone_priors must match frames length.")
        if self.backend == "ultralytics":
            return [self._detect_ultralytics(frame) for frame in frames]
        return self._detect_custom_batch(
            frames,
            camera_sides=camera_sides,
            temporal_priors=temporal_priors,
            zone_priors=zone_priors,
        )

    def track_with_ultralytics(
        self, frame: np.ndarray, camera_side: str
    ) -> tuple[list[Any], np.ndarray]:
        del camera_side
        if self.backend != "ultralytics" or self.yolo_model is None:
            raise RuntimeError("Ultralytics backend is not active.")
        from src.tracking.bytetrack_wrapper import TrackedObject

        results = self.yolo_model.track(
            frame,
            persist=True,
            tracker="config/bytetrack.yaml",
            conf=self.conf_thresh,
            classes=self.target_classes,
            verbose=False,
            device=str(self.device),
        )
        tracked: list[Any] = []
        zone_mask = np.zeros(frame.shape[:2], dtype=np.float32)
        boxes = results[0].boxes
        if boxes.id is not None:
            for box, track_id in zip(boxes, boxes.id):
                xyxy = box.xyxy[0].detach().cpu().numpy().astype(int)
                raw_class_id = int(box.cls[0])
                class_id = self._map_ultralytics_class(raw_class_id)
                if class_id is None:
                    continue
                tracked.append(
                    TrackedObject(
                        track_id=int(track_id),
                        bbox=(int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3])),
                        confidence=float(box.conf[0]),
                        class_id=class_id,
                        class_name=self._ultralytics_class_name(raw_class_id),
                        center=((float(xyxy[0]) + float(xyxy[2])) / 2.0, float(xyxy[3])),
                        area=float(max(0, xyxy[2] - xyxy[0]) * max(0, xyxy[3] - xyxy[1])),
                    )
                )
        return tracked, zone_mask

    @staticmethod
    def _is_ultralytics_model_alias(candidate: Path) -> bool:
        candidate_str = str(candidate)
        return (
            len(candidate.parts) == 1
            and candidate.suffix == ".pt"
            and candidate_str.startswith("yolo")
        )

    def _resolve_existing_weight(self, preferred: Path, include_pretrained: bool) -> Path:
        candidates = [preferred]
        if include_pretrained:
            candidates.append(Path(self.det_cfg["pretrained_path"]))
        for candidate in candidates:
            if candidate.exists() or self._is_ultralytics_model_alias(candidate):
                return candidate
        searched = ", ".join(str(item) for item in candidates)
        raise FileNotFoundError(
            f"No detector weights found. Expected one of: {searched}. "
            "Train a model first or provide a valid pretrained checkpoint."
        )

    def _init_custom(self, weight_path: Path) -> None:
        if not weight_path.exists():
            self._init_ultralytics(self._resolve_existing_weight(Path(self.det_cfg["pretrained_path"]), include_pretrained=False))
            return

        try:
            checkpoint = torch.load(weight_path, map_location="cpu")
            if isinstance(checkpoint, dict):
                self.input_size = int(checkpoint.get("input_size", self.input_size))
                self.class_names = list(checkpoint.get("class_names", self.class_names))
                self.box_format = str(checkpoint.get("box_format", "legacy_cxcywh"))
                self.reg_max = int(checkpoint.get("reg_max", 0))
                declared_backend = checkpoint.get("backend")
            else:
                self.box_format = "legacy_cxcywh"
                self.reg_max = 0
                declared_backend = None
            self.model = MultitaskYOLO(
                num_classes=max(1, len(self.class_names)),
                input_size=self.input_size,
                box_format=self.box_format,
                reg_max=self.reg_max,
                pretrained_path=None,
            ).to(self.device)
            state_dict = checkpoint.get("model", checkpoint) if isinstance(checkpoint, dict) else checkpoint
            self.model.load_state_dict(state_dict, strict=False)
            if declared_backend and declared_backend != "custom":
                raise RuntimeError(f"Checkpoint backend mismatch: {declared_backend}")
        except Exception:
            self._init_ultralytics(self._resolve_existing_weight(weight_path, include_pretrained=True))
            return
        self.model.eval()
        self.backend = "custom"

    def _init_ultralytics(self, weight_path: Path) -> None:
        from ultralytics import YOLO

        self.yolo_model = YOLO(str(weight_path))
        self.model = self.yolo_model
        self.backend = "ultralytics"

    def _detect_custom_batch(
        self,
        frames: list[np.ndarray],
        camera_sides: list[str | None],
        temporal_priors: list[list[DetectionTemporalPrior]] | None = None,
        zone_priors: list[np.ndarray | None] | None = None,
    ) -> list[DetectionResult]:
        if temporal_priors is None:
            temporal_priors = [[] for _ in frames]
        if zone_priors is None:
            zone_priors = [None for _ in frames]
        batch = np.stack(
            [self._prepare_custom_frame(frame) for frame in frames],
            axis=0,
        )
        tensor = torch.from_numpy(batch).float().to(self.device) / 255.0
        preds = self.model(tensor)
        seg_batch = preds["seg"].detach().cpu().numpy().astype(np.float32)
        det_batch = preds["det"].detach().cpu().numpy()
        results: list[DetectionResult] = []
        for idx, (frame, camera_side, frame_priors, zone_prior) in enumerate(
            zip(frames, camera_sides, temporal_priors, zone_priors)
        ):
            zone_mask = seg_batch[idx].squeeze()
            if zone_mask.shape[:2] != frame.shape[:2]:
                zone_mask = cv2.resize(zone_mask, (frame.shape[1], frame.shape[0]))
            bboxes, confidences, class_ids = self._decode_custom_predictions(
                det_batch[idx],
                frame.shape[1],
                frame.shape[0],
                temporal_priors=frame_priors,
                zone_prior=zone_prior,
            )
            bboxes, confidences, class_ids = self._apply_custom_side_prior(
                bboxes,
                confidences,
                class_ids,
                frame.shape[1],
                camera_side,
            )
            results.append(
                DetectionResult(
                    bboxes=bboxes,
                    confidences=confidences,
                    class_ids=class_ids,
                    track_inputs=self._to_track_inputs(bboxes, confidences, class_ids),
                    zone_mask=zone_mask,
                )
            )
        return results

    def _detect_custom(self, frame: np.ndarray, camera_side: str | None = None) -> DetectionResult:
        return self._detect_custom_batch([frame], camera_sides=[camera_side])[0]

    def _prepare_custom_frame(self, frame: np.ndarray) -> np.ndarray:
        resized = cv2.resize(frame, (self.input_size, self.input_size))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        chw = np.transpose(rgb, (2, 0, 1))
        return np.ascontiguousarray(chw, dtype=np.float32)

    def _detect_ultralytics(self, frame: np.ndarray) -> DetectionResult:
        results = self.yolo_model.predict(
            source=frame,
            conf=self.conf_thresh,
            classes=self.target_classes,
            verbose=False,
            device=str(self.device),
        )
        bboxes: list[tuple[int, int, int, int]] = []
        confidences: list[float] = []
        class_ids: list[int] = []
        for box in results[0].boxes:
            xyxy = box.xyxy[0].detach().cpu().numpy().astype(int)
            class_id = self._map_ultralytics_class(int(box.cls[0]))
            if class_id is None:
                continue
            bboxes.append((int(xyxy[0]), int(xyxy[1]), int(xyxy[2]), int(xyxy[3])))
            confidences.append(float(box.conf[0]))
            class_ids.append(class_id)
        zone_mask = np.zeros(frame.shape[:2], dtype=np.float32)
        return DetectionResult(
            bboxes=bboxes,
            confidences=confidences,
            class_ids=class_ids,
            track_inputs=self._to_track_inputs(bboxes, confidences, class_ids),
            zone_mask=zone_mask,
        )

    def _decode_custom_predictions(
        self,
        pred: np.ndarray,
        orig_w: int,
        orig_h: int,
        temporal_priors: list[DetectionTemporalPrior] | None = None,
        zone_prior: np.ndarray | None = None,
    ) -> tuple[list[tuple[int, int, int, int]], list[float], list[int]]:
        if self.box_format == "ltrb_dfl":
            return self._decode_custom_predictions_dfl(
                pred,
                orig_w,
                orig_h,
                temporal_priors=temporal_priors,
                zone_prior=zone_prior,
            )
        return self._decode_custom_predictions_legacy(
            pred,
            orig_w,
            orig_h,
            temporal_priors=temporal_priors,
            zone_prior=zone_prior,
        )

    def _decode_custom_predictions_legacy(
        self,
        pred: np.ndarray,
        orig_w: int,
        orig_h: int,
        temporal_priors: list[DetectionTemporalPrior] | None = None,
        zone_prior: np.ndarray | None = None,
    ) -> tuple[list[tuple[int, int, int, int]], list[float], list[int]]:
        _, grid_h, grid_w = pred.shape
        obj = self._sigmoid_np(pred[0])
        box = self._sigmoid_np(pred[1:5])
        cls_logits = pred[5:]
        cls_prob = self._sigmoid_np(cls_logits)
        temporal_prior_enabled = bool(
            self._det_opt("custom_temporal_prior_enabled", False)
        ) and bool(temporal_priors)
        zone_prior_enabled = bool(
            self._det_opt("custom_zone_rescue_enabled", False)
        ) and zone_prior is not None
        decode_thresh = (
            min(
                self.custom_conf_thresh,
                float(
                    self._det_opt(
                        "custom_temporal_prior_min_conf",
                        self.custom_conf_thresh,
                    )
                )
                if temporal_prior_enabled
                else self.custom_conf_thresh,
                float(
                    self._det_opt(
                        "custom_zone_rescue_min_conf",
                        self.custom_conf_thresh,
                    )
                )
                if zone_prior_enabled
                else self.custom_conf_thresh,
            )
        )
        bboxes: list[tuple[int, int, int, int]] = []
        confidences: list[float] = []
        class_ids: list[int] = []
        for gy in range(grid_h):
            for gx in range(grid_w):
                class_id = int(np.argmax(cls_prob[:, gy, gx]))
                base_score = float(obj[gy, gx] * cls_prob[class_id, gy, gx])
                if base_score < decode_thresh:
                    continue
                cx = ((gx + float(box[0, gy, gx])) / grid_w) * orig_w
                cy = ((gy + float(box[1, gy, gx])) / grid_h) * orig_h
                width = np.clip(box[2, gy, gx], 0.01, 1.0) * orig_w
                height = np.clip(box[3, gy, gx], 0.01, 1.0) * orig_h
                x1 = int(max(0, round(cx - width / 2.0)))
                y1 = int(max(0, round(cy - height / 2.0)))
                x2 = int(min(orig_w - 1, round(cx + width / 2.0)))
                y2 = int(min(orig_h - 1, round(cy + height / 2.0)))
                score = self._apply_temporal_prior_scoring(
                    bbox=(x1, y1, x2, y2),
                    base_score=base_score,
                    image_w=orig_w,
                    image_h=orig_h,
                    temporal_priors=temporal_priors,
                    zone_prior=zone_prior,
                )
                if score is None:
                    continue
                bboxes.append((x1, y1, x2, y2))
                confidences.append(score)
                class_ids.append(class_id)
        return self._nms(bboxes, confidences, class_ids, self.iou_thresh)

    def _decode_custom_predictions_dfl(
        self,
        pred: np.ndarray,
        orig_w: int,
        orig_h: int,
        temporal_priors: list[DetectionTemporalPrior] | None = None,
        zone_prior: np.ndarray | None = None,
    ) -> tuple[list[tuple[int, int, int, int]], list[float], list[int]]:
        _, grid_h, grid_w = pred.shape
        obj = self._sigmoid_np(pred[0])
        box_channels = 4 * (self.reg_max + 1)
        box_logits = pred[1 : 1 + box_channels].reshape(4, self.reg_max + 1, grid_h, grid_w)
        box_prob = self._softmax_np(box_logits, axis=1)
        bins = np.arange(self.reg_max + 1, dtype=np.float32).reshape(1, self.reg_max + 1, 1, 1)
        box_dist = (box_prob * bins).sum(axis=1) / float(self.reg_max)
        cls_logits = pred[1 + box_channels :]
        cls_prob = self._sigmoid_np(cls_logits)
        temporal_prior_enabled = bool(
            self._det_opt("custom_temporal_prior_enabled", False)
        ) and bool(temporal_priors)
        zone_prior_enabled = bool(
            self._det_opt("custom_zone_rescue_enabled", False)
        ) and zone_prior is not None
        decode_thresh = (
            min(
                self.custom_conf_thresh,
                float(
                    self._det_opt(
                        "custom_temporal_prior_min_conf",
                        self.custom_conf_thresh,
                    )
                )
                if temporal_prior_enabled
                else self.custom_conf_thresh,
                float(
                    self._det_opt(
                        "custom_zone_rescue_min_conf",
                        self.custom_conf_thresh,
                    )
                )
                if zone_prior_enabled
                else self.custom_conf_thresh,
            )
        )

        bboxes: list[tuple[int, int, int, int]] = []
        confidences: list[float] = []
        class_ids: list[int] = []
        for gy in range(grid_h):
            for gx in range(grid_w):
                class_id = int(np.argmax(cls_prob[:, gy, gx]))
                base_score = float(obj[gy, gx] * cls_prob[class_id, gy, gx])
                if base_score < decode_thresh:
                    continue
                location_quality = self._compute_dfl_localization_quality(box_prob[:, :, gy, gx])
                score = self._apply_custom_quality_ranking(base_score, location_quality)
                anchor_x = (gx + 0.5) / grid_w
                anchor_y = (gy + 0.5) / grid_h
                left = float(np.clip(box_dist[0, gy, gx], 0.0, 1.0))
                top = float(np.clip(box_dist[1, gy, gx], 0.0, 1.0))
                right = float(np.clip(box_dist[2, gy, gx], 0.0, 1.0))
                bottom = float(np.clip(box_dist[3, gy, gx], 0.0, 1.0))
                x1 = int(max(0, round((anchor_x - left) * orig_w)))
                y1 = int(max(0, round((anchor_y - top) * orig_h)))
                x2 = int(min(orig_w - 1, round((anchor_x + right) * orig_w)))
                y2 = int(min(orig_h - 1, round((anchor_y + bottom) * orig_h)))
                score = self._apply_temporal_prior_scoring(
                    bbox=(x1, y1, x2, y2),
                    base_score=score,
                    image_w=orig_w,
                    image_h=orig_h,
                    temporal_priors=temporal_priors,
                    zone_prior=zone_prior,
                )
                if score is None:
                    continue
                bboxes.append((x1, y1, x2, y2))
                confidences.append(score)
                class_ids.append(class_id)
        return self._nms(bboxes, confidences, class_ids, self.iou_thresh)

    def _apply_temporal_prior_scoring(
        self,
        bbox: tuple[int, int, int, int],
        base_score: float,
        image_w: int,
        image_h: int,
        temporal_priors: list[DetectionTemporalPrior] | None,
        zone_prior: np.ndarray | None = None,
    ) -> float | None:
        temporal_affinity = 0.0
        if bool(self._det_opt("custom_temporal_prior_enabled", False)) and temporal_priors:
            temporal_affinity = self._temporal_prior_affinity(
                bbox=bbox,
                temporal_priors=temporal_priors,
                image_w=image_w,
                image_h=image_h,
            )
        zone_affinity = 0.0
        if bool(self._det_opt("custom_zone_rescue_enabled", False)) and zone_prior is not None:
            zone_affinity = self._zone_prior_affinity(bbox=bbox, zone_prior=zone_prior)
        effective_thresh = self.custom_conf_thresh
        effective_thresh -= float(
            self._det_opt("custom_temporal_prior_thresh_relax", 0.0)
        ) * temporal_affinity
        effective_thresh -= float(
            self._det_opt("custom_zone_rescue_thresh_relax", 0.0)
        ) * zone_affinity
        effective_thresh = max(0.0, effective_thresh)
        if base_score < effective_thresh:
            return None
        adjusted_score = base_score
        adjusted_score *= 1.0 + float(
            self._det_opt("custom_temporal_prior_score_boost", 0.0)
        ) * temporal_affinity
        adjusted_score *= 1.0 + float(
            self._det_opt("custom_zone_rescue_score_boost", 0.0)
        ) * zone_affinity
        return float(np.clip(adjusted_score, 0.0, 1.0))

    def _temporal_prior_affinity(
        self,
        bbox: tuple[int, int, int, int],
        temporal_priors: list[DetectionTemporalPrior],
        image_w: int,
        image_h: int,
    ) -> float:
        if not temporal_priors:
            return 0.0
        iou_weight = float(
            self._det_opt("custom_temporal_prior_iou_weight", 0.65)
        )
        center_weight = float(
            self._det_opt("custom_temporal_prior_center_weight", 0.35)
        )
        center_gate_ratio = float(
            self._det_opt("custom_temporal_prior_center_gate_ratio", 0.18)
        )
        weight_sum = max(1e-6, iou_weight + center_weight)
        best_affinity = 0.0
        bbox_center = self._bbox_bottom_center(bbox)
        for prior in temporal_priors:
            prior_iou = self._bbox_iou(bbox, prior.bbox)
            center_score = self._center_proximity_score(
                bbox_center,
                self._bbox_bottom_center(prior.bbox),
                image_w=image_w,
                image_h=image_h,
                gate_ratio=center_gate_ratio,
            )
            affinity = (
                iou_weight * prior_iou
                + center_weight * center_score
            ) / weight_sum
            affinity *= float(np.clip(prior.confidence, 0.0, 1.0))
            best_affinity = max(best_affinity, float(np.clip(affinity, 0.0, 1.0)))
        return best_affinity

    def _zone_prior_affinity(
        self,
        bbox: tuple[int, int, int, int],
        zone_prior: np.ndarray,
    ) -> float:
        x1, y1, x2, y2 = bbox
        if x2 <= x1 or y2 <= y1:
            return 0.0
        bbox_polygon = np.array(
            [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
            dtype=np.float32,
        )
        try:
            inter_area, _ = cv2.intersectConvexConvex(
                bbox_polygon,
                zone_prior.astype(np.float32),
            )
        except cv2.error:
            inter_area = 0.0
        bbox_area = max(1.0, float((x2 - x1) * (y2 - y1)))
        return float(np.clip(inter_area / bbox_area, 0.0, 1.0))

    @staticmethod
    def _bbox_bottom_center(bbox: tuple[int, int, int, int]) -> tuple[float, float]:
        x1, _, x2, y2 = bbox
        return ((x1 + x2) * 0.5, float(y2))

    @staticmethod
    def _center_proximity_score(
        pred_center: tuple[float, float],
        det_center: tuple[float, float],
        image_w: int,
        image_h: int,
        gate_ratio: float,
    ) -> float:
        image_diag = max(1.0, float(np.hypot(image_w, image_h)))
        gate = max(1e-6, gate_ratio)
        distance = float(np.hypot(pred_center[0] - det_center[0], pred_center[1] - det_center[1]))
        normalized = (distance / image_diag) / gate
        return float(np.clip(1.0 - normalized, 0.0, 1.0))

    def _apply_custom_side_prior(
        self,
        bboxes: list[tuple[int, int, int, int]],
        confidences: list[float],
        class_ids: list[int],
        image_w: int,
        camera_side: str | None,
    ) -> tuple[list[tuple[int, int, int, int]], list[float], list[int]]:
        if self.backend != "custom":
            return bboxes, confidences, class_ids
        if not bool(self.det_cfg.get("custom_side_prior_enabled", False)):
            return bboxes, confidences, class_ids
        if camera_side not in ("left", "right"):
            return bboxes, confidences, class_ids

        mode = str(self.det_cfg.get("custom_side_prior_mode", "soft")).lower()
        left_max = float(self.det_cfg.get("custom_left_edge_max_center", 0.1))
        right_min = float(self.det_cfg.get("custom_right_edge_min_center", 0.95))
        max_keep = int(self.det_cfg.get("custom_max_detections_per_frame", 2))
        if mode == "soft":
            return self._apply_soft_side_prior(
                bboxes,
                confidences,
                class_ids,
                image_w=image_w,
                camera_side=camera_side,
                max_keep=max_keep,
            )

        kept: list[tuple[float, int, tuple[int, int, int, int]]] = []
        for conf, class_id, bbox in sorted(zip(confidences, class_ids, bboxes), reverse=True):
            x1, _, x2, _ = bbox
            cx_norm = ((x1 + x2) * 0.5) / max(1, image_w)
            if camera_side == "left" and cx_norm > left_max:
                continue
            if camera_side == "right" and cx_norm < right_min:
                continue
            kept.append((conf, class_id, bbox))
            if len(kept) >= max_keep:
                break

        if not kept:
            return self._limit_detections_by_confidence(
                bboxes,
                confidences,
                class_ids,
                max_keep=max_keep,
            )
        return (
            [bbox for _, _, bbox in kept],
            [conf for conf, _, _ in kept],
            [class_id for _, class_id, _ in kept],
        )

    def _apply_soft_side_prior(
        self,
        bboxes: list[tuple[int, int, int, int]],
        confidences: list[float],
        class_ids: list[int],
        image_w: int,
        camera_side: str,
        max_keep: int,
    ) -> tuple[list[tuple[int, int, int, int]], list[float], list[int]]:
        strength = float(self.det_cfg.get("custom_side_prior_strength", 0.25))
        ranked: list[tuple[float, float, int, tuple[int, int, int, int]]] = []
        for conf, class_id, bbox in zip(confidences, class_ids, bboxes):
            x1, _, x2, _ = bbox
            cx_norm = ((x1 + x2) * 0.5) / max(1, image_w)
            side_affinity = max(0.0, 1.0 - cx_norm) if camera_side == "left" else max(0.0, cx_norm)
            adjusted_conf = conf * (1.0 + strength * side_affinity)
            ranked.append((adjusted_conf, conf, class_id, bbox))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if max_keep > 0:
            ranked = ranked[:max_keep]
        return (
            [bbox for _, _, _, bbox in ranked],
            [conf for _, conf, _, _ in ranked],
            [class_id for _, _, class_id, _ in ranked],
        )

    @staticmethod
    def _limit_detections_by_confidence(
        bboxes: list[tuple[int, int, int, int]],
        confidences: list[float],
        class_ids: list[int],
        max_keep: int,
    ) -> tuple[list[tuple[int, int, int, int]], list[float], list[int]]:
        ranked = sorted(
            zip(confidences, class_ids, bboxes),
            key=lambda item: item[0],
            reverse=True,
        )
        if max_keep > 0:
            ranked = ranked[:max_keep]
        return (
            [bbox for _, _, bbox in ranked],
            [conf for conf, _, _ in ranked],
            [class_id for _, class_id, _ in ranked],
        )

    def _compute_dfl_localization_quality(self, side_probabilities: np.ndarray) -> float:
        if side_probabilities.ndim != 2 or side_probabilities.shape[0] != 4:
            return 1.0
        clipped = np.clip(side_probabilities, 1e-9, 1.0)
        peak_quality = float(np.mean(np.max(clipped, axis=1)))
        if clipped.shape[1] <= 1:
            return float(np.clip(peak_quality, 0.0, 1.0))
        entropy = -np.sum(clipped * np.log(clipped), axis=1)
        entropy_norm = entropy / np.log(clipped.shape[1])
        entropy_quality = float(np.clip(1.0 - np.mean(entropy_norm), 0.0, 1.0))
        return float(np.clip(0.5 * (peak_quality + entropy_quality), 0.0, 1.0))

    def _apply_custom_quality_ranking(
        self,
        base_score: float,
        location_quality: float,
    ) -> float:
        if not self.custom_quality_ranking_enabled or self.box_format != "ltrb_dfl":
            return base_score
        strength = float(np.clip(self.custom_quality_ranking_strength, 0.0, 1.0))
        floor = float(np.clip(self.custom_quality_ranking_floor, 0.0, 1.0))
        quality_factor = floor + (1.0 - floor) * float(np.clip(location_quality, 0.0, 1.0))
        adjusted_score = base_score * ((1.0 - strength) + strength * quality_factor)
        return float(np.clip(adjusted_score, 0.0, 1.0))

    @staticmethod
    def _sigmoid_np(values: np.ndarray) -> np.ndarray:
        positive = values >= 0
        neg_exp = np.exp(np.where(positive, -values, values))
        return np.where(positive, 1.0 / (1.0 + neg_exp), neg_exp / (1.0 + neg_exp))

    @staticmethod
    def _softmax_np(values: np.ndarray, axis: int) -> np.ndarray:
        shifted = values - np.max(values, axis=axis, keepdims=True)
        exp = np.exp(shifted)
        return exp / np.sum(exp, axis=axis, keepdims=True)

    @staticmethod
    def _nms(
        bboxes: list[tuple[int, int, int, int]],
        confidences: list[float],
        class_ids: list[int],
        iou_thresh: float,
    ) -> tuple[list[tuple[int, int, int, int]], list[float], list[int]]:
        if not bboxes:
            return [], [], []

        kept_boxes: list[tuple[int, int, int, int]] = []
        kept_scores: list[float] = []
        kept_classes: list[int] = []
        unique_classes = sorted(set(class_ids))
        for class_id in unique_classes:
            indices = [idx for idx, current in enumerate(class_ids) if current == class_id]
            indices.sort(key=lambda idx: confidences[idx], reverse=True)
            while indices:
                current = indices.pop(0)
                kept_boxes.append(bboxes[current])
                kept_scores.append(confidences[current])
                kept_classes.append(class_id)
                indices = [
                    idx for idx in indices
                    if MultitaskDetector._bbox_iou(bboxes[current], bboxes[idx]) < iou_thresh
                ]
        return kept_boxes, kept_scores, kept_classes

    @staticmethod
    def _bbox_iou(box_a: tuple[int, int, int, int], box_b: tuple[int, int, int, int]) -> float:
        ax1, ay1, ax2, ay2 = box_a
        bx1, by1, bx2, by2 = box_b
        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)
        inter = max(0, inter_x2 - inter_x1) * max(0, inter_y2 - inter_y1)
        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
        union = area_a + area_b - inter
        if union <= 0:
            return 0.0
        return inter / union

    @staticmethod
    def _to_track_inputs(
        bboxes: list[tuple[int, int, int, int]],
        confidences: list[float],
        class_ids: list[int],
    ) -> np.ndarray:
        if not bboxes:
            return np.zeros((0, 6), dtype=np.float32)
        rows = [
            [float(x1), float(y1), float(x2), float(y2), float(conf), float(class_id)]
            for (x1, y1, x2, y2), conf, class_id in zip(bboxes, confidences, class_ids)
        ]
        return np.asarray(rows, dtype=np.float32)

    def _map_ultralytics_class(self, class_id: int) -> int | None:
        names = getattr(self.yolo_model, "names", None)
        if isinstance(names, dict):
            name_count = len(names)
        elif isinstance(names, list):
            name_count = len(names)
        else:
            name_count = 0

        if name_count == len(self.class_names) and 0 <= class_id < len(self.class_names):
            return class_id

        if len(self.class_names) == 1:
            single_class_name = str(self.class_names[0]).strip().lower()
            single_class_coco_map = {
                "person": 0,
                "bicycle": 1,
                "car": 2,
                "motorcycle": 3,
                "bus": 5,
                "truck": 7,
            }
            expected_raw_class = single_class_coco_map.get(single_class_name)
            if expected_raw_class is None:
                return None
            return 0 if class_id == expected_raw_class else None

        mapping = {0: 0, 1: 1, 2: 2, 3: 3, 5: 4, 7: 5}
        return mapping.get(class_id)

    def _ultralytics_class_name(self, class_id: int) -> str:
        mapped = self._map_ultralytics_class(class_id)
        if mapped is None:
            return str(class_id)
        return self.class_names[mapped]
