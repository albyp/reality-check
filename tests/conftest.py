"""Synthetic datasets with known answers.

The surface is the plane z = 100 + 0.01 * (x - X0) + 0.02 * (y - Y0), in
GDA2020 / MGA zone 53 (EPSG:7853).
"""

from pathlib import Path

import reality_check  # noqa: F401  (sets PROJ/GDAL data paths before rasterio loads)
import laspy
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

X0, Y0 = 500000.0, 7000000.0
SIZE = 50.0  # m


def plane(x, y):
    return 100 + 0.01 * (np.asarray(x) - X0) + 0.02 * (np.asarray(y) - Y0)


@pytest.fixture
def dem_path(tmp_path: Path) -> str:
    res = 0.5
    n = int(SIZE / res)
    cols, rows = np.meshgrid(np.arange(n), np.arange(n))
    x = X0 + (cols + 0.5) * res
    y = Y0 + SIZE - (rows + 0.5) * res
    z = plane(x, y).astype("float32")
    z[0:4, 0:4] = -9999  # nodata block in the north-west corner
    p = tmp_path / "dem.tif"
    with rasterio.open(p, "w", driver="GTiff", width=n, height=n, count=1, dtype="float32", crs="EPSG:7853",
                       transform=from_origin(X0, Y0 + SIZE, res, res), nodata=-9999) as ds:
        ds.write(z, 1)
    return str(p)


@pytest.fixture
def ortho_path(tmp_path: Path) -> str:
    res = 0.05
    n = int(SIZE / res)
    img = np.full((4, n, n), 120, dtype="uint8")
    img[3] = 255
    # White X target centred on (X0 + 25, Y0 + 25).
    c = n // 2
    for i in range(-10, 11):
        for w in (-1, 0, 1):
            img[:3, c + i, c + i + w] = 255
            img[:3, c + i, c - i + w] = 255
    p = tmp_path / "ortho.tif"
    with rasterio.open(p, "w", driver="GTiff", width=n, height=n, count=4, dtype="uint8", crs="EPSG:7853",
                       transform=from_origin(X0, Y0 + SIZE, res, res)) as ds:
        ds.write(img)
    return str(p)


def _cloud_xyz(n=200_000, seed=1):
    rng = np.random.default_rng(seed)
    x = X0 + rng.uniform(0, SIZE, n)
    y = Y0 + rng.uniform(0, SIZE, n)
    z = plane(x, y)
    return x, y, z


@pytest.fixture
def las_path(tmp_path: Path) -> str:
    x, y, z = _cloud_xyz()
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = [0.001, 0.001, 0.001]
    header.offsets = [X0, Y0, 0]
    header.add_crs(__import__("pyproj").CRS.from_epsg(7853))
    las = laspy.LasData(header)
    las.x, las.y, las.z = x, y, z
    cls = np.full(len(x), 2, dtype=np.uint8)
    # High vegetation 5 m above ground in the eastern half.
    veg = x > X0 + SIZE / 2 + 5
    cls[veg] = 5
    las.z = np.where(veg, z + 5, z)
    las.classification = cls
    las.red = las.green = las.blue = np.full(len(x), 120 * 256, dtype=np.uint16)
    las.intensity = np.full(len(x), 1000, dtype=np.uint16)
    p = tmp_path / "cloud.laz"
    las.write(p)
    return str(p)


@pytest.fixture
def xyz_path(tmp_path: Path) -> str:
    x, y, z = _cloud_xyz(20_000)
    p = tmp_path / "cloud.xyz"
    with open(p, "w") as f:
        f.write("X Y Z R G B\n")
        for a, b, c in zip(x, y, z):
            f.write(f"{a:.3f} {b:.3f} {c:.3f} 10 20 30\n")
    return str(p)


@pytest.fixture
def control_path(tmp_path: Path) -> str:
    pts = [("A", X0 + 10, Y0 + 10), ("B", X0 + 25, Y0 + 25), ("C", X0 + 40, Y0 + 12), ("OUT", X0 - 100, Y0)]
    lines = ["#Label,X/Easting,Y/Northing,Z/Altitude"]
    for pid, x, y in pts:
        lines.append(f"{pid},{x:.3f},{y:.3f},{float(plane(x, y)) + 0.05:.3f}")  # surveyed 5 cm above surface
    p = tmp_path / "gcp.csv"
    p.write_text("\n".join(lines) + "\n")
    return str(p)
