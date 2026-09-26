"""Raster access (orthomosaic, DEM). All reads are windowed; files are never loaded whole."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import rasterio
from pyproj import CRS
from rasterio.enums import Resampling
from rasterio.windows import Window, from_bounds


@dataclass
class RasterInfo:
    path: str
    width: int
    height: int
    count: int
    dtype: str
    res: tuple[float, float]
    bounds: tuple[float, float, float, float]
    nodata: float | None
    crs: CRS | None


def raster_info(path: str) -> RasterInfo:
    with rasterio.open(path) as ds:
        return RasterInfo(
            path=path,
            width=ds.width,
            height=ds.height,
            count=ds.count,
            dtype=ds.dtypes[0],
            res=ds.res,
            bounds=tuple(ds.bounds),
            nodata=ds.nodata,
            crs=CRS.from_wkt(ds.crs.to_wkt()) if ds.crs else None,
        )


def sample_bilinear(ds: rasterio.DatasetReader, x: float, y: float, band: int = 1) -> float | None:
    """Bilinear sample at (x, y). Returns None outside the raster or on nodata.

    Pixel values are taken at pixel centres. If some of the four neighbours
    are nodata (or off the raster edge), the valid ones are reweighted.
    """
    if not in_bounds(ds, x, y):
        return None
    col, row = ~ds.transform @ (x, y)
    col -= 0.5
    row -= 0.5
    c0, r0 = math.floor(col), math.floor(row)
    fc, fr = col - c0, row - r0
    q = ds.read(band, window=Window(c0, r0, 2, 2), boundless=True, masked=True)
    q = np.ma.filled(q.astype(float), np.nan)
    w = np.array([[(1 - fc) * (1 - fr), fc * (1 - fr)], [(1 - fc) * fr, fc * fr]])
    valid = ~np.isnan(q)
    if not valid.any() or w[valid].sum() < 1e-9:
        return None
    return float((q[valid] * w[valid]).sum() / w[valid].sum())


def in_bounds(ds: rasterio.DatasetReader, x: float, y: float) -> bool:
    b = ds.bounds
    return b.left <= x <= b.right and b.bottom <= y <= b.top


def read_window(
    ds: rasterio.DatasetReader,
    cx: float,
    cy: float,
    half: float,
    out_px: int,
    bands: list[int] | None = None,
    resampling: Resampling = Resampling.bilinear,
) -> np.ma.MaskedArray:
    """Read a square window of side 2*half centred on (cx, cy), resampled to out_px.

    Returns a masked array (bands, out_px, out_px). Areas outside the raster
    or on nodata/alpha are masked. Overviews are used automatically.
    """
    bands = bands or list(range(1, ds.count + 1))
    win = from_bounds(cx - half, cy - half, cx + half, cy + half, ds.transform)
    data = ds.read(
        bands, window=win, out_shape=(len(bands), out_px, out_px), boundless=True, masked=True, resampling=resampling
    )
    return data
