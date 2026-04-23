from __future__ import annotations

import time
from typing import Any

from src.alerting.risk_manager import AlertLevel, RiskManager
from src.detection.multitask_detector import MultitaskDetector
from src.prediction.imm_predictor import IMMPredictor, NullPredictor, PolynomialPredictor
from src.runtime.types import RuntimeFrameInput, RuntimeProcessingResult
from src.tracking.bytetrack_wrapper import CameraTracker
from src.tracking.ego_motion_compensator import EgoMotionCompensator
from src.zones.adaptive_zone import AdaptiveZoneController


def build_predictor(
    config: dict[str, Any], ablation: dict[str, Any]
) -> IMMPredictor | PolynomialPredictor | NullPredictor:
    if not ablation.get("use_prediction", True):
        return NullPredictor(config)
    if ablation.get("use_imm", True):
        return IMMPredictor(config)
    return PolynomialPredictor(config)


class BSDRuntimePipeline:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.ablation = dict(config.get("ablation", {}))
        self.zone_controller = AdaptiveZoneController(config)
        self.zone_stabilizers = {
            side: self.zone_controller.build_stabilizer() for side in ("left", "right")
        }
        self.detector = MultitaskDetector(
            model_path=str(
                config["detection"]["model_path"]
                if self.ablation.get("use_multitask_yolo", True)
                else config["detection"]["pretrained_path"]
            ),
            config=config,
            force_ultralytics=not self.ablation.get("use_multitask_yolo", True),
        )
        self.trackers = {side: CameraTracker(side, config) for side in ("left", "right")}
        self.predictors = {
            side: build_predictor(config, self.ablation) for side in ("left", "right")
        }
        self.risk_managers = {side: RiskManager(config) for side in ("left", "right")}
        self.compensators = {
            "left": EgoMotionCompensator(
                int(config["sensors"]["cam_bsd_left"]["width"]),
                int(config["sensors"]["cam_bsd_left"]["height"]),
            ),
            "right": EgoMotionCompensator(
                int(config["sensors"]["cam_bsd_right"]["width"]),
                int(config["sensors"]["cam_bsd_right"]["height"]),
            ),
        }
        self.dt = float(config["carla"]["fixed_delta_seconds"])
        self._start_time = time.perf_counter()
        self._processed_frames = 0

    @property
    def class_names(self) -> list[str]:
        detector_names = getattr(self.detector, "class_names", None)
        if detector_names:
            return list(detector_names)
        return list(self.config["training"].get("class_names", ["car"]))

    @property
    def detector_backend(self) -> str:
        return self.detector.backend

    @property
    def detector_device(self) -> str:
        return str(self.detector.device)

    def process_frame(self, frame_input: RuntimeFrameInput) -> RuntimeProcessingResult:
        frames = {
            "left": frame_input.left_frame,
            "right": frame_input.right_frame,
        }
        template_zones: dict[str, Any] = {}
        zones: dict[str, Any] = {}
        tracked_by_side: dict[str, list[Any]] = {}
        alerts: dict[str, list[Any]] = {}
        side_order = ("left", "right")

        for side in side_order:
            gz_for_zone = (
                frame_input.ego_state.imu_gz
                if self.ablation.get("use_imu_zone", True)
                else 0.0
            )
            if self.ablation.get("use_adaptive_zone", True):
                template_zones[side] = self.zone_controller.compute_zone(
                    frame_input.ego_state.speed_kmh,
                    frame_input.ego_state.left_signal,
                    frame_input.ego_state.right_signal,
                    gz_for_zone,
                    frames[side].shape[1],
                    frames[side].shape[0],
                    side,
                )
            else:
                template_zones[side] = self.zone_controller.compute_zone(
                    30.0,
                    False,
                    False,
                    0.0,
                    frames[side].shape[1],
                    frames[side].shape[0],
                    side,
                )

        temporal_priors = [
            self.trackers[side].build_detection_priors(
                frame_shape=frames[side].shape,
                ego_gz=frame_input.ego_state.imu_gz
                if self.ablation.get("use_imu_compensation", True)
                else 0.0,
                dt=self.dt,
                min_track_age=int(
                    self.config["detection"].get(
                        "custom_temporal_prior_min_track_age", 2
                    )
                ),
                max_missing=int(
                    self.config["detection"].get("custom_temporal_prior_max_missing", 1)
                ),
                min_confidence=float(
                    self.config["detection"].get(
                        "custom_temporal_prior_min_track_confidence", 0.35
                    )
                ),
                max_priors=int(
                    self.config["detection"].get("custom_temporal_prior_max_tracks", 4)
                ),
            )
            for side in side_order
        ]

        detection_batch = self.detector.detect_batch(
            [frames[side] for side in side_order],
            camera_sides=list(side_order),
            temporal_priors=temporal_priors,
            zone_priors=[template_zones[side].polygon_px for side in side_order],
        )
        detections_by_side = {
            side: detections for side, detections in zip(side_order, detection_batch)
        }

        for side in side_order:
            zone_mask, zone_source = self._select_zone_mask(
                side,
                frame_input,
                detections_by_side[side].zone_mask,
            )
            raw_zone = self.zone_controller.refine_zone_from_mask(
                zone_mask,
                fallback_zone=template_zones[side],
                source_name=zone_source,
            )
            raw_zone = self.zone_controller.apply_imu_bias_to_segmentation(
                raw_zone,
                template_zone=template_zones[side],
                image_w=frames[side].shape[1],
                image_h=frames[side].shape[0],
            )
            zones[side] = self.zone_stabilizers[side].stabilize(
                raw_zone,
                template_zone=template_zones[side],
                image_w=frames[side].shape[1],
                image_h=frames[side].shape[0],
            )

        for side in side_order:
            tracked, _zone_mask = self.trackers[side].update(
                frames[side],
                detector=self.detector,
                detections=detections_by_side[side],
                zone=zones[side],
                ego_gz=frame_input.ego_state.imu_gz
                if self.ablation.get("use_imu_compensation", True)
                else 0.0,
                dt=self.dt,
            )
            tracked_by_side[side] = tracked
            active_ids = {obj.track_id for obj in tracked}
            for obj in tracked:
                center = obj.center
                if self.ablation.get("use_imu_compensation", True):
                    center = self.compensators[side].compensate_center(
                        obj.track_id,
                        obj.center,
                        frame_input.ego_state.imu_gz,
                        self.dt,
                    ).compensated
                self.predictors[side].update(
                    obj.track_id,
                    center,
                    frame_input.timestamp,
                )
            self.trackers[side].apply_risk_feedback(
                tracked,
                self.predictors[side],
                zones[side],
                self.dt,
            )
            self.predictors[side].prune_lost_tracks(active_ids)
            alerts[side] = self.risk_managers[side].evaluate(
                tracked,
                zones[side],
                self.predictors[side],
                side,
                self.dt,
                frame_input.ego_state.speed_kmh,
            )
            alerts[side] = self.risk_managers[side].active_alerts()

        self._processed_frames += 1
        elapsed = max(1e-6, time.perf_counter() - self._start_time)
        frame_max_level = max(
            (
                event.level
                for side_alerts in alerts.values()
                for event in side_alerts
            ),
            default=AlertLevel.SAFE,
        )
        return RuntimeProcessingResult(
            frame_idx=self._processed_frames - 1,
            frame_input=frame_input,
            detector_backend=self.detector.backend,
            detector_device=str(self.detector.device),
            fps=self._processed_frames / elapsed,
            dt=self.dt,
            tracked_by_side=tracked_by_side,
            zones=zones,
            alerts=alerts,
            predictors=self.predictors,
            max_alert_level=frame_max_level,
        )

    @staticmethod
    def _select_zone_mask(
        side: str,
        frame_input: RuntimeFrameInput,
        detection_zone_mask: Any,
    ) -> tuple[Any, str]:
        external_mask = (
            frame_input.left_zone_mask if side == "left" else frame_input.right_zone_mask
        )
        if external_mask is not None:
            return external_mask, "external_mask"
        return detection_zone_mask, "segmentation"
