from __future__ import annotations

import base64
import json
from pathlib import Path

import cv2
import numpy as np

from demo.modulecd_bsd_demo.protocol import ModuleCDDemoMessageDecoder
from src.alerting.risk_manager import AlertEvent, AlertLevel, RiskManager
from src.runtime.modulecd_payload import build_modulecd_bsd_payload
from src.runtime.pipeline import BSDRuntimePipeline
from src.runtime.types import (
    RuntimeEgoState,
    RuntimeFrameInput,
    RuntimeProcessingResult,
)
from src.tracking.bytetrack_wrapper import TrackedObject
from src.zones.adaptive_zone import AdaptiveZoneController
from src.zones.zone_model import BlindSpotZone


def _demo_config() -> dict:
    return {
        "demo": {
            "zmq": {"input_topic": "Frame"},
            "sensors": {
                "left_camera_sensor_id": "left_camera",
                "right_camera_sensor_id": "right_camera",
                "top_camera_sensor_id": "top_camera",
                "left_zone_mask_sensor_id": "adjacent_lane_mask_left",
                "right_zone_mask_sensor_id": "adjacent_lane_mask_right",
                "imu_sensor_id": "imu",
                "vehicle_state_id": "ego",
            },
            "defaults": {
                "speed_kmh": 30.0,
                "left_signal": False,
                "right_signal": False,
                "imu_gz": 0.0,
                "imu_ax": 0.0,
            },
        },
        "zones": {
            "speed_scale": {
                "breakpoints": [0, 30, 60, 80, 120],
                "scales": [0.70, 1.00, 1.25, 1.50, 1.80],
            },
            "signal_expansion": {"value": 0.25},
            "imu_expansion": {"gz_thresh": 0.05, "gz_max": 0.5, "max_value": 0.40},
            "segmentation": {
                "enabled": True,
                "threshold": 0.50,
                "min_area_ratio": 0.01,
                "max_area_ratio": 0.60,
                "min_bottom_y_ratio": 0.85,
                "side_split_x": 0.50,
                "open_kernel": 3,
                "close_kernel": 5,
                "edge_quantile": 0.08,
                "top_quantile": 0.10,
                "band_height_ratio": 0.12,
            },
            "external_mask": {
                "polygon_points": 128,
                "simplify_epsilon_ratio": 0.003,
                "min_length_ratio": 0.30,
                "length_gain": 1.0,
                "max_length_ratio": 0.16,
            },
            "stability": {
                "enabled": True,
                "segmentation_alpha": 0.78,
                "template_alpha": 0.04,
                "hold_frames": 1,
            },
            "imu_segmentation_bias": {
                "enabled": True,
                "top_y_strength": 0.22,
                "outer_edge_strength": 0.28,
                "max_top_y_ratio": 0.06,
                "max_outer_edge_ratio": 0.08,
            },
            "left": {
                "center_x": 0.24,
                "top_y_base": 0.52,
                "bot_half_w_base": 0.22,
                "top_half_w_base": 0.09,
            },
            "right": {
                "center_x": 0.76,
                "top_y_base": 0.52,
                "bot_half_w_base": 0.22,
                "top_half_w_base": 0.09,
            },
        },
        "alerting": {
            "warning_risk_thresh": 0.6,
            "danger_hysteresis": 3,
            "min_pred_confidence": 0.3,
            "min_object_confidence": 0.5,
            "warning_hysteresis": 2,
            "warning_min_track_age": 2,
            "danger_min_track_age": 2,
            "warning_repeat_interval_frames": 6,
            "danger_repeat_interval_frames": 4,
            "stationary_single_alert_enabled": True,
            "stationary_speed_threshold_kmh": 1.0,
            "warning_display_hold_frames": 3,
            "danger_display_hold_frames": 4,
            "display_repeat_extend_frames": 2,
            "display_max_hold_frames": 8,
        },
    }


def _jpg_b64(color: tuple[int, int, int] = (10, 30, 200)) -> str:
    image = np.full((32, 48, 3), color, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return base64.b64encode(encoded.tobytes()).decode("utf-8")


def _frame_payload(image_b64: str) -> dict[str, object]:
    return {"payload": {"Image": {"format": "jpeg", "data": image_b64}}}


def _mask_payload(active_slice: tuple[slice, slice]) -> dict[str, object]:
    mask = np.zeros((180, 320), dtype=np.uint8)
    mask[active_slice] = 255
    ok, encoded = cv2.imencode(".png", mask)
    assert ok
    return {
        "format": "png",
        "width": 320,
        "height": 180,
        "data": base64.b64encode(encoded.tobytes()).decode("utf-8"),
    }


def test_decoder_parses_jpeg_base64_multisensor_payload() -> None:
    decoder = ModuleCDDemoMessageDecoder(_demo_config())
    payload = {
        "t_sync": 123.4,
        "frame_id": 7,
        "frames": {
            "left_camera": _frame_payload(_jpg_b64((255, 0, 0))),
            "right_camera": _frame_payload(_jpg_b64((0, 255, 0))),
            "top_camera": _frame_payload(_jpg_b64((0, 0, 255))),
            "imu": {
                "payload": {
                    "Imu": {
                        "gyro": {"z": 0.12},
                        "accel": {"x": 0.34},
                    }
                }
            },
        },
        "vehicle_states": {
            "ego": {
                "speed_mps": 10.0,
                "turn_signal": "left",
            }
        },
        "sync_meta": {"test": True},
    }

    decoded = decoder.decode_message("Frame", json.dumps(payload).encode("utf-8"))
    assert decoded is not None
    assert decoded.frame_input.frame_id == 7
    assert decoded.frame_input.left_frame.shape[:2] == (32, 48)
    assert decoded.frame_input.right_frame.shape[:2] == (32, 48)
    assert decoded.frame_input.top_frame is not None
    assert decoded.frame_input.ego_state.speed_kmh == 36.0
    assert decoded.frame_input.ego_state.left_signal is True
    assert decoded.frame_input.ego_state.right_signal is False
    assert decoded.frame_input.ego_state.imu_gz == 0.12
    assert decoded.frame_input.ego_state.imu_ax == 0.34


def test_decoder_fallbacks_when_optional_fields_are_missing() -> None:
    decoder = ModuleCDDemoMessageDecoder(_demo_config())
    payload = {
        "t_sync": 5.0,
        "frame_id": 2,
        "frames": {
            "left_camera": _frame_payload(_jpg_b64()),
            "right_camera": _frame_payload(_jpg_b64()),
        },
        "vehicle_states": {
            "something_else": {
                "speed_mps": 5.0,
                "turn_signal": "hazard",
            }
        },
    }

    decoded = decoder.decode_message("Frame", json.dumps(payload).encode("utf-8"))
    assert decoded is not None
    assert decoded.frame_input.top_frame is None
    assert decoded.frame_input.ego_state.imu_gz == 0.0
    assert decoded.frame_input.ego_state.imu_ax == 0.0
    assert decoded.frame_input.ego_state.speed_kmh == 18.0
    assert decoded.frame_input.ego_state.left_signal is True
    assert decoded.frame_input.ego_state.right_signal is True


def test_decoder_parses_external_zone_masks_from_zmq_payload() -> None:
    decoder = ModuleCDDemoMessageDecoder(_demo_config())
    payload = {
        "t_sync": 9.0,
        "frame_id": 8,
        "frames": {
            "left_camera": _frame_payload(_jpg_b64((255, 0, 0))),
            "right_camera": _frame_payload(_jpg_b64((0, 255, 0))),
            "adjacent_lane_mask_left": {
                "sensor_id": "adjacent_lane_mask_left",
                "sensor_type": "camera",
                "payload": {"Image": _mask_payload((slice(60, 180), slice(0, 90)))},
            },
            "adjacent_lane_mask_right": {
                "sensor_id": "adjacent_lane_mask_right",
                "sensor_type": "camera",
                "payload": {"Image": _mask_payload((slice(60, 180), slice(230, 320)))},
            },
        },
    }

    decoded = decoder.decode_message("Frame", json.dumps(payload).encode("utf-8"))

    assert decoded is not None
    assert decoded.frame_input.left_zone_mask is not None
    assert decoded.frame_input.right_zone_mask is not None
    assert decoded.frame_input.left_zone_mask.shape == (180, 320)
    assert decoded.frame_input.right_zone_mask.shape == (180, 320)
    assert decoded.frame_input.left_zone_mask[120, 40] == 1.0
    assert decoded.frame_input.left_zone_mask[10, 10] == 0.0
    assert decoded.frame_input.right_zone_mask[120, 280] == 1.0
    assert decoded.frame_input.source_details["external_zone_masks"]["left"] == [180, 320]


def test_decoder_drops_frame_when_required_camera_is_missing() -> None:
    decoder = ModuleCDDemoMessageDecoder(_demo_config())
    payload = {
        "t_sync": 5.0,
        "frame_id": 3,
        "frames": {
            "left_camera": _frame_payload(_jpg_b64()),
        },
    }

    decoded = decoder.decode_message("Frame", json.dumps(payload).encode("utf-8"))
    assert decoded is None


def test_zone_controller_prefers_segmentation_mask_when_quality_is_good() -> None:
    controller = AdaptiveZoneController(_demo_config())
    fallback_zone = controller.compute_zone(
        30.0,
        False,
        False,
        0.0,
        100,
        100,
        "left",
    )
    zone_mask = np.zeros((100, 100), dtype=np.float32)
    zone_mask[48:100, 6:41] = 1.0

    refined_zone = controller.refine_zone_from_mask(zone_mask, fallback_zone)

    assert refined_zone.source == "segmentation"
    assert refined_zone.camera_side == "left"
    assert refined_zone.polygon_px.shape == (4, 2)
    assert float(refined_zone.polygon_px[:, 1].max()) >= 95.0
    assert float(refined_zone.polygon_px[:, 0].mean()) < 50.0
    assert not np.array_equal(refined_zone.polygon_px, fallback_zone.polygon_px)


def test_zone_controller_marks_external_mask_source_when_used() -> None:
    controller = AdaptiveZoneController(_demo_config())
    fallback_zone = controller.compute_zone(
        30.0,
        False,
        False,
        0.0,
        100,
        100,
        "left",
    )
    zone_mask = np.zeros((100, 100), dtype=np.float32)
    zone_mask[8:100, 6:18] = 1.0
    zone_mask[16:100, 18:34] = 1.0
    zone_mask[32:100, 34:48] = 1.0

    refined_zone = controller.refine_zone_from_mask(
        zone_mask,
        fallback_zone,
        source_name="external_mask",
    )

    assert refined_zone.source == "external_mask"
    assert refined_zone.mask_px is not None
    assert refined_zone.polygon_px.shape == (128, 2)
    assert float(refined_zone.polygon_px[:, 1].min()) <= 12.0
    assert float(refined_zone.polygon_px[:, 1].max()) >= 95.0


def test_external_mask_length_extends_with_imu() -> None:
    controller = AdaptiveZoneController(_demo_config())
    neutral_zone = controller.compute_zone(
        30.0,
        False,
        False,
        0.0,
        100,
        100,
        "left",
    )
    imu_zone = controller.compute_zone(
        30.0,
        False,
        False,
        0.5,
        100,
        100,
        "left",
    )
    zone_mask = np.zeros((100, 100), dtype=np.float32)
    zone_mask[40:100, 8:42] = 1.0

    neutral_refined = controller.refine_zone_from_mask(
        zone_mask,
        neutral_zone,
        source_name="external_mask",
    )
    imu_refined = controller.refine_zone_from_mask(
        zone_mask,
        imu_zone,
        source_name="external_mask",
    )

    assert neutral_refined.mask_px is not None
    assert imu_refined.mask_px is not None
    assert float(imu_refined.polygon_px[:, 1].min()) < float(neutral_refined.polygon_px[:, 1].min())
    assert float(imu_refined.mask_px.sum()) >= float(neutral_refined.mask_px.sum())


def test_external_mask_length_extends_with_speed() -> None:
    controller = AdaptiveZoneController(_demo_config())
    base_zone = controller.compute_zone(
        30.0,
        False,
        False,
        0.0,
        100,
        100,
        "left",
    )
    fast_zone = controller.compute_zone(
        80.0,
        False,
        False,
        0.0,
        100,
        100,
        "left",
    )
    zone_mask = np.zeros((100, 100), dtype=np.float32)
    zone_mask[40:100, 8:42] = 1.0

    base_refined = controller.refine_zone_from_mask(
        zone_mask,
        base_zone,
        source_name="external_mask",
    )
    fast_refined = controller.refine_zone_from_mask(
        zone_mask,
        fast_zone,
        source_name="external_mask",
    )

    assert base_refined.mask_px is not None
    assert fast_refined.mask_px is not None
    assert float(fast_refined.polygon_px[:, 1].min()) < float(base_refined.polygon_px[:, 1].min())
    assert float(fast_refined.mask_px.sum()) > float(base_refined.mask_px.sum())


def test_external_mask_length_shrinks_to_min_30_percent_at_low_speed() -> None:
    controller = AdaptiveZoneController(_demo_config())
    slow_zone = controller.compute_zone(
        0.0,
        False,
        False,
        0.0,
        100,
        100,
        "left",
    )
    zone_mask = np.zeros((100, 100), dtype=np.float32)
    zone_mask[40:100, 8:42] = 1.0

    slow_refined = controller.refine_zone_from_mask(
        zone_mask,
        slow_zone,
        source_name="external_mask",
    )

    assert slow_refined.mask_px is not None
    per_column_heights = []
    for column in range(zone_mask.shape[1]):
        original_rows = np.where(zone_mask[:, column] > 0)[0]
        refined_rows = np.where(slow_refined.mask_px[:, column] > 0)[0]
        if original_rows.size == 0:
            continue
        assert refined_rows.size > 0
        original_height = int(original_rows.max() - original_rows.min() + 1)
        refined_height = int(refined_rows.max() - refined_rows.min() + 1)
        per_column_heights.append((refined_height, original_height))
    assert per_column_heights
    for refined_height, original_height in per_column_heights:
        assert refined_height >= int(round(original_height * 0.30))
        assert refined_height <= int(round(original_height * 0.35))


def test_zone_controller_falls_back_to_template_when_segmentation_mask_is_invalid() -> None:
    controller = AdaptiveZoneController(_demo_config())
    fallback_zone = controller.compute_zone(
        30.0,
        False,
        False,
        0.0,
        100,
        100,
        "right",
    )
    zone_mask = np.zeros((100, 100), dtype=np.float32)
    zone_mask[5:12, 5:12] = 1.0

    refined_zone = controller.refine_zone_from_mask(zone_mask, fallback_zone)

    assert refined_zone.source == "template"
    assert np.array_equal(refined_zone.polygon_px, fallback_zone.polygon_px)


def test_zone_stabilizer_holds_recent_segmentation_when_single_frame_is_bad() -> None:
    controller = AdaptiveZoneController(_demo_config())
    stabilizer = controller.build_stabilizer()
    template_zone = controller.compute_zone(30.0, False, False, 0.0, 100, 100, "left")
    zone_mask = np.zeros((100, 100), dtype=np.float32)
    zone_mask[48:100, 6:41] = 1.0
    segmentation_zone = controller.refine_zone_from_mask(zone_mask, template_zone)

    stable_segmentation = stabilizer.stabilize(
        segmentation_zone,
        template_zone=template_zone,
        image_w=100,
        image_h=100,
    )
    fallback_zone = stabilizer.stabilize(
        template_zone,
        template_zone=template_zone,
        image_w=100,
        image_h=100,
    )

    assert stable_segmentation.source == "segmentation"
    assert fallback_zone.source == "segmentation_hold"
    assert np.array_equal(fallback_zone.polygon_px, stable_segmentation.polygon_px)


def test_zone_controller_biases_segmentation_with_left_imu_trend() -> None:
    controller = AdaptiveZoneController(_demo_config())
    template_zone = controller.compute_zone(
        30.0,
        False,
        False,
        0.5,
        100,
        100,
        "left",
    )
    zone_mask = np.zeros((100, 100), dtype=np.float32)
    zone_mask[48:100, 6:41] = 1.0
    segmentation_zone = controller.refine_zone_from_mask(zone_mask, template_zone)

    biased_zone = controller.apply_imu_bias_to_segmentation(
        segmentation_zone,
        template_zone=template_zone,
        image_w=100,
        image_h=100,
    )

    assert biased_zone.source == "segmentation_imu"
    assert biased_zone.polygon_norm[0, 1] <= segmentation_zone.polygon_norm[0, 1]
    assert biased_zone.polygon_norm[0, 0] <= segmentation_zone.polygon_norm[0, 0]
    assert biased_zone.polygon_norm[3, 0] <= segmentation_zone.polygon_norm[3, 0]
    assert (
        segmentation_zone.polygon_norm[0, 1] - biased_zone.polygon_norm[0, 1]
        <= (segmentation_zone.polygon_norm[3, 1] - segmentation_zone.polygon_norm[0, 1]) * 0.06 + 1e-6
    )
    assert (
        segmentation_zone.polygon_norm[0, 0] - biased_zone.polygon_norm[0, 0]
        <= (segmentation_zone.polygon_norm[1, 0] - segmentation_zone.polygon_norm[0, 0]) * 0.08 + 1e-6
    )


def test_pipeline_prefers_external_zone_mask_over_model_mask() -> None:
    frame_input = RuntimeFrameInput(
        frame_id=1,
        timestamp=1.0,
        left_frame=np.zeros((10, 10, 3), dtype=np.uint8),
        right_frame=np.zeros((10, 10, 3), dtype=np.uint8),
        top_frame=None,
        ego_state=RuntimeEgoState(
            speed_kmh=0.0,
            left_signal=False,
            right_signal=False,
            imu_gz=0.0,
            imu_ax=0.0,
            timestamp=1.0,
        ),
        left_zone_mask=np.ones((180, 320), dtype=np.float32),
        right_zone_mask=None,
    )

    left_mask, left_source = BSDRuntimePipeline._select_zone_mask(
        "left",
        frame_input,
        np.zeros((10, 10), dtype=np.float32),
    )
    right_mask, right_source = BSDRuntimePipeline._select_zone_mask(
        "right",
        frame_input,
        np.ones((10, 10), dtype=np.float32),
    )

    assert left_source == "external_mask"
    assert left_mask.shape == (180, 320)
    assert right_source == "segmentation"
    assert right_mask.shape == (10, 10)


class _StubPredictor:
    def get_model_weights(self, track_id: int) -> dict[str, float]:
        return {"CV": 0.7, "CA": 0.2, "CTRV": 0.1}

    def predict_position(self, track_id: int, horizon_s: float, dt: float) -> tuple[float, float]:
        return 100.0 + horizon_s * 5.0, 200.0


class _AlertingPredictor:
    def __init__(self, risk: float = 0.9, confidence: float = 1.0):
        self.risk = risk
        self.confidence = confidence

    def predict_risk(self, track_id: int, zone: BlindSpotZone, dt: float) -> float:
        return self.risk

    def get_model_weights(self, track_id: int) -> dict[str, float]:
        return {"CV": 1.0}

    def get_prediction_confidence(self, track_id: int) -> float:
        return self.confidence


def test_risk_manager_emits_only_once_per_track_when_stationary() -> None:
    config = _demo_config()
    config["alerting"].update(
        {
            "warning_risk_thresh": 0.5,
            "warning_hysteresis": 1,
            "warning_min_track_age": 1,
            "warning_repeat_interval_frames": 0,
            "danger_hysteresis": 99,
            "danger_min_track_age": 99,
        }
    )
    manager = RiskManager(config)
    predictor = _AlertingPredictor(risk=0.9, confidence=1.0)
    zone = BlindSpotZone(
        "left",
        np.array([[0.0, 0.0], [0.2, 0.0], [0.2, 0.2], [0.0, 0.2]], dtype=np.float32),
        np.array([[0, 0], [20, 0], [20, 20], [0, 20]], dtype=np.int32),
        0.0,
        False,
        0.0,
        1.0,
    )
    track = TrackedObject(
        track_id=7,
        bbox=(60, 60, 80, 90),
        confidence=0.95,
        class_id=0,
        class_name="car",
        center=(70.0, 90.0),
        area=600.0,
    )

    first = manager.evaluate([track], zone, predictor, "left", 0.05, 0.0)
    second = manager.evaluate([track], zone, predictor, "left", 0.05, 0.0)

    assert [event.level for event in first] == [AlertLevel.WARNING]
    assert second == []


def test_risk_manager_rearms_track_after_vehicle_moves_again() -> None:
    config = _demo_config()
    config["alerting"].update(
        {
            "warning_risk_thresh": 0.5,
            "warning_hysteresis": 1,
            "warning_min_track_age": 1,
            "warning_repeat_interval_frames": 0,
            "danger_hysteresis": 99,
            "danger_min_track_age": 99,
        }
    )
    manager = RiskManager(config)
    predictor = _AlertingPredictor(risk=0.9, confidence=1.0)
    zone = BlindSpotZone(
        "left",
        np.array([[0.0, 0.0], [0.2, 0.0], [0.2, 0.2], [0.0, 0.2]], dtype=np.float32),
        np.array([[0, 0], [20, 0], [20, 20], [0, 20]], dtype=np.int32),
        0.0,
        False,
        0.0,
        1.0,
    )
    track = TrackedObject(
        track_id=7,
        bbox=(60, 60, 80, 90),
        confidence=0.95,
        class_id=0,
        class_name="car",
        center=(70.0, 90.0),
        area=600.0,
    )

    stationary_first = manager.evaluate([track], zone, predictor, "left", 0.05, 0.0)
    stationary_second = manager.evaluate([track], zone, predictor, "left", 0.05, 0.0)
    moving = manager.evaluate([track], zone, predictor, "left", 0.05, 5.0)

    assert [event.level for event in stationary_first] == [AlertLevel.WARNING]
    assert stationary_second == []
    assert [event.level for event in moving] == [AlertLevel.WARNING]


def test_risk_manager_allows_stationary_warning_to_escalate_to_danger() -> None:
    config = _demo_config()
    config["alerting"].update(
        {
            "warning_risk_thresh": 0.5,
            "warning_hysteresis": 1,
            "warning_min_track_age": 1,
            "warning_repeat_interval_frames": 0,
            "danger_hysteresis": 1,
            "danger_min_track_age": 1,
            "danger_repeat_interval_frames": 0,
        }
    )
    manager = RiskManager(config)
    predictor = _AlertingPredictor(risk=0.9, confidence=1.0)
    zone = BlindSpotZone(
        "left",
        np.array([[0.0, 0.0], [0.2, 0.0], [0.2, 0.2], [0.0, 0.2]], dtype=np.float32),
        np.array([[0, 0], [20, 0], [20, 20], [0, 20]], dtype=np.int32),
        0.0,
        False,
        0.0,
        1.0,
    )
    approaching_track = TrackedObject(
        track_id=7,
        bbox=(60, 60, 80, 90),
        confidence=0.95,
        class_id=0,
        class_name="car",
        center=(70.0, 90.0),
        area=600.0,
    )
    in_zone_track = TrackedObject(
        track_id=7,
        bbox=(2, 2, 16, 18),
        confidence=0.95,
        class_id=0,
        class_name="car",
        center=(9.0, 18.0),
        area=224.0,
    )

    warning = manager.evaluate([approaching_track], zone, predictor, "left", 0.05, 0.0)
    danger = manager.evaluate([in_zone_track], zone, predictor, "left", 0.05, 0.0)
    repeated_danger = manager.evaluate([in_zone_track], zone, predictor, "left", 0.05, 0.0)

    assert [event.level for event in warning] == [AlertLevel.WARNING]
    assert [event.level for event in danger] == [AlertLevel.DANGER]
    assert repeated_danger == []


def test_risk_manager_holds_active_alert_for_multiple_frames() -> None:
    config = _demo_config()
    config["alerting"].update(
        {
            "warning_risk_thresh": 0.5,
            "warning_hysteresis": 1,
            "warning_min_track_age": 1,
            "warning_repeat_interval_frames": 0,
            "warning_display_hold_frames": 3,
            "danger_hysteresis": 99,
            "danger_min_track_age": 99,
        }
    )
    manager = RiskManager(config)
    predictor = _AlertingPredictor(risk=0.9, confidence=1.0)
    zone = BlindSpotZone(
        "left",
        np.array([[0.0, 0.0], [0.2, 0.0], [0.2, 0.2], [0.0, 0.2]], dtype=np.float32),
        np.array([[0, 0], [20, 0], [20, 20], [0, 20]], dtype=np.int32),
        0.0,
        False,
        0.0,
        1.0,
    )
    track = TrackedObject(
        track_id=7,
        bbox=(60, 60, 80, 90),
        confidence=0.95,
        class_id=0,
        class_name="car",
        center=(70.0, 90.0),
        area=600.0,
    )

    manager.evaluate([track], zone, predictor, "left", 0.05, 5.0)
    held_after_emit = manager.active_alerts()
    manager.evaluate([], zone, predictor, "left", 0.05, 5.0)
    held_next_frame = manager.active_alerts()
    manager.evaluate([], zone, predictor, "left", 0.05, 5.0)
    expired_after_hold = manager.active_alerts()

    assert [event.level for event in held_after_emit] == [AlertLevel.WARNING]
    assert [event.level for event in held_next_frame] == [AlertLevel.WARNING]
    assert expired_after_hold == []


def test_risk_manager_extends_active_alert_hold_on_repeat_track_id() -> None:
    config = _demo_config()
    config["alerting"].update(
        {
            "warning_risk_thresh": 0.5,
            "warning_hysteresis": 1,
            "warning_min_track_age": 1,
            "warning_repeat_interval_frames": 0,
            "warning_display_hold_frames": 3,
            "display_repeat_extend_frames": 2,
            "display_max_hold_frames": 8,
            "danger_hysteresis": 99,
            "danger_min_track_age": 99,
        }
    )
    manager = RiskManager(config)
    predictor = _AlertingPredictor(risk=0.9, confidence=1.0)
    zone = BlindSpotZone(
        "left",
        np.array([[0.0, 0.0], [0.2, 0.0], [0.2, 0.2], [0.0, 0.2]], dtype=np.float32),
        np.array([[0, 0], [20, 0], [20, 20], [0, 20]], dtype=np.int32),
        0.0,
        False,
        0.0,
        1.0,
    )
    track = TrackedObject(
        track_id=7,
        bbox=(60, 60, 80, 90),
        confidence=0.95,
        class_id=0,
        class_name="car",
        center=(70.0, 90.0),
        area=600.0,
    )

    manager.evaluate([track], zone, predictor, "left", 0.05, 5.0)
    manager.evaluate([], zone, predictor, "left", 0.05, 5.0)
    manager.evaluate([track], zone, predictor, "left", 0.05, 5.0)
    manager.evaluate([], zone, predictor, "left", 0.05, 5.0)
    manager.evaluate([], zone, predictor, "left", 0.05, 5.0)
    manager.evaluate([], zone, predictor, "left", 0.05, 5.0)
    still_held = manager.active_alerts()
    manager.evaluate([], zone, predictor, "left", 0.05, 5.0)
    expired_after_extension = manager.active_alerts()

    assert [event.level for event in still_held] == [AlertLevel.WARNING]
    assert expired_after_extension == []


def test_risk_manager_suppresses_warning_for_small_bbox() -> None:
    config = _demo_config()
    config["alerting"].update(
        {
            "warning_risk_thresh": 0.5,
            "warning_hysteresis": 1,
            "warning_min_track_age": 1,
            "warning_repeat_interval_frames": 0,
            "min_alert_bbox_area_px2": 500.0,
            "min_alert_bbox_width_px": 18.0,
            "min_alert_bbox_height_px": 18.0,
            "danger_hysteresis": 99,
            "danger_min_track_age": 99,
        }
    )
    manager = RiskManager(config)
    predictor = _AlertingPredictor(risk=0.9, confidence=1.0)
    zone = BlindSpotZone(
        "left",
        np.array([[0.0, 0.0], [0.2, 0.0], [0.2, 0.2], [0.0, 0.2]], dtype=np.float32),
        np.array([[0, 0], [20, 0], [20, 20], [0, 20]], dtype=np.int32),
        0.0,
        False,
        0.0,
        1.0,
    )
    small_track = TrackedObject(
        track_id=7,
        bbox=(60, 60, 74, 76),
        confidence=0.95,
        class_id=0,
        class_name="car",
        center=(67.0, 76.0),
        area=224.0,
    )

    warning = manager.evaluate([small_track], zone, predictor, "left", 0.05, 5.0)

    assert warning == []
    assert manager.active_alerts() == []


def test_risk_manager_suppresses_danger_for_small_bbox() -> None:
    config = _demo_config()
    config["alerting"].update(
        {
            "warning_risk_thresh": 0.5,
            "warning_hysteresis": 1,
            "warning_min_track_age": 1,
            "warning_repeat_interval_frames": 0,
            "min_alert_bbox_area_px2": 500.0,
            "min_alert_bbox_width_px": 18.0,
            "min_alert_bbox_height_px": 18.0,
            "danger_hysteresis": 1,
            "danger_min_track_age": 1,
            "danger_repeat_interval_frames": 0,
        }
    )
    manager = RiskManager(config)
    predictor = _AlertingPredictor(risk=0.9, confidence=1.0)
    zone = BlindSpotZone(
        "left",
        np.array([[0.0, 0.0], [0.2, 0.0], [0.2, 0.2], [0.0, 0.2]], dtype=np.float32),
        np.array([[0, 0], [20, 0], [20, 20], [0, 20]], dtype=np.int32),
        0.0,
        False,
        0.0,
        1.0,
    )
    small_in_zone_track = TrackedObject(
        track_id=7,
        bbox=(2, 2, 16, 18),
        confidence=0.95,
        class_id=0,
        class_name="car",
        center=(9.0, 18.0),
        area=224.0,
    )

    danger = manager.evaluate([small_in_zone_track], zone, predictor, "left", 0.05, 5.0)

    assert danger == []
    assert manager.active_alerts() == []


def test_payload_builder_emits_modulecd_and_bsd_fields() -> None:
    left_frame = np.zeros((64, 128, 3), dtype=np.uint8)
    right_frame = np.zeros((64, 128, 3), dtype=np.uint8)
    ego_state = RuntimeEgoState(
        speed_kmh=35.0,
        left_signal=True,
        right_signal=False,
        imu_gz=0.15,
        imu_ax=0.2,
        timestamp=1.0,
    )
    frame_input = RuntimeFrameInput(
        frame_id=11,
        timestamp=1.0,
        left_frame=left_frame,
        right_frame=right_frame,
        top_frame=None,
        ego_state=ego_state,
    )
    zone = BlindSpotZone(
        camera_side="left",
        polygon_norm=np.array([[0.1, 0.1], [0.9, 0.1], [0.9, 1.0], [0.1, 1.0]], dtype=np.float32),
        polygon_px=np.array([[10, 10], [118, 10], [118, 63], [10, 63]], dtype=np.int32),
        speed_kmh=35.0,
        signal_active=True,
        imu_gz=0.15,
        scale_used=1.2,
    )
    track = TrackedObject(
        track_id=1,
        bbox=(20, 20, 70, 60),
        confidence=0.93,
        class_id=0,
        class_name="car",
        center=(45.0, 60.0),
        area=2000.0,
    )
    alert = AlertEvent(
        track_id=1,
        camera_side="left",
        level=AlertLevel.WARNING,
        r_score=0.82,
        time_to_entry_s=1.4,
        bbox=track.bbox,
        model_weights={"CV": 0.7},
    )
    result = RuntimeProcessingResult(
        frame_idx=0,
        frame_input=frame_input,
        detector_backend="custom",
        detector_device="cpu",
        fps=12.5,
        dt=0.05,
        tracked_by_side={"left": [track], "right": []},
        zones={"left": zone, "right": BlindSpotZone("right", zone.polygon_norm, zone.polygon_px, 35.0, False, 0.15, 1.1)},
        alerts={"left": [alert], "right": []},
        predictors={"left": _StubPredictor(), "right": _StubPredictor()},
        max_alert_level=AlertLevel.WARNING,
    )

    payload = build_modulecd_bsd_payload(
        result,
        class_names=["car"],
        top_camera_present=False,
        sensor_ids={
            "left_camera": "left_camera",
            "right_camera": "right_camera",
            "top_camera": "top_camera",
            "imu": "imu",
            "vehicle_state": "ego",
        },
    )
    assert payload["frame_id"] == 11
    assert payload["image_size"] == {"width": 128, "height": 64}
    assert payload["traffic_signs"] == []
    assert payload["num_traffic_signs"] == 0
    assert payload["num_vehicles"] == 1
    assert payload["tracked_pedestrians"] is False
    assert payload["bsd"]["schema_version"] == "bsd.demo.modulecd.v1"
    assert payload["bsd"]["left"]["zone"]["source"] == "template"
    assert payload["bsd"]["left"]["zone"]["visible"] is False
    assert payload["bsd"]["left"]["tracks"][0]["class_name"] == "car"
    assert payload["bsd"]["left"]["alerts"][0]["level"] == "WARNING"


def test_payload_builder_marks_only_external_mask_zone_as_visible() -> None:
    left_frame = np.zeros((64, 128, 3), dtype=np.uint8)
    right_frame = np.zeros((64, 128, 3), dtype=np.uint8)
    ego_state = RuntimeEgoState(
        speed_kmh=25.0,
        left_signal=False,
        right_signal=False,
        imu_gz=0.0,
        imu_ax=0.0,
        timestamp=1.0,
    )
    frame_input = RuntimeFrameInput(
        frame_id=12,
        timestamp=1.0,
        left_frame=left_frame,
        right_frame=right_frame,
        top_frame=None,
        ego_state=ego_state,
    )
    left_zone = BlindSpotZone(
        camera_side="left",
        polygon_norm=np.array([[0.1, 0.1], [0.4, 0.1], [0.5, 1.0], [0.0, 1.0]], dtype=np.float32),
        polygon_px=np.array([[10, 10], [50, 10], [63, 63], [0, 63]], dtype=np.int32),
        speed_kmh=25.0,
        signal_active=False,
        imu_gz=0.0,
        scale_used=1.0,
        source="external_mask",
        mask_px=np.ones((64, 128), dtype=np.uint8),
    )
    right_zone = BlindSpotZone(
        camera_side="right",
        polygon_norm=np.array([[0.6, 0.1], [0.9, 0.1], [1.0, 1.0], [0.5, 1.0]], dtype=np.float32),
        polygon_px=np.array([[76, 10], [118, 10], [127, 63], [64, 63]], dtype=np.int32),
        speed_kmh=25.0,
        signal_active=False,
        imu_gz=0.0,
        scale_used=1.0,
        source="segmentation",
    )
    result = RuntimeProcessingResult(
        frame_idx=0,
        frame_input=frame_input,
        detector_backend="custom",
        detector_device="cpu",
        fps=10.0,
        dt=0.05,
        tracked_by_side={"left": [], "right": []},
        zones={"left": left_zone, "right": right_zone},
        alerts={"left": [], "right": []},
        predictors={"left": _StubPredictor(), "right": _StubPredictor()},
        max_alert_level=AlertLevel.SAFE,
    )

    payload = build_modulecd_bsd_payload(
        result,
        class_names=["car"],
        top_camera_present=False,
        sensor_ids={
            "left_camera": "left_camera",
            "right_camera": "right_camera",
            "top_camera": "top_camera",
            "imu": "imu",
            "vehicle_state": "ego",
        },
    )

    assert payload["bsd"]["left"]["zone"]["visible"] is True
    assert payload["bsd"]["right"]["zone"]["visible"] is False
