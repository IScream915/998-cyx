from __future__ import annotations

from dataclasses import dataclass
from math import cos, sin


@dataclass(frozen=True)
class CompensatedCenter:
    track_id: int
    original: tuple[float, float]
    compensated: tuple[float, float]
    delta_psi: float


class EgoMotionCompensator:
    def __init__(self, img_w: int, img_h: int):
        self.ox = img_w / 2.0
        self.oy = img_h / 2.0

    def compensate_center(
        self, track_id: int, center: tuple[float, float], gz: float, dt: float
    ) -> CompensatedCenter:
        delta_psi = float(gz) * float(dt)
        cx, cy = center
        dx = cx - self.ox
        dy = cy - self.oy
        cx_ = self.ox + dx * cos(delta_psi) + dy * sin(delta_psi)
        cy_ = self.oy - dx * sin(delta_psi) + dy * cos(delta_psi)
        return CompensatedCenter(
            track_id=track_id,
            original=(float(cx), float(cy)),
            compensated=(float(cx_), float(cy_)),
            delta_psi=delta_psi,
        )

    def compensate_batch(
        self, centers: dict[int, tuple[float, float]], gz: float, dt: float
    ) -> dict[int, tuple[float, float]]:
        return {
            track_id: self.compensate_center(track_id, center, gz, dt).compensated
            for track_id, center in centers.items()
        }
