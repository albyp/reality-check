"""Core data model.

Residual sign convention: measured (dataset) minus surveyed (control point).
A positive dZ means the surface is above the surveyed point.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class Role(str, Enum):
    GCP = "gcp"  # used in processing; residuals are not independent
    CHECKPOINT = "checkpoint"  # independent; gives the true accuracy figure
    UNKNOWN = "unknown"


class DatasetKind(str, Enum):
    ORTHO = "ortho"
    DEM = "dem"
    CLOUD = "cloud"


class CheckKind(str, Enum):
    Z = "z"
    XY = "xy"


class ObsSource(str, Enum):
    AUTO = "auto"
    MANUAL = "manual"


class ObsStatus(str, Enum):
    OK = "ok"
    NOT_FOUND = "not_found"  # target not identifiable in the dataset
    NO_DATA = "no_data"  # inside the extent but no valid data at the point
    OUT_OF_EXTENT = "out_of_extent"


@dataclass
class ControlPoint:
    id: str
    x: float
    y: float
    z: float
    role: Role = Role.UNKNOWN
    enabled: bool = True
    needs_touch_up: bool = False  # target should be repainted in the field
    note: str = ""


@dataclass
class Dataset:
    id: str
    kind: DatasetKind
    path: str


@dataclass
class Observation:
    """One measurement of one control point in one dataset."""

    point_id: str
    dataset_id: str
    check: CheckKind
    status: ObsStatus
    x: float | None = None
    y: float | None = None
    z: float | None = None
    source: ObsSource = ObsSource.AUTO
    confidence: float | None = None
    n_points: int | None = None  # cloud Z: points used
    spread: float | None = None  # cloud Z: robust std dev of points used
    # The automatic result, kept when the user overrides it so it can be restored.
    auto_status: ObsStatus | None = None
    auto_x: float | None = None
    auto_y: float | None = None


@dataclass
class Residual:
    point_id: str
    dataset_id: str
    check: CheckKind
    dx: float | None = None
    dy: float | None = None
    dz: float | None = None

    @property
    def dxy(self) -> float | None:
        if self.dx is None or self.dy is None:
            return None
        return math.hypot(self.dx, self.dy)


def residual(cp: ControlPoint, obs: Observation) -> Residual | None:
    if obs.status is not ObsStatus.OK:
        return None
    if obs.check is CheckKind.Z:
        return Residual(cp.id, obs.dataset_id, obs.check, dz=obs.z - cp.z)
    return Residual(cp.id, obs.dataset_id, obs.check, dx=obs.x - cp.x, dy=obs.y - cp.y)
