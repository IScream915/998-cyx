from __future__ import annotations

import base64
from dataclasses import dataclass
import json
from typing import Any

import cv2
import numpy as np

from src.runtime.types import RuntimeEgoState, RuntimeFrameInput


@dataclass(frozen=True)
class DecodedSensorBundle:
    frame_input: RuntimeFrameInput
    top_camera_present: bool
    sensor_ids: dict[str, str]
    browser_cameras: dict[str, dict[str, object]]


class ModuleCDDemoMessageDecoder:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        zmq_cfg = config["demo"]["zmq"]
        defaults = config["demo"]["defaults"]
        sensors_cfg = config["demo"]["sensors"]
        self.topic = str(zmq_cfg["input_topic"])
        self.left_sensor_id = str(sensors_cfg["left_camera_sensor_id"])
        self.right_sensor_id = str(sensors_cfg["right_camera_sensor_id"])
        self.top_sensor_id = str(sensors_cfg["top_camera_sensor_id"])
        self.left_zone_mask_sensor_id = str(
            sensors_cfg.get("left_zone_mask_sensor_id", "adjacent_lane_mask_left")
        )
        self.right_zone_mask_sensor_id = str(
            sensors_cfg.get("right_zone_mask_sensor_id", "adjacent_lane_mask_right")
        )
        self.imu_sensor_id = str(sensors_cfg["imu_sensor_id"])
        self.vehicle_state_id = str(sensors_cfg["vehicle_state_id"])
        self.default_speed_kmh = float(defaults["speed_kmh"])
        self.default_left_signal = bool(defaults["left_signal"])
        self.default_right_signal = bool(defaults["right_signal"])
        self.default_imu_gz = float(defaults["imu_gz"])
        self.default_imu_ax = float(defaults["imu_ax"])

    def decode_message(
        self,
        topic: str,
        payload_bytes: bytes,
    ) -> DecodedSensorBundle | None:
        if topic != self.topic:
            return None
        payload = json.loads(payload_bytes.decode("utf-8"))
        return self.decode_payload(payload)

    def decode_payload(self, payload: dict[str, Any]) -> DecodedSensorBundle | None:
        frame_id = int(payload.get("frame_id", -1))
        timestamp = float(payload.get("t_sync", 0.0))
        frames = payload.get("frames", {})
        if not isinstance(frames, dict):
            raise ValueError("frames must be an object.")

        left_camera = self._extract_browser_camera(frames, self.left_sensor_id, required=True)
        right_camera = self._extract_browser_camera(frames, self.right_sensor_id, required=True)
        if left_camera is None or right_camera is None:
            return None
        left_frame = self._decode_camera_frame(frames, self.left_sensor_id, required=True)
        right_frame = self._decode_camera_frame(frames, self.right_sensor_id, required=True)
        if left_frame is None or right_frame is None:
            return None
        top_frame = self._decode_camera_frame(frames, self.top_sensor_id, required=False)
        left_zone_mask = self._decode_external_zone_mask(
            payload,
            frames,
            side="left",
            mask_sensor_id=self.left_zone_mask_sensor_id,
        )
        right_zone_mask = self._decode_external_zone_mask(
            payload,
            frames,
            side="right",
            mask_sensor_id=self.right_zone_mask_sensor_id,
        )
        imu_gz, imu_ax = self._decode_imu(frames.get(self.imu_sensor_id))
        ego_state = self._decode_ego_state(payload.get("vehicle_states"), imu_gz, imu_ax, timestamp)
        source_details = {
            "sync_meta": payload.get("sync_meta", {}),
            "t_sync": timestamp,
            "external_zone_masks": {
                "left": None if left_zone_mask is None else list(left_zone_mask.shape),
                "right": None if right_zone_mask is None else list(right_zone_mask.shape),
            },
        }
        return DecodedSensorBundle(
            frame_input=RuntimeFrameInput(
                frame_id=frame_id,
                timestamp=timestamp,
                left_frame=left_frame,
                right_frame=right_frame,
                top_frame=top_frame,
                ego_state=ego_state,
                source_topic=self.topic,
                source_details=source_details,
                left_zone_mask=left_zone_mask,
                right_zone_mask=right_zone_mask,
            ),
            top_camera_present=top_frame is not None,
            sensor_ids={
                "left_camera": self.left_sensor_id,
                "right_camera": self.right_sensor_id,
                "top_camera": self.top_sensor_id,
                "imu": self.imu_sensor_id,
                "vehicle_state": self.vehicle_state_id,
            },
            browser_cameras={
                "left": left_camera,
                "right": right_camera,
            },
        )

    def _decode_external_zone_mask(
        self,
        payload: dict[str, Any],
        frames: dict[str, Any],
        *,
        side: str,
        mask_sensor_id: str,
    ) -> np.ndarray | None:
        candidate = self._find_external_zone_mask_payload(
            payload,
            frames,
            side=side,
            mask_sensor_id=mask_sensor_id,
        )
        if candidate is None:
            return None
        return self._decode_mask_payload(candidate)

    def _decode_camera_frame(
        self,
        frames: dict[str, Any],
        sensor_id: str,
        *,
        required: bool,
    ) -> np.ndarray | None:
        sensor_payload = frames.get(sensor_id)
        if sensor_payload is None:
            if required:
                return None
            return None
        image_payload = self._extract_image_payload(sensor_payload)
        if image_payload is None:
            if required:
                return None
            return None
        image_format = str(image_payload.get("format", "")).strip().lower()
        if image_format != "jpeg":
            raise ValueError(f"Unsupported image format for {sensor_id}: {image_format}")
        image_data = image_payload.get("data")
        if not isinstance(image_data, str) or not image_data:
            raise ValueError(f"Missing JPEG data for {sensor_id}.")
        raw = base64.b64decode(image_data.encode("utf-8"))
        frame = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is None or frame.size == 0:
            raise ValueError(f"Failed to decode JPEG image for {sensor_id}.")
        return frame

    def _extract_browser_camera(
        self,
        frames: dict[str, Any],
        sensor_id: str,
        *,
        required: bool,
    ) -> dict[str, object] | None:
        sensor_payload = frames.get(sensor_id)
        if sensor_payload is None:
            if required:
                return None
            return None
        image_payload = self._extract_image_payload(sensor_payload)
        if image_payload is None:
            if required:
                return None
            return None
        image_data = image_payload.get("data")
        if not isinstance(image_data, str) or not image_data:
            if required:
                return None
            return None
        width = int(image_payload.get("width", 0) or 0)
        height = int(image_payload.get("height", 0) or 0)
        resolved_sensor_id = sensor_id
        if isinstance(sensor_payload, dict) and isinstance(sensor_payload.get("sensor_id"), str):
            resolved_sensor_id = str(sensor_payload["sensor_id"])
        return {
            "src": f"data:image/jpeg;base64,{image_data}",
            "width": width,
            "height": height,
            "sensor_id": resolved_sensor_id,
        }

    @staticmethod
    def _extract_image_payload(sensor_payload: Any) -> dict[str, Any] | None:
        if not isinstance(sensor_payload, dict):
            return None
        payload = sensor_payload.get("payload", sensor_payload)
        if not isinstance(payload, dict):
            return None
        image_payload = payload.get("Image")
        if isinstance(image_payload, dict):
            return image_payload
        image_payload = payload.get("image")
        if isinstance(image_payload, dict):
            return image_payload
        return None

    def _find_external_zone_mask_payload(
        self,
        payload: dict[str, Any],
        frames: dict[str, Any],
        *,
        side: str,
        mask_sensor_id: str,
    ) -> Any | None:
        candidate = self._extract_mask_payload(frames.get(mask_sensor_id))
        if candidate is not None:
            return candidate
        for container_name in ("zone_masks", "segmentation_masks", "masks", "blind_spot_masks"):
            candidate = self._lookup_mask_from_container(
                payload.get(container_name),
                side=side,
                sensor_id=mask_sensor_id,
                include_sensor_id=True,
            )
            if candidate is not None:
                return candidate
        candidate = self._lookup_mask_from_container(
            frames,
            side=side,
            sensor_id=mask_sensor_id,
            include_sensor_id=True,
        )
        if candidate is not None:
            return candidate
        return None

    @staticmethod
    def _lookup_mask_from_container(
        container: Any,
        *,
        side: str,
        sensor_id: str,
        include_sensor_id: bool,
    ) -> Any | None:
        if not isinstance(container, dict):
            return None
        keys = [
            side,
            f"{side}_mask",
            f"{side}_zone_mask",
            f"{side}_segmentation_mask",
            f"{side}_blind_spot_mask",
            f"mask_{side}",
            f"zone_mask_{side}",
            f"segmentation_mask_{side}",
        ]
        if include_sensor_id:
            keys.extend(
                [
                    sensor_id,
                    f"{sensor_id}_mask",
                    f"{sensor_id}_zone_mask",
                    f"{sensor_id}_segmentation_mask",
                ]
            )
        for key in keys:
            if key not in container:
                continue
            candidate = ModuleCDDemoMessageDecoder._extract_mask_payload(container[key])
            if candidate is not None:
                return candidate
        return None

    @staticmethod
    def _extract_mask_payload(mask_payload: Any) -> Any | None:
        if mask_payload is None:
            return None
        if isinstance(mask_payload, (list, tuple)):
            return mask_payload
        if not isinstance(mask_payload, dict):
            return None
        payload = mask_payload.get("payload", mask_payload)
        if isinstance(payload, (list, tuple)):
            return payload
        if not isinstance(payload, dict):
            return None
        for key in (
            "BinaryMask",
            "ZoneMask",
            "SegmentationMask",
            "Mask",
            "binary_mask",
            "zone_mask",
            "segmentation_mask",
            "mask",
        ):
            candidate = payload.get(key)
            if candidate is not None:
                return candidate
        image_payload = payload.get("Image")
        if isinstance(image_payload, dict):
            return image_payload
        image_payload = payload.get("image")
        if isinstance(image_payload, dict):
            return image_payload
        if "data" in payload or "values" in payload:
            return payload
        return None

    @staticmethod
    def _decode_mask_payload(mask_payload: Any) -> np.ndarray | None:
        payload = mask_payload.get("payload", mask_payload) if isinstance(mask_payload, dict) else mask_payload
        if isinstance(payload, (list, tuple)):
            return ModuleCDDemoMessageDecoder._normalize_mask_array(np.asarray(payload))
        if not isinstance(payload, dict):
            return None
        width = int(payload.get("width", 0) or 0)
        height = int(payload.get("height", 0) or 0)
        shape = payload.get("shape")
        if isinstance(shape, (list, tuple)) and len(shape) >= 2:
            height = int(shape[0] or height)
            width = int(shape[1] or width)
        values = payload.get("values")
        if isinstance(values, (list, tuple)):
            return ModuleCDDemoMessageDecoder._normalize_mask_array(
                np.asarray(values),
                width=width,
                height=height,
            )
        data = payload.get("data")
        if isinstance(data, (list, tuple)):
            return ModuleCDDemoMessageDecoder._normalize_mask_array(
                np.asarray(data),
                width=width,
                height=height,
            )
        if not isinstance(data, str) or not data:
            return None
        raw = base64.b64decode(data.encode("utf-8"))
        image_format = str(payload.get("format", payload.get("encoding", ""))).strip().lower()
        if image_format in {"raw_u8", "uint8", "u8", "binary"} and width > 0 and height > 0:
            array = np.frombuffer(raw, dtype=np.uint8)
            return ModuleCDDemoMessageDecoder._normalize_mask_array(array, width=width, height=height)
        decoded = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if decoded is None or decoded.size == 0:
            if width <= 0 or height <= 0:
                return None
            decoded = np.frombuffer(raw, dtype=np.uint8)
        return ModuleCDDemoMessageDecoder._normalize_mask_array(decoded, width=width, height=height)

    @staticmethod
    def _normalize_mask_array(
        array: np.ndarray,
        *,
        width: int = 0,
        height: int = 0,
    ) -> np.ndarray | None:
        mask = np.asarray(array)
        if mask.size == 0:
            return None
        if mask.ndim == 3:
            mask = mask[..., 0]
        if mask.ndim == 1:
            if width <= 0 or height <= 0 or width * height != int(mask.size):
                return None
            mask = mask.reshape((height, width))
        if mask.ndim != 2:
            return None
        mask = mask.astype(np.float32)
        threshold = 0.5 if float(np.nanmax(mask)) <= 1.0 else 127.0
        return (mask > threshold).astype(np.float32)

    def _decode_imu(self, imu_payload: Any) -> tuple[float, float]:
        if not isinstance(imu_payload, dict):
            return self.default_imu_gz, self.default_imu_ax
        payload = imu_payload.get("payload", imu_payload)
        imu = payload.get("Imu", payload.get("imu", payload))
        if not isinstance(imu, dict):
            return self.default_imu_gz, self.default_imu_ax
        gyro = imu.get("gyro", imu.get("angular_velocity", {}))
        accel = imu.get("accel", imu.get("accelerometer", {}))
        gz = float(gyro.get("z", imu.get("gz", self.default_imu_gz)))
        ax = float(accel.get("x", imu.get("ax", self.default_imu_ax)))
        return gz, ax

    def _decode_ego_state(
        self,
        vehicle_states: Any,
        imu_gz: float,
        imu_ax: float,
        timestamp: float,
    ) -> RuntimeEgoState:
        speed_kmh = self.default_speed_kmh
        left_signal = self.default_left_signal
        right_signal = self.default_right_signal
        vehicle_state = self._resolve_vehicle_state(vehicle_states)
        if isinstance(vehicle_state, dict):
            speed_mps = float(vehicle_state.get("speed_mps", speed_kmh / 3.6))
            speed_kmh = speed_mps * 3.6
            turn_signal = str(vehicle_state.get("turn_signal", "off")).strip().lower()
            left_signal = turn_signal in {"left", "hazard"}
            right_signal = turn_signal in {"right", "hazard"}
        return RuntimeEgoState(
            speed_kmh=float(speed_kmh),
            left_signal=bool(left_signal),
            right_signal=bool(right_signal),
            imu_gz=float(imu_gz),
            imu_ax=float(imu_ax),
            timestamp=float(timestamp),
        )

    def _resolve_vehicle_state(self, vehicle_states: Any) -> dict[str, Any] | None:
        if not isinstance(vehicle_states, dict):
            return None
        explicit = vehicle_states.get(self.vehicle_state_id)
        if isinstance(explicit, dict):
            return explicit
        for value in vehicle_states.values():
            if isinstance(value, dict):
                return value
        return None
