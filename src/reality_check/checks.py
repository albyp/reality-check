"""Z checks against DEMs and point clouds."""

from __future__ import annotations

import numpy as np
import rasterio

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
