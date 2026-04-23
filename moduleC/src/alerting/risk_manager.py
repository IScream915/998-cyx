from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from src.tracking.bytetrack_wrapper import TrackedObject
from src.zones.zone_model import BlindSpotZone


class AlertLevel(IntEnum):
    SAFE = 0
    WARNING = 1
    DANGER = 2


@dataclass
class AlertEvent:
    track_id: int
    camera_side: str
    level: AlertLevel
    r_score: float
    time_to_entry_s: float | None
    bbox: tuple[int, int, int, int]
    model_weights: dict[str, float]


@dataclass
class _LatchedAlertState:
    event: AlertEvent
    remaining_frames: int


class RiskManager:
    def __init__(self, config: dict[str, Any]):
        alert_cfg = config["alerting"]
        self.warn_thresh = float(alert_cfg["warning_risk_thresh"])
        self.danger_hyst = int(alert_cfg["danger_hysteresis"])
        self.min_pred_conf = float(alert_cfg["min_pred_confidence"])
        self.min_object_conf = float(alert_cfg.get("min_object_confidence", 0.0))
        self.min_alert_bbox_area_px2 = float(
            alert_cfg.get("min_alert_bbox_area_px2", 0.0)
        )
        self.min_alert_bbox_width_px = float(
            alert_cfg.get("min_alert_bbox_width_px", 0.0)
        )
        self.min_alert_bbox_height_px = float(
            alert_cfg.get("min_alert_bbox_height_px", 0.0)
        )
        self.warning_hyst = max(1, int(alert_cfg.get("warning_hysteresis", 1)))
        self.warning_min_track_age = max(1, int(alert_cfg.get("warning_min_track_age", 1)))
        self.danger_min_track_age = max(1, int(alert_cfg.get("danger_min_track_age", 1)))
        self.warning_repeat_interval = max(
            0, int(alert_cfg.get("warning_repeat_interval_frames", 0))
        )
        self.danger_repeat_interval = max(
            0, int(alert_cfg.get("danger_repeat_interval_frames", 0))
        )
        self.stationary_single_alert_enabled = bool(
            alert_cfg.get("stationary_single_alert_enabled", True)
        )
        self.stationary_speed_threshold_kmh = float(
            alert_cfg.get("stationary_speed_threshold_kmh", 1.0)
        )
        self.warning_display_hold_frames = max(
            0, int(alert_cfg.get("warning_display_hold_frames", 12))
        )
        self.danger_display_hold_frames = max(
            0, int(alert_cfg.get("danger_display_hold_frames", 16))
        )
        self.display_repeat_extend_frames = max(
            0, int(alert_cfg.get("display_repeat_extend_frames", 6))
        )
        self.display_max_hold_frames = max(
            self.warning_display_hold_frames,
            self.danger_display_hold_frames,
            int(alert_cfg.get("display_max_hold_frames", 40)),
        )
        self._in_zone_counter: dict[int, int] = {}
        self._seen_counter: dict[int, int] = {}
        self._warning_counter: dict[int, int] = {}
        self._emit_cooldown: dict[tuple[int, AlertLevel], int] = {}
        self._stationary_alerted_levels: dict[int, AlertLevel] = {}
        self._active_alerts: dict[int, _LatchedAlertState] = {}

    def evaluate(
        self,
        tracked: list[TrackedObject],
        zone: BlindSpotZone,
        predictor: Any,
        camera_side: str,
        dt: float,
        ego_speed_kmh: float,
    ) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        active_ids = {obj.track_id for obj in tracked}
        stationary = self._is_stationary(ego_speed_kmh)
        if not stationary:
            self._stationary_alerted_levels.clear()
        for track_id in list(self._in_zone_counter):
            if track_id not in active_ids:
                del self._in_zone_counter[track_id]
        for track_id in list(self._seen_counter):
            if track_id not in active_ids:
                del self._seen_counter[track_id]
        for track_id in list(self._warning_counter):
            if track_id not in active_ids:
                del self._warning_counter[track_id]
        for key in list(self._emit_cooldown):
            track_id, _ = key
            if track_id not in active_ids:
                del self._emit_cooldown[key]
        for track_id in list(self._stationary_alerted_levels):
            if track_id not in active_ids:
                del self._stationary_alerted_levels[track_id]

        emitted_this_frame: set[tuple[int, AlertLevel]] = set()

        for obj in tracked:
            self._seen_counter[obj.track_id] = self._seen_counter.get(obj.track_id, 0) + 1
            track_age = self._seen_counter[obj.track_id]
            if not self._is_alert_bbox_eligible(obj):
                self._in_zone_counter[obj.track_id] = 0
                self._warning_counter[obj.track_id] = 0
                continue
            in_zone_now = zone.contains_bbox_bottom_center(*obj.bbox)
            if in_zone_now:
                self._in_zone_counter[obj.track_id] = self._in_zone_counter.get(obj.track_id, 0) + 1
            else:
                self._in_zone_counter[obj.track_id] = 0

            risk = predictor.predict_risk(obj.track_id, zone, dt)
            weights = predictor.get_model_weights(obj.track_id)
            if (
                track_age >= self.danger_min_track_age
                and self._in_zone_counter.get(obj.track_id, 0) >= self.danger_hyst
            ):
                danger_event = AlertEvent(
                    track_id=obj.track_id,
                    camera_side=camera_side,
                    level=AlertLevel.DANGER,
                    r_score=risk,
                    time_to_entry_s=None,
                    bbox=obj.bbox,
                    model_weights=weights,
                )
                self._refresh_active_alert(danger_event)
                if self._should_emit(danger_event.level, obj.track_id, stationary=stationary):
                    events.append(danger_event)
                    self._set_emit_cooldown(danger_event.level, obj.track_id)
                    self._mark_emitted(danger_event.level, obj.track_id, stationary=stationary)
                    emitted_this_frame.add((obj.track_id, danger_event.level))
                continue

            if in_zone_now:
                self._warning_counter[obj.track_id] = 0
                continue

            pred_conf = (
                predictor.get_prediction_confidence(obj.track_id)
                if hasattr(predictor, "get_prediction_confidence")
                else 1.0
            )
            warning_ready = (
                track_age >= self.warning_min_track_age
                and obj.confidence >= self.min_object_conf
                and pred_conf >= self.min_pred_conf
                and risk >= self.warn_thresh
            )
            if warning_ready:
                self._warning_counter[obj.track_id] = self._warning_counter.get(obj.track_id, 0) + 1
            else:
                self._warning_counter[obj.track_id] = 0

            if self._warning_counter.get(obj.track_id, 0) >= self.warning_hyst:
                warning_event = AlertEvent(
                    track_id=obj.track_id,
                    camera_side=camera_side,
                    level=AlertLevel.WARNING,
                    r_score=risk,
                    time_to_entry_s=self._estimate_tte(risk),
                    bbox=obj.bbox,
                    model_weights=weights,
                )
                self._refresh_active_alert(warning_event)
                if self._should_emit(warning_event.level, obj.track_id, stationary=stationary):
                    events.append(warning_event)
                    self._set_emit_cooldown(warning_event.level, obj.track_id)
                    self._mark_emitted(warning_event.level, obj.track_id, stationary=stationary)
                    emitted_this_frame.add((obj.track_id, warning_event.level))
        for key in list(self._emit_cooldown):
            if key in emitted_this_frame:
                continue
            self._emit_cooldown[key] = max(0, self._emit_cooldown[key] - 1)
            if self._emit_cooldown[key] == 0:
                del self._emit_cooldown[key]
        self._decay_active_alerts()
        return events

    def active_alerts(self) -> list[AlertEvent]:
        return [
            state.event
            for state in sorted(
                self._active_alerts.values(),
                key=lambda state: (-int(state.event.level), int(state.event.track_id)),
            )
        ]

    def _should_emit(self, level: AlertLevel, track_id: int, *, stationary: bool) -> bool:
        if stationary:
            previous_level = self._stationary_alerted_levels.get(track_id, AlertLevel.SAFE)
            if previous_level >= level:
                return False
        return self._emit_cooldown.get((track_id, level), 0) <= 0

    def _set_emit_cooldown(self, level: AlertLevel, track_id: int) -> None:
        interval = (
            self.danger_repeat_interval
            if level == AlertLevel.DANGER
            else self.warning_repeat_interval
        )
        if interval > 0:
            self._emit_cooldown[(track_id, level)] = interval

    def _mark_emitted(
        self,
        level: AlertLevel,
        track_id: int,
        *,
        stationary: bool,
    ) -> None:
        if stationary:
            previous_level = self._stationary_alerted_levels.get(track_id, AlertLevel.SAFE)
            if level > previous_level:
                self._stationary_alerted_levels[track_id] = level

    def _refresh_active_alert(self, event: AlertEvent) -> None:
        base_hold = self._display_hold_frames(event.level)
        if base_hold <= 0:
            return
        current = self._active_alerts.get(event.track_id)
        if current is None:
            self._active_alerts[event.track_id] = _LatchedAlertState(
                event=event,
                remaining_frames=base_hold,
            )
            return

        if event.level < current.event.level:
            return

        remaining_frames = max(base_hold, current.remaining_frames)
        if event.level == current.event.level:
            remaining_frames += self.display_repeat_extend_frames
        else:
            remaining_frames = max(
                remaining_frames,
                base_hold + self.display_repeat_extend_frames,
            )
        self._active_alerts[event.track_id] = _LatchedAlertState(
            event=event,
            remaining_frames=min(self.display_max_hold_frames, remaining_frames),
        )

    def _decay_active_alerts(self) -> None:
        for track_id in list(self._active_alerts):
            state = self._active_alerts[track_id]
            state.remaining_frames = max(0, state.remaining_frames - 1)
            if state.remaining_frames == 0:
                del self._active_alerts[track_id]

    def _display_hold_frames(self, level: AlertLevel) -> int:
        if level == AlertLevel.DANGER:
            return self.danger_display_hold_frames
        if level == AlertLevel.WARNING:
            return self.warning_display_hold_frames
        return 0

    def _is_alert_bbox_eligible(self, obj: TrackedObject) -> bool:
        x1, y1, x2, y2 = obj.bbox
        width = float(max(0, x2 - x1))
        height = float(max(0, y2 - y1))
        area = float(obj.area)
        if area < self.min_alert_bbox_area_px2:
            return False
        if width < self.min_alert_bbox_width_px:
            return False
        if height < self.min_alert_bbox_height_px:
            return False
        return True

    def global_level(self, alerts_by_side: dict[str, list[AlertEvent]]) -> AlertLevel:
        highest = AlertLevel.SAFE
        for events in alerts_by_side.values():
            for event in events:
                if event.level > highest:
                    highest = event.level
        return highest

    def _estimate_tte(self, r_score: float) -> float:
        normalized = (r_score - self.warn_thresh) / max(1e-6, 1.0 - self.warn_thresh)
        return float(max(1.0, min(2.0, 2.0 - normalized)))

    def _is_stationary(self, ego_speed_kmh: float) -> bool:
        if not self.stationary_single_alert_enabled:
            return False
        return float(ego_speed_kmh) <= self.stationary_speed_threshold_kmh
