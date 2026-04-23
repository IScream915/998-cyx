from __future__ import annotations

from dataclasses import dataclass, field
from math import atan2, cos, sin, sqrt
from typing import Iterable

from filterpy.kalman import KalmanFilter
import numpy as np
from scipy.stats import multivariate_normal, norm

from src.zones.zone_model import BlindSpotZone


def _gaussian_likelihood(residual: np.ndarray, cov: np.ndarray) -> float:
    cov = np.asarray(cov, dtype=float)
    cov = cov + np.eye(cov.shape[0]) * 1e-6
    try:
        return float(multivariate_normal.pdf(residual, mean=np.zeros_like(residual), cov=cov))
    except Exception:
        return 1e-9


@dataclass
class _LinearModel:
    name: str
    kf: KalmanFilter
    q: np.ndarray

    def copy(self) -> "_LinearModel":
        copied = KalmanFilter(dim_x=self.kf.dim_x, dim_z=self.kf.dim_z)
        copied.x = self.kf.x.copy()
        copied.P = self.kf.P.copy()
        copied.F = self.kf.F.copy()
        copied.H = self.kf.H.copy()
        copied.Q = self.kf.Q.copy()
        copied.R = self.kf.R.copy()
        return _LinearModel(name=self.name, kf=copied, q=self.q.copy())

    def set_dt(self, dt: float) -> None:
        if self.name == "CV":
            self.kf.F = np.array(
                [[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=float
            )
        else:
            dt2 = dt * dt
            self.kf.F = np.array(
                [
                    [1, 0, dt, 0, 0.5 * dt2, 0],
                    [0, 1, 0, dt, 0, 0.5 * dt2],
                    [0, 0, 1, 0, dt, 0],
                    [0, 0, 0, 1, 0, dt],
                    [0, 0, 0, 0, 1, 0],
                    [0, 0, 0, 0, 0, 1],
                ],
                dtype=float,
            )

    def predict(self, dt: float) -> tuple[np.ndarray, np.ndarray]:
        self.set_dt(dt)
        self.kf.predict()
        return self.kf.x.copy(), self.kf.P.copy()

    def update(self, measurement: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        residual = measurement - (self.kf.H @ self.kf.x)
        innovation_cov = self.kf.H @ self.kf.P @ self.kf.H.T + self.kf.R
        self.kf.update(measurement)
        return residual, innovation_cov, self.kf.x.copy()

    def position_mean_cov(self) -> tuple[np.ndarray, np.ndarray]:
        return self.kf.x[:2].copy(), self.kf.P[:2, :2].copy()

    def predict_position(self, horizon_s: float, dt: float) -> tuple[np.ndarray, np.ndarray]:
        model = self.copy()
        steps = max(1, int(round(horizon_s / dt)))
        for _ in range(steps):
            model.predict(dt)
        return model.position_mean_cov()


@dataclass
class _CTRVModel:
    x: np.ndarray
    P: np.ndarray
    Q: np.ndarray
    R: np.ndarray
    initialized: bool = False

    def copy(self) -> "_CTRVModel":
        return _CTRVModel(
            x=self.x.copy(),
            P=self.P.copy(),
            Q=self.Q.copy(),
            R=self.R.copy(),
            initialized=self.initialized,
        )

    def predict(self, dt: float) -> tuple[np.ndarray, np.ndarray]:
        self.x = self._transition(self.x, dt)
        self.P = self._jacobian(self.x, dt) @ self.P @ self._jacobian(self.x, dt).T + self.Q
        return self.x.copy(), self.P.copy()

    def update(self, measurement: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        H = np.array([[1, 0, 0, 0, 0], [0, 1, 0, 0, 0]], dtype=float)
        residual = measurement - (H @ self.x)
        innovation_cov = H @ self.P @ H.T + self.R
        K = self.P @ H.T @ np.linalg.inv(innovation_cov)
        self.x = self.x + K @ residual
        self.P = (np.eye(5) - K @ H) @ self.P
        self.initialized = True
        return residual, innovation_cov, self.x.copy()

    def position_mean_cov(self) -> tuple[np.ndarray, np.ndarray]:
        return self.x[:2].copy(), self.P[:2, :2].copy()

    def predict_position(self, horizon_s: float, dt: float) -> tuple[np.ndarray, np.ndarray]:
        model = self.copy()
        steps = max(1, int(round(horizon_s / dt)))
        for _ in range(steps):
            model.predict(dt)
        return model.position_mean_cov()

    @staticmethod
    def _transition(state: np.ndarray, dt: float) -> np.ndarray:
        cx, cy, v, theta, omega = state
        if abs(omega) > 1e-3:
            cx = cx + (v / omega) * (sin(theta + omega * dt) - sin(theta))
            cy = cy + (v / omega) * (cos(theta) - cos(theta + omega * dt))
        else:
            cx = cx + v * cos(theta) * dt
            cy = cy + v * sin(theta) * dt
        theta = theta + omega * dt
        return np.array([cx, cy, v, theta, omega], dtype=float)

    @staticmethod
    def _jacobian(state: np.ndarray, dt: float) -> np.ndarray:
        _, _, v, theta, omega = state
        F = np.eye(5, dtype=float)
        if abs(omega) > 1e-3:
            wt = theta + omega * dt
            F[0, 2] = (sin(wt) - sin(theta)) / omega
            F[0, 3] = (v / omega) * (cos(wt) - cos(theta))
            F[0, 4] = (
                v * (omega * dt * cos(wt) - sin(wt) + sin(theta)) / (omega ** 2)
            )
            F[1, 2] = (cos(theta) - cos(wt)) / omega
            F[1, 3] = (v / omega) * (sin(wt) - sin(theta))
            F[1, 4] = (
                v * (omega * dt * sin(wt) + cos(wt) - cos(theta)) / (omega ** 2)
            )
        else:
            F[0, 2] = cos(theta) * dt
            F[0, 3] = -v * sin(theta) * dt
            F[1, 2] = sin(theta) * dt
            F[1, 3] = v * cos(theta) * dt
        F[3, 4] = dt
        return F


@dataclass
class TrackIMMState:
    track_id: int
    model_weights: np.ndarray
    cv: _LinearModel
    ca: _LinearModel
    ctrv: _CTRVModel
    history_len: int = 0
    last_timestamp: float = 0.0
    history: list[tuple[float, float, float]] = field(default_factory=list)


class IMMPredictor:
    def __init__(self, config: dict):
        pred_cfg = config["prediction"]
        self.pred_cfg = pred_cfg
        self.transition = np.asarray(pred_cfg["markov"]["transition"], dtype=float)
        self.initial_weights = np.asarray(pred_cfg["markov"]["initial_weights"], dtype=float)
        self.history_len_limit = int(pred_cfg["history_len"])
        self.min_history = int(pred_cfg["min_history"])
        self.full_conf_frames = int(pred_cfg["full_conf_frames"])
        self.horizons = [float(v) for v in pred_cfg["horizons_s"]]
        self.default_dt = float(config["carla"]["fixed_delta_seconds"])
        self.track_states: dict[int, TrackIMMState] = {}
        self.config = config

    def update(self, track_id: int, compensated_center: tuple[float, float], timestamp: float) -> None:
        measurement = np.array(compensated_center, dtype=float)
        if track_id not in self.track_states:
            self.track_states[track_id] = self._init_track(track_id, measurement, timestamp)
            return

        state = self.track_states[track_id]
        dt = max(1e-3, float(timestamp - state.last_timestamp))
        state.last_timestamp = float(timestamp)
        state.history.append((measurement[0], measurement[1], float(timestamp)))
        state.history = state.history[-self.history_len_limit :]
        state.history_len += 1

        accel_mag = 0.0
        omega = state.ctrv.x[4]
        if len(state.history) >= 2:
            vx = (state.history[-1][0] - state.history[-2][0]) / dt
            vy = (state.history[-1][1] - state.history[-2][1]) / dt
            speed = sqrt(vx ** 2 + vy ** 2)
            theta = atan2(vy, vx) if speed > 1e-6 else state.ctrv.x[3]
            omega = 0.0
            if len(state.history) >= 3:
                prev_vx = (state.history[-2][0] - state.history[-3][0]) / max(
                    1e-3, state.history[-2][2] - state.history[-3][2]
                )
                prev_vy = (state.history[-2][1] - state.history[-3][1]) / max(
                    1e-3, state.history[-2][2] - state.history[-3][2]
                )
                prev_theta = atan2(prev_vy, prev_vx) if abs(prev_vx) + abs(prev_vy) > 1e-6 else theta
                omega = (theta - prev_theta) / dt
                accel_mag = sqrt((vx - prev_vx) ** 2 + (vy - prev_vy) ** 2) / dt
            state.ctrv.x[2] = speed
            state.ctrv.x[3] = theta
            state.ctrv.x[4] = omega

        models = [state.cv, state.ca, state.ctrv]
        prior = state.model_weights @ self.transition
        likelihoods = []
        for model in models:
            model.predict(dt)
            residual, innovation_cov, _ = model.update(measurement)
            likelihoods.append(max(_gaussian_likelihood(residual, innovation_cov), 1e-9))

        motion_bias = np.ones(3, dtype=float)
        abs_omega = abs(omega)
        if abs_omega < 0.1 and accel_mag < 5.0:
            motion_bias[0] = 20.0
            motion_bias[1] = 0.5
            motion_bias[2] = 0.01
        elif abs_omega > 0.25:
            motion_bias[0] = 0.2
            motion_bias[1] = 0.8
            motion_bias[2] = min(20.0, 2.0 + abs_omega * 8.0)
        elif accel_mag > 10.0:
            motion_bias[0] = 0.7
            motion_bias[1] = min(3.0, 1.0 + accel_mag / 10.0)
            motion_bias[2] = 0.8

        posterior = prior * np.asarray(likelihoods, dtype=float) * motion_bias
        posterior_sum = posterior.sum()
        if posterior_sum <= 0:
            posterior = np.full_like(posterior, 1.0 / len(posterior))
        else:
            posterior = posterior / posterior_sum
        state.model_weights = posterior

    def predict_position(self, track_id: int, horizon_s: float, dt: float | None = None) -> tuple[float, float]:
        mean, _ = self._predict_distribution(track_id, horizon_s, dt or self.default_dt)
        return float(mean[0]), float(mean[1])

    def predict_risk(self, track_id: int, zone: BlindSpotZone, dt: float) -> float:
        state = self.track_states.get(track_id)
        if state is None or state.history_len < self.min_history:
            return 0.0
        x_min = float(zone.polygon_px[:, 0].min())
        x_max = float(zone.polygon_px[:, 0].max())
        y_min = float(zone.polygon_px[:, 1].min())
        y_max = float(zone.polygon_px[:, 1].max())
        risks = []
        for horizon in self.horizons:
            mean, cov = self._predict_distribution(track_id, horizon, dt)
            sigma_x = max(sqrt(max(cov[0, 0], 1e-6)), 1e-3)
            sigma_y = max(sqrt(max(cov[1, 1], 1e-6)), 1e-3)
            px = norm.cdf(x_max, mean[0], sigma_x) - norm.cdf(x_min, mean[0], sigma_x)
            py = norm.cdf(y_max, mean[1], sigma_y) - norm.cdf(y_min, mean[1], sigma_y)
            dx = 0.0 if x_min <= mean[0] <= x_max else min(abs(mean[0] - x_min), abs(mean[0] - x_max))
            dy = 0.0 if y_min <= mean[1] <= y_max else min(abs(mean[1] - y_min), abs(mean[1] - y_max))
            closeness = float(np.exp(-(dx + dy) / 120.0))
            risks.append(max(0.0, min(1.0, float(px * py * closeness))))
        return max(risks) * self.get_prediction_confidence(track_id)

    def get_model_weights(self, track_id: int) -> dict[str, float]:
        state = self.track_states.get(track_id)
        if state is None:
            return {"CV": 0.0, "CA": 0.0, "CTRV": 0.0}
        return {
            "CV": float(state.model_weights[0]),
            "CA": float(state.model_weights[1]),
            "CTRV": float(state.model_weights[2]),
        }

    def get_prediction_confidence(self, track_id: int) -> float:
        state = self.track_states.get(track_id)
        if state is None:
            return 0.0
        return min(1.0, state.history_len / max(1, self.full_conf_frames))

    def prune_lost_tracks(self, active_ids: set[int]) -> None:
        for track_id in list(self.track_states):
            if track_id not in active_ids:
                del self.track_states[track_id]

    def _predict_distribution(self, track_id: int, horizon_s: float, dt: float) -> tuple[np.ndarray, np.ndarray]:
        state = self.track_states[track_id]
        models = [state.cv, state.ca, state.ctrv]
        means = []
        covs = []
        for model in models:
            mean, cov = model.predict_position(horizon_s, dt)
            means.append(mean)
            covs.append(cov)
        weights = state.model_weights
        fused_mean = sum(weight * mean for weight, mean in zip(weights, means))
        fused_cov = np.zeros((2, 2), dtype=float)
        for weight, mean, cov in zip(weights, means, covs):
            diff = (mean - fused_mean).reshape(2, 1)
            fused_cov += weight * (cov + diff @ diff.T)
        return fused_mean, fused_cov

    def _init_track(
        self, track_id: int, measurement: np.ndarray, timestamp: float
    ) -> TrackIMMState:
        cv = self._create_cv_filter(measurement)
        ca = self._create_ca_filter(measurement)
        ctrv = self._create_ctrv_filter(measurement)
        return TrackIMMState(
            track_id=track_id,
            model_weights=self.initial_weights.copy(),
            cv=cv,
            ca=ca,
            ctrv=ctrv,
            history_len=1,
            last_timestamp=float(timestamp),
            history=[(float(measurement[0]), float(measurement[1]), float(timestamp))],
        )

    def _create_cv_filter(self, measurement: np.ndarray) -> _LinearModel:
        cv_cfg = self.config["prediction"]["cv"]
        kf = KalmanFilter(dim_x=4, dim_z=2)
        kf.x = np.array([measurement[0], measurement[1], 0.0, 0.0], dtype=float)
        kf.H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]], dtype=float)
        kf.P = np.eye(4, dtype=float) * 10.0
        kf.R = np.eye(2, dtype=float) * float(cv_cfg["meas_noise"])
        q = np.diag(
            [
                float(cv_cfg["process_noise_pos"]),
                float(cv_cfg["process_noise_pos"]),
                float(cv_cfg["process_noise_vel"]),
                float(cv_cfg["process_noise_vel"]),
            ]
        )
        kf.Q = q.copy()
        return _LinearModel("CV", kf, q)

    def _create_ca_filter(self, measurement: np.ndarray) -> _LinearModel:
        ca_cfg = self.config["prediction"]["ca"]
        kf = KalmanFilter(dim_x=6, dim_z=2)
        kf.x = np.array([measurement[0], measurement[1], 0.0, 0.0, 0.0, 0.0], dtype=float)
        kf.H = np.array([[1, 0, 0, 0, 0, 0], [0, 1, 0, 0, 0, 0]], dtype=float)
        kf.P = np.eye(6, dtype=float) * 10.0
        kf.R = np.eye(2, dtype=float) * float(ca_cfg["meas_noise"])
        q = np.diag(
            [
                float(ca_cfg["process_noise_pos"]),
                float(ca_cfg["process_noise_pos"]),
                float(ca_cfg["process_noise_vel"]),
                float(ca_cfg["process_noise_vel"]),
                float(ca_cfg["process_noise_acc"]),
                float(ca_cfg["process_noise_acc"]),
            ]
        )
        kf.Q = q.copy()
        return _LinearModel("CA", kf, q)

    def _create_ctrv_filter(self, measurement: np.ndarray) -> _CTRVModel:
        ctrv_cfg = self.config["prediction"]["ctrv"]
        q = np.diag(
            [
                float(self.config["prediction"]["cv"]["process_noise_pos"]),
                float(self.config["prediction"]["cv"]["process_noise_pos"]),
                float(ctrv_cfg["process_noise_vel"]),
                float(ctrv_cfg["process_noise_yaw"]),
                float(ctrv_cfg["process_noise_omega"]),
            ]
        )
        r = np.eye(2, dtype=float) * float(ctrv_cfg["meas_noise"])
        return _CTRVModel(
            x=np.array([measurement[0], measurement[1], 0.0, 0.0, 0.0], dtype=float),
            P=np.eye(5, dtype=float) * 10.0,
            Q=q,
            R=r,
            initialized=False,
        )


@dataclass
class PolynomialTrackState:
    track_id: int
    history: list[tuple[float, float, float]] = field(default_factory=list)


class PolynomialPredictor:
    def __init__(self, config: dict):
        pred_cfg = config["prediction"]
        self.history_len_limit = int(pred_cfg["history_len"])
        self.min_history = int(pred_cfg["min_history"])
        self.full_conf_frames = int(pred_cfg["full_conf_frames"])
        self.horizons = [float(v) for v in pred_cfg["horizons_s"]]
        self.track_states: dict[int, PolynomialTrackState] = {}
        self.default_dt = float(config["carla"]["fixed_delta_seconds"])

    def update(self, track_id: int, compensated_center: tuple[float, float], timestamp: float) -> None:
        state = self.track_states.setdefault(track_id, PolynomialTrackState(track_id))
        state.history.append((float(compensated_center[0]), float(compensated_center[1]), float(timestamp)))
        state.history = state.history[-self.history_len_limit :]

    def predict_position(self, track_id: int, horizon_s: float, dt: float | None = None) -> tuple[float, float]:
        state = self.track_states[track_id]
        if len(state.history) == 1:
            return state.history[-1][0], state.history[-1][1]
        xs = np.array([item[0] for item in state.history], dtype=float)
        ys = np.array([item[1] for item in state.history], dtype=float)
        ts = np.array([item[2] for item in state.history], dtype=float)
        ts = ts - ts[-1]
        t = float(horizon_s)
        if len(ts) == 2 or np.allclose(ts, ts[0]):
            dt_local = max(1e-3, ts[-1] - ts[-2]) if len(ts) >= 2 else self.default_dt
            vx = (xs[-1] - xs[-2]) / dt_local
            vy = (ys[-1] - ys[-2]) / dt_local
            return float(xs[-1] + vx * t), float(ys[-1] + vy * t)
        degree = 2 if len(ts) >= 3 else 1
        try:
            coef_x = np.polyfit(ts, xs, deg=degree)
            coef_y = np.polyfit(ts, ys, deg=degree)
            return float(np.polyval(coef_x, t)), float(np.polyval(coef_y, t))
        except np.linalg.LinAlgError:
            dt_local = max(1e-3, ts[-1] - ts[-2]) if len(ts) >= 2 else self.default_dt
            vx = (xs[-1] - xs[-2]) / dt_local
            vy = (ys[-1] - ys[-2]) / dt_local
            return float(xs[-1] + vx * t), float(ys[-1] + vy * t)

    def predict_risk(self, track_id: int, zone: BlindSpotZone, dt: float) -> float:
        state = self.track_states.get(track_id)
        if state is None or len(state.history) < self.min_history:
            return 0.0
        x_min = float(zone.polygon_px[:, 0].min())
        x_max = float(zone.polygon_px[:, 0].max())
        y_min = float(zone.polygon_px[:, 1].min())
        y_max = float(zone.polygon_px[:, 1].max())
        risks = []
        for horizon in self.horizons:
            cx, cy = self.predict_position(track_id, horizon, dt)
            if x_min <= cx <= x_max and y_min <= cy <= y_max:
                risks.append(0.95)
            else:
                dx = 0.0 if x_min <= cx <= x_max else min(abs(cx - x_min), abs(cx - x_max))
                dy = 0.0 if y_min <= cy <= y_max else min(abs(cy - y_min), abs(cy - y_max))
                risks.append(float(np.exp(-(dx + dy) / 100.0)))
        return max(risks) * self.get_prediction_confidence(track_id)

    def get_model_weights(self, track_id: int) -> dict[str, float]:
        return {"POLY": 1.0}

    def get_prediction_confidence(self, track_id: int) -> float:
        state = self.track_states.get(track_id)
        if state is None:
            return 0.0
        return min(1.0, len(state.history) / max(1, self.full_conf_frames))

    def prune_lost_tracks(self, active_ids: set[int]) -> None:
        for track_id in list(self.track_states):
            if track_id not in active_ids:
                del self.track_states[track_id]


class NullPredictor:
    def __init__(self, config: dict):
        self.default_dt = float(config["carla"]["fixed_delta_seconds"])
        self.track_states: dict[int, tuple[float, float]] = {}

    def update(self, track_id: int, compensated_center: tuple[float, float], timestamp: float) -> None:
        del timestamp
        self.track_states[track_id] = (float(compensated_center[0]), float(compensated_center[1]))

    def predict_position(self, track_id: int, horizon_s: float, dt: float | None = None) -> tuple[float, float]:
        del horizon_s, dt
        return self.track_states.get(track_id, (0.0, 0.0))

    def predict_risk(self, track_id: int, zone: BlindSpotZone, dt: float) -> float:
        del track_id, zone, dt
        return 0.0

    def get_model_weights(self, track_id: int) -> dict[str, float]:
        del track_id
        return {"NONE": 1.0}

    def get_prediction_confidence(self, track_id: int) -> float:
        del track_id
        return 0.0

    def prune_lost_tracks(self, active_ids: set[int]) -> None:
        for track_id in list(self.track_states):
            if track_id not in active_ids:
                del self.track_states[track_id]
