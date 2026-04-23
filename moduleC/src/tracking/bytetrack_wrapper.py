from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment

from src.detection.multitask_detector import DetectionResult, DetectionTemporalPrior
from src.tracking.ego_motion_compensator import EgoMotionCompensator
from src.zones.zone_model import BlindSpotZone


@dataclass
class TrackedObject:
    track_id: int
    bbox: tuple[int, int, int, int]
    confidence: float
    class_id: int
    class_name: str
    center: tuple[float, float]
    area: float


@dataclass
class _TrackState:
    track_id: int
    bbox: tuple[int, int, int, int]
    confidence: float
    class_id: int
    class_name: str
    age: int = 0
    missing: int = 0
    last_zone_overlap: float = 0.0
    zone_age: int = 0
    last_risk_score: float = 0.0
    risk_age: int = 0

    def to_tracked_object(self) -> TrackedObject:
        x1, y1, x2, y2 = self.bbox
        return TrackedObject(
            track_id=self.track_id,
            bbox=self.bbox,
            confidence=self.confidence,
            class_id=self.class_id,
            class_name=self.class_name,
            center=((x1 + x2) / 2.0, float(y2)),
            area=float(max(0, x2 - x1) * max(0, y2 - y1)),
        )


class _DetectionBatch:
    def __init__(self, xywh: np.ndarray, conf: np.ndarray, cls: np.ndarray):
        self.xywh = xywh.astype(np.float32)
        self.conf = conf.astype(np.float32)
        self.cls = cls.astype(np.float32)

    def __len__(self) -> int:
        return len(self.conf)

    def __getitem__(self, item: Any) -> "_DetectionBatch":
        return _DetectionBatch(self.xywh[item], self.conf[item], self.cls[item])


class CameraTracker:
    def __init__(self, camera_side: str, config: dict[str, Any]):
        self.side = camera_side
        self.config = config["tracking"]
        self._next_id = 1
        self._tracks: dict[int, _TrackState] = {}
        self._use_motion_aware_byte = bool(
            self.config.get("enable_imu_compensated_byte", True)
        )
        self._prefer_ultralytics_byte_backend = bool(
            self.config.get("prefer_ultralytics_byte_backend", True)
        )
        self._use_zone_aware_byte = bool(
            self.config.get("enable_zone_aware_byte", True)
        )
        self._use_risk_aware_byte = bool(
            self.config.get("enable_risk_aware_byte", True)
        )
        self._byte_tracker = self._build_byte_tracker()

    def build_detection_priors(
        self,
        frame_shape: tuple[int, ...],
        ego_gz: float,
        dt: float,
        *,
        min_track_age: int = 2,
        max_missing: int = 1,
        min_confidence: float = 0.35,
        max_priors: int = 4,
    ) -> list[DetectionTemporalPrior]:
        priors: list[tuple[float, DetectionTemporalPrior]] = []
        for state in self._tracks.values():
            if state.age < min_track_age:
                continue
            if state.missing > max_missing:
                continue
            if state.confidence < min_confidence:
                continue
            prior_bbox = self._predict_track_bbox(
                state,
                frame_shape=frame_shape,
                ego_gz=ego_gz,
                dt=dt,
            )
            prior_conf = float(
                np.clip(
                    0.6 * state.confidence
                    + 0.2 * min(1.0, state.age / 6.0)
                    + 0.1 * float(state.zone_age > 0)
                    + 0.1 * float(state.risk_age > 0),
                    0.0,
                    1.0,
                )
            )
            priors.append((prior_conf, DetectionTemporalPrior(prior_bbox, prior_conf)))
        priors.sort(key=lambda item: item[0], reverse=True)
        if max_priors > 0:
            priors = priors[:max_priors]
        return [prior for _, prior in priors]

    def update(
        self,
        frame: np.ndarray,
        detector: Any | None = None,
        detections: DetectionResult | None = None,
        zone: BlindSpotZone | None = None,
        ego_gz: float = 0.0,
        dt: float = 0.05,
    ) -> tuple[list[TrackedObject], np.ndarray]:
        if detections is None:
            if detector is None:
                raise ValueError("Either detector or detections must be provided.")
            detections = detector.detect(frame)

        if (
            not self._use_motion_aware_byte
            and detector is not None
            and hasattr(detector, "track_with_ultralytics")
        ):
            try:
                tracked, zone_mask = detector.track_with_ultralytics(frame, self.side)
                if tracked:
                    self._tracks = {
                        item.track_id: _TrackState(
                            track_id=item.track_id,
                            bbox=item.bbox,
                            confidence=item.confidence,
                            class_id=item.class_id,
                            class_name=item.class_name,
                            age=1,
                            missing=0,
                        )
                        for item in tracked
                    }
                    self._next_id = max(self._next_id, max(self._tracks) + 1)
                    return tracked, zone_mask
            except Exception:
                pass

        if (
            not self._use_motion_aware_byte
            and self._prefer_ultralytics_byte_backend
            and self._byte_tracker is not None
        ):
            tracked = self._associate_bytetrack(detections)
            if tracked:
                return tracked, detections.zone_mask

        tracked = self._associate(
            detections,
            frame_shape=frame.shape,
            zone=zone,
            ego_gz=ego_gz,
            dt=dt,
        )
        return tracked, detections.zone_mask

    def _build_byte_tracker(self) -> Any | None:
        try:
            from ultralytics.trackers.byte_tracker import BYTETracker
        except Exception:
            return None

        args = SimpleNamespace(
            track_high_thresh=float(self.config["track_thresh"]),
            track_low_thresh=0.1,
            new_track_thresh=max(float(self.config["track_thresh"]), 0.6),
            track_buffer=int(self.config["track_buffer"]),
            match_thresh=float(self.config["match_thresh"]),
            fuse_score=True,
        )
        return BYTETracker(args, frame_rate=int(self.config["frame_rate"]))

    def _associate_bytetrack(self, detections: DetectionResult) -> list[TrackedObject]:
        if self._byte_tracker is None:
            return []
        batch = self._to_detection_batch(detections)
        if len(batch) == 0:
            tracks = self._byte_tracker.update(batch)
        else:
            tracks = self._byte_tracker.update(batch)
        if tracks is None or len(tracks) == 0:
            return []
        results: list[TrackedObject] = []
        for row in tracks:
            x1, y1, x2, y2, track_id, score, class_id, det_idx = row.tolist()
            class_id = int(class_id)
            bbox = (int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2)))
            results.append(
                TrackedObject(
                    track_id=int(track_id),
                    bbox=bbox,
                    confidence=float(score),
                    class_id=class_id,
                    class_name=self._class_name(class_id),
                    center=((bbox[0] + bbox[2]) / 2.0, float(bbox[3])),
                    area=float(max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])),
                )
            )
        results.sort(key=lambda item: item.track_id)
        return results

    @staticmethod
    def _to_detection_batch(detections: DetectionResult) -> _DetectionBatch:
        if not detections.bboxes:
            empty = np.zeros((0, 4), dtype=np.float32)
            return _DetectionBatch(empty, np.zeros((0,), dtype=np.float32), np.zeros((0,), dtype=np.float32))
        xywh_rows = []
        for x1, y1, x2, y2 in detections.bboxes:
            w = max(0, x2 - x1)
            h = max(0, y2 - y1)
            xywh_rows.append([x1 + w / 2.0, y1 + h / 2.0, w, h])
        return _DetectionBatch(
            np.asarray(xywh_rows, dtype=np.float32),
            np.asarray(detections.confidences, dtype=np.float32),
            np.asarray(detections.class_ids, dtype=np.float32),
        )

    def _associate(
        self,
        detections: DetectionResult,
        frame_shape: tuple[int, ...],
        zone: BlindSpotZone | None,
        ego_gz: float,
        dt: float,
    ) -> list[TrackedObject]:
        high_thresh = float(self.config["track_thresh"])
        low_thresh = float(self.config.get("track_low_thresh", 0.1))
        new_track_thresh = float(
            self.config.get("new_track_thresh", max(high_thresh, 0.6))
        )
        high_match_thresh = float(
            self.config.get(
                "match_score_thresh",
                max(0.65, float(self.config.get("match_thresh", 0.8)) - 0.05),
            )
        )
        low_match_thresh = float(
            self.config.get("low_match_thresh", max(0.4, high_match_thresh - 0.1))
        )

        all_dets = self._build_detection_states(
            detections,
            low_thresh=low_thresh,
            zone=zone,
        )
        high_dets = [det for det in all_dets if det.confidence >= high_thresh]
        low_dets = [det for det in all_dets if det.confidence < high_thresh]

        existing_ids = list(self._tracks.keys())
        predicted_boxes = {
            track_id: self._predict_track_bbox(
                self._tracks[track_id],
                frame_shape=frame_shape,
                ego_gz=ego_gz,
                dt=dt,
            )
            for track_id in existing_ids
        }

        matched_tracks: set[int] = set()
        matched_high_dets: set[int] = set()
        if existing_ids and high_dets:
            matched_tracks, matched_high_dets = self._match_track_states(
                track_ids=existing_ids,
                detections=high_dets,
                predicted_boxes=predicted_boxes,
                frame_shape=frame_shape,
                zone=zone,
                min_score=high_match_thresh,
            )

        unmatched_track_ids = [track_id for track_id in existing_ids if track_id not in matched_tracks]
        matched_low_tracks: set[int] = set()
        matched_low_dets: set[int] = set()
        if unmatched_track_ids and low_dets:
            matched_low_tracks, matched_low_dets = self._match_track_states(
                track_ids=unmatched_track_ids,
                detections=low_dets,
                predicted_boxes=predicted_boxes,
                frame_shape=frame_shape,
                zone=zone,
                min_score=low_match_thresh,
            )
        matched_tracks |= matched_low_tracks

        for idx, det in enumerate(high_dets):
            if idx in matched_high_dets:
                continue
            if det.confidence < new_track_thresh:
                continue
            self._start_track(det, zone=zone)

        for track_id in existing_ids:
            if track_id in matched_tracks:
                continue
            track = self._tracks[track_id]
            track.missing += 1
            track.zone_age = max(0, track.zone_age - 1)

        for track_id in list(self._tracks):
            if self._tracks[track_id].missing > self._effective_track_buffer(
                self._tracks[track_id]
            ):
                del self._tracks[track_id]

        active = [
            state.to_tracked_object()
            for state in self._tracks.values()
            if state.missing == 0
        ]
        active.sort(key=lambda item: item.track_id)
        return active

    def _build_detection_states(
        self,
        detections: DetectionResult,
        low_thresh: float,
        zone: BlindSpotZone | None,
    ) -> list[_TrackState]:
        det_states: list[_TrackState] = []
        for bbox, conf, class_id in zip(
            detections.bboxes,
            detections.confidences,
            detections.class_ids,
        ):
            if float(conf) < low_thresh:
                continue
            det_bbox = tuple(map(int, bbox))
            det_states.append(
                _TrackState(
                    track_id=-1,
                    bbox=det_bbox,
                    confidence=float(conf),
                    class_id=int(class_id),
                    class_name=self._class_name(int(class_id)),
                    last_zone_overlap=self._zone_overlap(det_bbox, zone),
                    zone_age=0,
                )
            )
        return det_states

    def _match_track_states(
        self,
        track_ids: list[int],
        detections: list[_TrackState],
        predicted_boxes: dict[int, tuple[int, int, int, int]],
        frame_shape: tuple[int, ...],
        zone: BlindSpotZone | None,
        min_score: float,
    ) -> tuple[set[int], set[int]]:
        if not track_ids or not detections:
            return set(), set()

        score_matrix = np.zeros((len(track_ids), len(detections)), dtype=np.float32)
        for row, track_id in enumerate(track_ids):
            track = self._tracks[track_id]
            pred_bbox = predicted_boxes.get(track_id, track.bbox)
            for col, det in enumerate(detections):
                score_matrix[row, col] = self._association_score(
                    track=track,
                    predicted_bbox=pred_bbox,
                    detection=det,
                    frame_shape=frame_shape,
                    zone=zone,
                )

        rows, cols = linear_sum_assignment(1.0 - score_matrix)
        matched_tracks: set[int] = set()
        matched_dets: set[int] = set()
        for row, col in zip(rows, cols):
            score = float(score_matrix[row, col])
            track_id = track_ids[row]
            det = detections[col]
            track = self._tracks[track_id]
            pred_bbox = predicted_boxes.get(track_id, track.bbox)
            required_score = self._min_match_score_for_pair(
                track=track,
                predicted_bbox=pred_bbox,
                detection=det,
                zone=zone,
                base_min_score=min_score,
            )
            if score < required_score:
                continue
            matched_tracks.add(track_id)
            matched_dets.add(col)
            self._update_track(track_id, det, zone=zone)
        return matched_tracks, matched_dets

    def _update_track(
        self,
        track_id: int,
        detection: _TrackState,
        zone: BlindSpotZone | None,
    ) -> None:
        track = self._tracks[track_id]
        track.bbox = detection.bbox
        track.confidence = detection.confidence
        track.class_id = detection.class_id
        track.class_name = detection.class_name
        track.age += 1
        track.missing = 0
        track.last_zone_overlap = self._zone_overlap(detection.bbox, zone)
        zone_overlap_thresh = float(self.config.get("zone_overlap_thresh", 0.05))
        zone_memory_frames = int(self.config.get("zone_memory_frames", 8))
        if track.last_zone_overlap >= zone_overlap_thresh:
            track.zone_age = zone_memory_frames
        else:
            track.zone_age = max(0, track.zone_age - 1)

    def _predict_track_bbox(
        self,
        track: _TrackState,
        frame_shape: tuple[int, ...],
        ego_gz: float,
        dt: float,
    ) -> tuple[int, int, int, int]:
        if not self._use_motion_aware_byte:
            return track.bbox
        img_h, img_w = frame_shape[:2]
        compensator = EgoMotionCompensator(img_w, img_h)
        x1, y1, x2, y2 = track.bbox
        width = max(1.0, float(x2 - x1))
        height = max(1.0, float(y2 - y1))
        compensated = compensator.compensate_center(
            track.track_id,
            ((x1 + x2) / 2.0, float(y2)),
            ego_gz,
            dt,
        ).compensated
        cx, cy = compensated
        pred_x1 = int(round(max(0.0, cx - width / 2.0)))
        pred_y2 = int(round(min(float(img_h - 1), cy)))
        pred_x2 = int(round(min(float(img_w - 1), cx + width / 2.0)))
        pred_y1 = int(round(max(0.0, pred_y2 - height)))
        return (pred_x1, pred_y1, pred_x2, pred_y2)

    def _association_score(
        self,
        track: _TrackState,
        predicted_bbox: tuple[int, int, int, int],
        detection: _TrackState,
        frame_shape: tuple[int, ...],
        zone: BlindSpotZone | None,
    ) -> float:
        if detection.class_id != track.class_id:
            return 0.0
        iou = self._iou(predicted_bbox, detection.bbox)
        risk_priority = self._risk_match_priority(
            track,
            predicted_bbox=predicted_bbox,
            detection_bbox=detection.bbox,
            zone=zone,
        )
        pred_center = self._bbox_bottom_center(predicted_bbox)
        det_center = self._bbox_bottom_center(detection.bbox)
        center_score = self._center_proximity_score(
            pred_center,
            det_center,
            frame_shape=frame_shape,
            gate_scale=1.0
            + (
                float(self.config.get("risk_center_gate_bonus", 0.35)) * risk_priority
                if self._use_risk_aware_byte
                else 0.0
            ),
        )
        zone_affinity = self._zone_affinity(track, predicted_bbox, detection.bbox, zone)
        risk_affinity = self._risk_affinity(
            track,
            predicted_bbox=predicted_bbox,
            detection_bbox=detection.bbox,
            zone=zone,
        )

        iou_weight = float(self.config.get("assoc_iou_weight", 0.55))
        center_weight = float(self.config.get("assoc_center_weight", 0.20))
        conf_weight = float(self.config.get("assoc_conf_weight", 0.10))
        zone_weight = (
            float(self.config.get("assoc_zone_weight", 0.15))
            if self._use_zone_aware_byte
            else 0.0
        )
        if self._use_risk_aware_byte and risk_priority > 0.0:
            center_weight += float(self.config.get("risk_center_weight_boost", 0.12)) * risk_priority
            zone_weight += float(self.config.get("risk_zone_weight_boost", 0.18)) * risk_priority
            conf_weight = max(
                0.02,
                conf_weight * (1.0 - float(self.config.get("risk_conf_suppression", 0.30)) * risk_priority),
            )
        weight_sum = max(1e-6, iou_weight + center_weight + conf_weight + zone_weight)
        score = (
            iou_weight * iou
            + center_weight * center_score
            + conf_weight * float(np.clip(detection.confidence, 0.0, 1.0))
            + zone_weight * zone_affinity
        ) / weight_sum
        if self._use_risk_aware_byte and risk_affinity > 0.0:
            risk_weight = float(self.config.get("assoc_risk_weight", 0.10))
            score += risk_weight * risk_affinity * (1.0 - score)
        return float(np.clip(score, 0.0, 1.0))

    def _center_proximity_score(
        self,
        pred_center: tuple[float, float],
        det_center: tuple[float, float],
        frame_shape: tuple[int, ...],
        gate_scale: float = 1.0,
    ) -> float:
        img_h, img_w = frame_shape[:2]
        image_diag = max(1.0, float(np.hypot(img_w, img_h)))
        gate_ratio = float(self.config.get("center_distance_gate_ratio", 0.18))
        gate = max(1e-6, gate_ratio * max(1e-3, gate_scale))
        distance = float(np.hypot(pred_center[0] - det_center[0], pred_center[1] - det_center[1]))
        normalized = (distance / image_diag) / gate
        return float(np.clip(1.0 - normalized, 0.0, 1.0))

    def _zone_affinity(
        self,
        track: _TrackState,
        predicted_bbox: tuple[int, int, int, int],
        detection_bbox: tuple[int, int, int, int],
        zone: BlindSpotZone | None,
    ) -> float:
        if not self._use_zone_aware_byte:
            return 0.0
        det_overlap = self._zone_overlap(detection_bbox, zone)
        pred_overlap = self._zone_overlap(predicted_bbox, zone)
        historical_overlap = track.last_zone_overlap if track.zone_age > 0 else 0.0
        return float(
            np.clip(
                max(det_overlap, pred_overlap, historical_overlap),
                0.0,
                1.0,
            )
        )

    def _effective_track_buffer(self, track: _TrackState) -> int:
        base_buffer = int(self.config["track_buffer"])
        if self._use_zone_aware_byte:
            zone_overlap_thresh = float(self.config.get("zone_overlap_thresh", 0.05))
            zone_buffer_bonus = int(self.config.get("zone_track_buffer_bonus", 12))
            if track.last_zone_overlap >= zone_overlap_thresh or track.zone_age > 0:
                base_buffer += zone_buffer_bonus
        if self._use_risk_aware_byte:
            risk_score_thresh = float(self.config.get("risk_score_thresh", 0.6))
            risk_buffer_bonus = int(self.config.get("risk_track_buffer_bonus", 10))
            if track.last_risk_score >= risk_score_thresh or track.risk_age > 0:
                base_buffer += risk_buffer_bonus
        return base_buffer

    def _start_track(self, detection: _TrackState, zone: BlindSpotZone | None) -> None:
        detection.track_id = self._next_id
        detection.age = 1
        detection.missing = 0
        detection.last_zone_overlap = self._zone_overlap(detection.bbox, zone)
        zone_overlap_thresh = float(self.config.get("zone_overlap_thresh", 0.05))
        detection.zone_age = (
            int(self.config.get("zone_memory_frames", 8))
            if detection.last_zone_overlap >= zone_overlap_thresh
            else 0
        )
        self._tracks[self._next_id] = detection
        self._next_id += 1

    @staticmethod
    def _bbox_bottom_center(bbox: tuple[int, int, int, int]) -> tuple[float, float]:
        x1, _, x2, y2 = bbox
        return ((x1 + x2) / 2.0, float(y2))

    def _zone_overlap(
        self,
        bbox: tuple[int, int, int, int],
        zone: BlindSpotZone | None,
    ) -> float:
        if zone is None:
            return 0.0
        x1, y1, x2, y2 = bbox
        if x2 <= x1 or y2 <= y1:
            return 0.0
        if zone.mask_px is not None:
            mask = np.asarray(zone.mask_px)
            if mask.ndim == 2 and mask.size > 0:
                x1_clip = max(0, min(mask.shape[1], int(np.floor(x1))))
                x2_clip = max(0, min(mask.shape[1], int(np.ceil(x2))))
                y1_clip = max(0, min(mask.shape[0], int(np.floor(y1))))
                y2_clip = max(0, min(mask.shape[0], int(np.ceil(y2))))
                if x2_clip <= x1_clip or y2_clip <= y1_clip:
                    return 0.0
                inter_area = float(np.count_nonzero(mask[y1_clip:y2_clip, x1_clip:x2_clip] > 0))
                bbox_area = max(1.0, float((x2 - x1) * (y2 - y1)))
                return float(np.clip(inter_area / bbox_area, 0.0, 1.0))
        bbox_polygon = np.array(
            [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
            dtype=np.float32,
        )
        zone_polygon = zone.polygon_px.astype(np.float32)
        try:
            inter_area, _ = cv2.intersectConvexConvex(bbox_polygon, zone_polygon)
        except cv2.error:
            inter_area = 0.0
        bbox_area = max(1.0, float((x2 - x1) * (y2 - y1)))
        return float(np.clip(inter_area / bbox_area, 0.0, 1.0))

    def _risk_affinity(
        self,
        track: _TrackState,
        predicted_bbox: tuple[int, int, int, int],
        detection_bbox: tuple[int, int, int, int],
        zone: BlindSpotZone | None,
    ) -> float:
        if not self._use_risk_aware_byte:
            return 0.0
        if track.risk_age <= 0:
            return 0.0
        zone_support = max(
            self._zone_overlap(predicted_bbox, zone),
            self._zone_overlap(detection_bbox, zone),
            track.last_zone_overlap if track.zone_age > 0 else 0.0,
        )
        return float(np.clip(track.last_risk_score * zone_support, 0.0, 1.0))

    def _risk_match_priority(
        self,
        track: _TrackState,
        predicted_bbox: tuple[int, int, int, int],
        detection_bbox: tuple[int, int, int, int],
        zone: BlindSpotZone | None,
    ) -> float:
        if not self._use_risk_aware_byte or track.risk_age <= 0:
            return 0.0
        zone_support = max(
            self._zone_overlap(predicted_bbox, zone),
            self._zone_overlap(detection_bbox, zone),
            track.last_zone_overlap if track.zone_age > 0 else 0.0,
        )
        if zone_support <= 0.0:
            return 0.0
        risk_base = float(np.clip(track.last_risk_score, 0.0, 1.0))
        missing_boost = (
            float(self.config.get("risk_missing_boost", 0.15))
            * min(1.0, track.missing / max(1.0, float(self.config.get("track_buffer", 30))))
        )
        priority = risk_base * (0.5 + 0.5 * zone_support) + missing_boost
        return float(np.clip(priority, 0.0, 1.0))

    def _min_match_score_for_pair(
        self,
        track: _TrackState,
        predicted_bbox: tuple[int, int, int, int],
        detection: _TrackState,
        zone: BlindSpotZone | None,
        base_min_score: float,
    ) -> float:
        if not self._use_risk_aware_byte:
            return base_min_score
        if detection.confidence < float(self.config.get("risk_match_min_conf", 0.15)):
            return base_min_score
        priority = self._risk_match_priority(
            track,
            predicted_bbox=predicted_bbox,
            detection_bbox=detection.bbox,
            zone=zone,
        )
        relax = float(self.config.get("risk_match_relax_max", 0.18)) * priority
        floor = float(self.config.get("risk_match_score_floor", 0.35))
        return max(floor, base_min_score - relax)

    def apply_risk_feedback(
        self,
        tracked: list[TrackedObject],
        predictor: Any,
        zone: BlindSpotZone,
        dt: float,
    ) -> None:
        risk_memory_frames = int(self.config.get("risk_memory_frames", 8))
        risk_score_thresh = float(self.config.get("risk_score_thresh", 0.6))
        active_ids = {obj.track_id for obj in tracked}
        for track_id, state in self._tracks.items():
            if track_id not in active_ids:
                state.risk_age = max(0, state.risk_age - 1)
                continue
            if not self._use_risk_aware_byte:
                state.last_risk_score = 0.0
                state.risk_age = 0
                continue
            try:
                risk_score = float(np.clip(predictor.predict_risk(track_id, zone, dt), 0.0, 1.0))
            except Exception:
                risk_score = 0.0
            state.last_risk_score = risk_score
            if risk_score >= risk_score_thresh:
                state.risk_age = risk_memory_frames
            else:
                state.risk_age = max(0, state.risk_age - 1)

    @staticmethod
    def _iou(box_a: tuple[int, int, int, int], box_b: tuple[int, int, int, int]) -> float:
        ax1, ay1, ax2, ay2 = box_a
        bx1, by1, bx2, by2 = box_b
        inter_x1 = max(ax1, bx1)
        inter_y1 = max(ay1, by1)
        inter_x2 = min(ax2, bx2)
        inter_y2 = min(ay2, by2)
        inter_w = max(0, inter_x2 - inter_x1)
        inter_h = max(0, inter_y2 - inter_y1)
        inter = inter_w * inter_h
        area_a = max(0, ax2 - ax1) * max(0, ay2 - ay1)
        area_b = max(0, bx2 - bx1) * max(0, by2 - by1)
        union = area_a + area_b - inter
        if union <= 0:
            return 0.0
        return inter / union

    @staticmethod
    def _class_name(class_id: int) -> str:
        names = ["person", "bicycle", "car", "motorcycle", "bus", "truck"]
        if 0 <= class_id < len(names):
            return names[class_id]
        return f"class_{class_id}"
