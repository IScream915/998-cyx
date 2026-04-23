from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from src.alerting.risk_manager import AlertEvent, AlertLevel
from src.prediction.imm_predictor import IMMPredictor, NullPredictor, PolynomialPredictor
from src.tracking.bytetrack_wrapper import TrackedObject
from src.zones.zone_model import BlindSpotZone


@dataclass(frozen=True)
class RuntimeEgoState:
    speed_kmh: float
    left_signal: bool
    right_signal: bool
    imu_gz: float
    imu_ax: float
    timestamp: float


@dataclass(frozen=True)
class RuntimeFrameInput:
    frame_id: int
    timestamp: float
    left_frame: np.ndarray
    right_frame: np.ndarray
    top_frame: np.ndarray | None
    ego_state: RuntimeEgoState
    source_topic: str | None = None
    source_details: dict[str, Any] = field(default_factory=dict)
    left_zone_mask: np.ndarray | None = None
    right_zone_mask: np.ndarray | None = None


PredictorType = IMMPredictor | PolynomialPredictor | NullPredictor


@dataclass(frozen=True)
class RuntimeProcessingResult:
    frame_idx: int
    frame_input: RuntimeFrameInput
    detector_backend: str
    detector_device: str
    fps: float
    dt: float
    tracked_by_side: dict[str, list[TrackedObject]]
    zones: dict[str, BlindSpotZone]
    alerts: dict[str, list[AlertEvent]]
    predictors: dict[str, PredictorType]
    max_alert_level: AlertLevel
