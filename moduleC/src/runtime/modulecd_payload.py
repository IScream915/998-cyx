from __future__ import annotations

from typing import Any

import numpy as np

from src.alerting.risk_manager import AlertEvent, AlertLevel
from src.runtime.types import PredictorType, RuntimeFrameInput, RuntimeProcessingResult
from src.tracking.bytetrack_wrapper import TrackedObject
from src.zones.zone_model import BlindSpotZone


def runtime_class_name(class_id: int, class_names: list[str]) -> str:
    if 0 <= int(class_id) < len(class_names):
        return str(class_names[int(class_id)])
    return f"class_{int(class_id)}"


def _alert_level_name(level: AlertLevel | int) -> str:
    return AlertLevel(level).name


def _serialize_bbox(bbox: tuple[int, int, int, int]) -> list[int]:
    return [int(value) for value in bbox]


def _serialize_point(point: tuple[float, float]) -> list[float]:
    return [float(point[0]), float(point[1])]


def _serialize_polygon(polygon: np.ndarray) -> list[list[float]]:
    polygon_array = np.asarray(polygon, dtype=float)
    return [[float(x), float(y)] for x, y in polygon_array.tolist()]


def _serialize_prediction_points(
    predictor: PredictorType,
    track_id: int,
    dt: float,
) -> list[dict[str, object]]:
    prediction_points: list[dict[str, object]] = []
    if not hasattr(predictor, "predict_position"):
        return prediction_points
    for horizon_s in (1.0, 2.0):
        try:
            px, py = predictor.predict_position(track_id, horizon_s, dt)
        except Exception:
            continue
        prediction_points.append(
            {
                "horizon_s": float(horizon_s),
                "point_px": [float(px), float(py)],
            }
        )
    return prediction_points


def _serialize_track_state(
    obj: TrackedObject,
    predictor: PredictorType,
    class_names: list[str],
    dt: float,
) -> dict[str, object]:
    model_weights: dict[str, float] = {}
    if hasattr(predictor, "get_model_weights"):
        try:
            model_weights = {
                str(key): float(value)
                for key, value in predictor.get_model_weights(obj.track_id).items()
            }
        except Exception:
            model_weights = {}
    payload: dict[str, object] = {
        "track_id": int(obj.track_id),
        "class_id": int(obj.class_id),
        "class_name": runtime_class_name(obj.class_id, class_names),
        "confidence": float(obj.confidence),
        "bbox": _serialize_bbox(obj.bbox),
        "center_px": _serialize_point(obj.center),
        "area_px2": float(obj.area),
        "model_weights": model_weights,
    }
    predictions = _serialize_prediction_points(predictor, int(obj.track_id), dt)
    if predictions:
        payload["predictions"] = predictions
    return payload


def _serialize_alert_event(event: AlertEvent) -> dict[str, object]:
    return {
        "track_id": int(event.track_id),
        "level": _alert_level_name(event.level),
        "r_score": float(event.r_score),
        "time_to_entry_s": (
            None if event.time_to_entry_s is None else float(event.time_to_entry_s)
        ),
        "bbox": _serialize_bbox(event.bbox),
        "model_weights": {
            str(key): float(value) for key, value in event.model_weights.items()
        },
    }


def _side_alert_level(events: list[AlertEvent]) -> str:
    highest = max((event.level for event in events), default=AlertLevel.SAFE)
    return _alert_level_name(highest)


def _zone_visible_in_frontend(zone: BlindSpotZone) -> bool:
    return str(zone.source) == "external_mask"


def _serialize_zone_state(zone: BlindSpotZone, events: list[AlertEvent]) -> dict[str, object]:
    return {
        "polygon_px": _serialize_polygon(zone.polygon_px),
        "level": _side_alert_level(events),
        "scale_used": float(zone.scale_used),
        "source": str(zone.source),
        "visible": _zone_visible_in_frontend(zone),
    }


def _side_payload(
    side: str,
    tracked_by_side: dict[str, list[TrackedObject]],
    zones: dict[str, BlindSpotZone],
    alerts: dict[str, list[AlertEvent]],
    predictors: dict[str, PredictorType],
    class_names: list[str],
    dt: float,
) -> dict[str, object]:
    side_tracks = tracked_by_side.get(side, [])
    side_alerts = alerts.get(side, [])
    predictor = predictors[side]
    return {
        "tracked_count": len(side_tracks),
        "alert_count": len(side_alerts),
        "zone": _serialize_zone_state(zones[side], side_alerts),
        "tracks": [
            _serialize_track_state(obj, predictor, class_names, dt)
            for obj in side_tracks
        ],
        "alerts": [_serialize_alert_event(event) for event in side_alerts],
    }


def _is_pedestrian(class_name: str) -> bool:
    return class_name.strip().lower() in {"person", "pedestrian"}


def _modulecd_entities(
    tracked_by_side: dict[str, list[TrackedObject]],
    class_names: list[str],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    pedestrians: list[dict[str, object]] = []
    vehicles: list[dict[str, object]] = []
    for side in ("left", "right"):
        for obj in tracked_by_side.get(side, []):
            class_name = runtime_class_name(obj.class_id, class_names)
            row = {
                "bbox": _serialize_bbox(obj.bbox),
                "confidence": float(obj.confidence),
            }
            if _is_pedestrian(class_name):
                pedestrians.append(row)
            else:
                vehicles.append(
                    {
                        **row,
                        "track_id": int(obj.track_id),
                        "side": side,
                        "class_name": class_name,
                    }
                )
    return pedestrians, vehicles


def build_modulecd_bsd_payload(
    result: RuntimeProcessingResult,
    *,
    class_names: list[str],
    top_camera_present: bool,
    sensor_ids: dict[str, str],
) -> dict[str, object]:
    left_h, left_w = result.frame_input.left_frame.shape[:2]
    top_h = top_w = None
    if result.frame_input.top_frame is not None:
        top_h, top_w = result.frame_input.top_frame.shape[:2]
    pedestrians, vehicles = _modulecd_entities(result.tracked_by_side, class_names)

    return {
        "frame_id": int(result.frame_input.frame_id),
        "image_size": {"width": int(left_w), "height": int(left_h)},
        "traffic_signs": [],
        "num_traffic_signs": 0,
        "pedestrians": pedestrians,
        "num_pedestrians": len(pedestrians),
        "vehicles": vehicles,
        "num_vehicles": len(vehicles),
        "tracked_pedestrians": bool(pedestrians),
        "bsd": {
            "schema_version": "bsd.demo.modulecd.v1",
            "input_source": "zmq_sync_frame",
            "ego": {
                "speed_kmh": float(result.frame_input.ego_state.speed_kmh),
                "left_signal": bool(result.frame_input.ego_state.left_signal),
                "right_signal": bool(result.frame_input.ego_state.right_signal),
                "imu_gz": float(result.frame_input.ego_state.imu_gz),
                "imu_ax": float(result.frame_input.ego_state.imu_ax),
            },
            "system": {
                "detector_backend": str(result.detector_backend),
                "detector_device": str(result.detector_device),
                "fps": float(result.fps),
                "max_alert_level": _alert_level_name(result.max_alert_level),
            },
            "left": _side_payload(
                "left",
                result.tracked_by_side,
                result.zones,
                result.alerts,
                result.predictors,
                class_names,
                result.dt,
            ),
            "right": _side_payload(
                "right",
                result.tracked_by_side,
                result.zones,
                result.alerts,
                result.predictors,
                class_names,
                result.dt,
            ),
            "overview": {
                "top_camera_present": bool(top_camera_present),
                "left_image_size": {"width": int(left_w), "height": int(left_h)},
                "right_image_size": {
                    "width": int(result.frame_input.right_frame.shape[1]),
                    "height": int(result.frame_input.right_frame.shape[0]),
                },
                "top_image_size": (
                    None
                    if top_h is None or top_w is None
                    else {"width": int(top_w), "height": int(top_h)}
                ),
                "sensor_ids": sensor_ids,
            },
        },
    }
