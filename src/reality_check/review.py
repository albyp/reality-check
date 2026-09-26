"""Review actions on a session: manual XY adjust, not found, reset, tolerances.

The automatic run comes first. These functions apply the user's corrections.
An override keeps the automatic result on the observation so reset can
restore it.
"""

from __future__ import annotations

from dataclasses import dataclass

from reality_check.checks import z_from_cloud
from reality_check.cloud import Chunk, filter_classes
from reality_check.models import (
    CheckKind,
    ControlPoint,
    DatasetKind,
    Observation,
    ObsSource,
    ObsStatus,
    residual,
)
from reality_check.session import Session

XY_KINDS = (DatasetKind.ORTHO, DatasetKind.CLOUD)
_KIND_NAME = {DatasetKind.ORTHO: "orthomosaic", DatasetKind.DEM: "DEM", DatasetKind.CLOUD: "point cloud"}


def find_obs(session: Session, pid: str, ds_id: str, check: CheckKind) -> Observation | None:
    return next(
        (o for o in session.observations if o.point_id == pid and o.dataset_id == ds_id and o.check is check), None
    )


def _keep_auto(o: Observation) -> None:
    if o.source is ObsSource.AUTO:
        o.auto_status, o.auto_x, o.auto_y = o.status, o.x, o.y


def set_manual_xy(session: Session, pid: str, ds_id: str, x: float, y: float) -> Observation:
    o = find_obs(session, pid, ds_id, CheckKind.XY)
    if o is None:
        o = Observation(pid, ds_id, CheckKind.XY, ObsStatus.OK)
        session.observations.append(o)
    else:
        _keep_auto(o)
    o.status, o.x, o.y, o.source, o.confidence = ObsStatus.OK, x, y, ObsSource.MANUAL, None
    return o


def set_not_found(session: Session, pid: str, ds_id: str) -> Observation:
    o = find_obs(session, pid, ds_id, CheckKind.XY)
    if o is None:
        o = Observation(pid, ds_id, CheckKind.XY, ObsStatus.NOT_FOUND, source=ObsSource.MANUAL)
        session.observations.append(o)
        return o
    _keep_auto(o)
    o.status, o.x, o.y, o.source = ObsStatus.NOT_FOUND, None, None, ObsSource.MANUAL
    return o


def reset_xy(session: Session, pid: str, ds_id: str) -> None:
    """Undo a manual XY result: restore the automatic one, or remove it if there was none."""
    o = find_obs(session, pid, ds_id, CheckKind.XY)
    if o is None or o.source is ObsSource.AUTO:
        return
    if o.auto_status is None:
        session.observations.remove(o)
        return
    o.status, o.x, o.y, o.source = o.auto_status, o.auto_x, o.auto_y, ObsSource.AUTO
    o.auto_status = o.auto_x = o.auto_y = None


def recompute_cloud_z(session: Session, windows: dict[str, dict[str, Chunk]]) -> None:
    """Recompute cloud Z after a class or radius change. windows are unfiltered."""
    classes = set(session.settings.cloud_classes) if session.settings.cloud_classes is not None else None
    cloud_ids = {ds.id for ds in session.datasets if ds.kind is DatasetKind.CLOUD}
    session.observations = [
        o for o in session.observations if not (o.dataset_id in cloud_ids and o.check is CheckKind.Z)
    ]
    for ds_id in cloud_ids:
        for p in session.points:
            raw = windows[ds_id][p.id]
            if len(raw) == 0:
                session.observations.append(Observation(p.id, ds_id, CheckKind.Z, ObsStatus.OUT_OF_EXTENT))
            else:
                session.observations.append(
                    z_from_cloud(ds_id, p, filter_classes(raw, classes), session.settings.cloud_radius)
                )


@dataclass
class PointResult:
    """One control point's residuals across all datasets, with tolerance checks."""

    point: ControlPoint
    dz: dict[str, float | None]  # dataset id -> dZ
    dxy: dict[str, float | None]  # dataset id -> dXY
    dx: dict[str, float | None]
    dy: dict[str, float | None]
    status: dict[tuple[str, CheckKind], Observation]
    exceeds: list[str]  # human-readable tolerance failures

    @property
    def manual(self) -> bool:
        return any(o.source is ObsSource.MANUAL for o in self.status.values())

    @property
    def passes(self) -> bool | None:
        """None if no tolerance is set or nothing was measured."""
        return None if not self.point.enabled else not self.exceeds


def point_results(session: Session) -> list[PointResult]:
    s = session.settings
    names = {d.id: _KIND_NAME[d.kind] for d in session.datasets}
    out = []
    for cp in session.points:
        dz, dxy, dx, dy, status, exceeds = {}, {}, {}, {}, {}, []
        for o in session.observations:
            if o.point_id != cp.id:
                continue
            status[(o.dataset_id, o.check)] = o
            r = residual(cp, o)
            if r is None:
                continue
            if o.check is CheckKind.Z:
                dz[o.dataset_id] = r.dz
                if s.tol_z is not None and abs(r.dz) > s.tol_z:
                    exceeds.append(f"dZ {r.dz * 1000:+.0f} mm in the {names.get(o.dataset_id, o.dataset_id)}")
            else:
                dxy[o.dataset_id], dx[o.dataset_id], dy[o.dataset_id] = r.dxy, r.dx, r.dy
                if s.tol_xy is not None and r.dxy > s.tol_xy:
                    exceeds.append(f"dXY {r.dxy * 1000:.0f} mm in the {names.get(o.dataset_id, o.dataset_id)}")
        out.append(PointResult(cp, dz, dxy, dx, dy, status, exceeds))
    return out
