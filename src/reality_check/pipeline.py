"""The automatic run: load inputs, check CRS, compute Z checks.

The CLI and the GUI both call run(). Progress messages go to a callback.
Point cloud windows stay in memory (unfiltered) so chips can be rendered on
demand and cloud Z can be recomputed after a class change without
re-reading the cloud.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from reality_check.checks import z_from_dem
from reality_check.cloud import Chunk, cloud_info, sample_windows
from reality_check.control import load_control
from reality_check.crs import CRSLibrary, describe, same_horizontal_crs
from reality_check.models import Dataset, DatasetKind
from reality_check.raster import raster_info
from reality_check.review import recompute_cloud_z
from reality_check.session import Session, Settings

Progress = Callable[[str], None]

MAX_CHIP_SIZE = 10.0  # m; largest chip the GUI offers. Cloud windows cover this.


@dataclass
class RunResult:
    session: Session
    windows: dict[str, dict[str, Chunk]] = field(default_factory=dict)  # cloud dataset id -> point id -> points
    warnings: list[str] = field(default_factory=list)


def make_datasets(ortho: list[str], dem: list[str], cloud: list[str]) -> list[Dataset]:
    out: list[Dataset] = []
    used: set[str] = set()
    for kind, paths in ((DatasetKind.ORTHO, ortho), (DatasetKind.DEM, dem), (DatasetKind.CLOUD, cloud)):
        for p in paths:
            base = f"{kind.value}:{Path(p).stem}"
            ds_id, n = base, 2
            while ds_id in used:
                ds_id, n = f"{base}-{n}", n + 1
            used.add(ds_id)
            out.append(Dataset(ds_id, kind, str(p)))
    return out


def run(
    control_path: str,
    datasets: list[Dataset],
    settings: Settings | None = None,
    crs_library: CRSLibrary | None = None,
    progress: Progress = print,
) -> RunResult:
    settings = settings or Settings()
    points = load_control(control_path)
    progress(f"Loaded {len(points)} control points from {control_path}")
    session = Session(control_path, points, datasets, settings)
    _apply_control_crs(session, crs_library, progress)
    result = RunResult(session)
    result.warnings = check_crs(datasets, progress)

    for ds in datasets:
        if ds.kind is DatasetKind.DEM:
            progress(f"DEM Z check: {Path(ds.path).name}")
            session.observations += z_from_dem(ds.id, ds.path, points)

    result.windows = load_windows(session, progress)
    recompute_cloud_z(session, result.windows)
    return result


def load_windows(session: Session, progress: Progress = print) -> dict[str, dict[str, Chunk]]:
    """Stream each point cloud once and keep the points around every control point."""
    half = max(MAX_CHIP_SIZE / 2, session.settings.cloud_radius)
    out = {}
    for ds in session.datasets:
        if ds.kind is not DatasetKind.CLOUD:
            continue
        progress(f"Reading point cloud: {Path(ds.path).name}")
        sample = sample_windows(ds.path, {p.id: (p.x, p.y) for p in session.points}, half)
        session.cloud_class_counts[ds.id] = sample.class_counts
        progress(f"  {sample.total_points:,} points; classes present: {sorted(sample.class_counts)}")
        out[ds.id] = sample.windows
    return out


def _apply_control_crs(session: Session, lib: CRSLibrary | None, progress: Progress) -> None:
    name = session.settings.control_crs
    if not name:
        return
    lib = lib or CRSLibrary.load()
    entry = lib.entries.get(name)
    if entry is None:
        raise ValueError(f"CRS {name!r} is not in the CRS library ({lib.path})")
    if entry.kind != "local_grid":
        # TODO: datum transforms (e.g. GDA94 <-> GDA2020) with pyproj.
        raise NotImplementedError("Only local_grid entries can be applied to control points so far")
    for p in session.points:
        p.x, p.y, p.z = entry.grid.to_base(p.x, p.y, p.z)
    progress(f"Control points transformed from local grid {name!r} to {entry.grid.base_crs}")


def check_crs(datasets: list[Dataset], progress: Progress = print) -> list[str]:
    """Report each dataset's CRS and warn when they differ."""
    found = []
    for ds in datasets:
        crs = cloud_info(ds.path).crs if ds.kind is DatasetKind.CLOUD else raster_info(ds.path).crs
        found.append((ds, crs))
        progress(f"  {ds.id}: {describe(crs)}")
    msgs = []
    ref = next(((d, c) for d, c in found if c is not None), None)
    for ds, crs in found:
        if crs is None:
            msgs.append(f"{ds.id} has no CRS. It is assumed to match the control points.")
        elif ref and not same_horizontal_crs(ref[1], crs):
            msgs.append(f"{ds.id} CRS ({describe(crs)}) differs from {ref[0].id} ({describe(ref[1])}).")
    for m in msgs:
        progress(f"WARNING: {m}")
    return msgs
