"""Z checks against DEMs and point clouds."""

from __future__ import annotations

import numpy as np
import rasterio
from rasterio.errors import WindowError
from rasterio.windows import Window, from_bounds

from reality_check.cloud import Chunk
from reality_check.models import CheckKind, ControlPoint, Observation, ObsStatus
from reality_check.raster import in_bounds, sample_bilinear

MIN_CLOUD_POINTS = 3


def z_from_dem(dataset_id: str, path: str, points: list[ControlPoint]) -> list[Observation]:
    out = []
    with rasterio.open(path) as ds:
        for cp in points:
            if not in_bounds(ds, cp.x, cp.y):
                out.append(Observation(cp.id, dataset_id, CheckKind.Z, ObsStatus.OUT_OF_EXTENT))
                continue
            z = sample_bilinear(ds, cp.x, cp.y)
            if z is None:
                out.append(Observation(cp.id, dataset_id, CheckKind.Z, ObsStatus.NO_DATA))
            else:
                out.append(Observation(cp.id, dataset_id, CheckKind.Z, ObsStatus.OK, x=cp.x, y=cp.y, z=z))
    return out


def dem_distance_to_data(ds: rasterio.DatasetReader, x: float, y: float, limit: float) -> float | None:
    """Plan distance from (x, y) to the nearest valid DEM cell centre (0 if (x, y) has a value),
    or None if there is none within limit."""
    if sample_bilinear(ds, x, y) is not None:
        return 0.0
    win = from_bounds(x - limit, y - limit, x + limit, y + limit, ds.transform).round_offsets().round_lengths()
    try:
        win = win.intersection(Window(0, 0, ds.width, ds.height))
    except WindowError:  # the search square misses the raster
        return None
    a = ds.read(1, window=win, masked=True)
    rows, cols = np.nonzero(~np.ma.getmaskarray(a) & np.isfinite(np.ma.filled(a.astype(float), np.nan)))
    if len(rows) == 0:
        return None
    cx, cy = ds.window_transform(win) @ (cols + 0.5, rows + 0.5)
    d = float(np.hypot(np.asarray(cx) - x, np.asarray(cy) - y).min())
    return d if d <= limit else None


def z_from_cloud(dataset_id: str, cp: ControlPoint, window: Chunk, radius: float) -> Observation:
    """Median Z of cloud points within radius (plan distance) of the control point.

    The median resists a few stray points (vegetation, noise). spread is a
    robust standard deviation (1.4826 * MAD) and shows how rough or noisy
    the surface is at the target.

    window may already be class-filtered, so an empty window means no data
    here. The caller decides out-of-extent from the unfiltered window.
    """
    d = np.hypot(window.x - cp.x, window.y - cp.y)
    z = window.z[d <= radius]
    if len(z) < MIN_CLOUD_POINTS:
        return Observation(cp.id, dataset_id, CheckKind.Z, ObsStatus.NO_DATA, n_points=int(len(z)))
    med = float(np.median(z))
    spread = float(1.4826 * np.median(np.abs(z - med)))
    return Observation(
        cp.id, dataset_id, CheckKind.Z, ObsStatus.OK, x=cp.x, y=cp.y, z=med, n_points=int(len(z)), spread=spread
    )
