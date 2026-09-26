import numpy as np
import pytest
import rasterio

from conftest import SIZE, X0, Y0, plane
from reality_check.checks import z_from_cloud, z_from_dem
from reality_check.cloud import Chunk, filter_classes, is_classified, sample_windows
from reality_check.control import load_control
from reality_check.models import CheckKind, ControlPoint, Observation, ObsStatus, Role
from reality_check.raster import sample_bilinear
from reality_check.stats import Stats, summarise


def test_bilinear_exact_on_plane(dem_path):
    with rasterio.open(dem_path) as ds:
        for x, y in [(X0 + 10.13, Y0 + 20.77), (X0 + 33.3, Y0 + 1.1)]:
            assert sample_bilinear(ds, x, y) == pytest.approx(float(plane(x, y)), abs=1e-4)
        # Within half a pixel of the edge the nearest valid cells are used, so
        # the error is at most slope * half pixel.
        for x, y in [(X0 + 0.1, Y0 + 0.1), (X0 + 49.9, Y0 + 30)]:
            assert sample_bilinear(ds, x, y) == pytest.approx(float(plane(x, y)), abs=0.03 * 0.25)


def test_bilinear_nodata_and_outside(dem_path):
    with rasterio.open(dem_path) as ds:
        assert sample_bilinear(ds, X0 + 0.5, Y0 + SIZE - 0.5) is None  # inside nodata block
        assert sample_bilinear(ds, X0 - 1, Y0 + 1) is None


def test_dem_z_residuals(dem_path, control_path):
    pts = load_control(control_path)
    obs = {o.point_id: o for o in z_from_dem("dem", dem_path, pts)}
    assert obs["OUT"].status is ObsStatus.OUT_OF_EXTENT
    for pid in "ABC":
        cp = next(p for p in pts if p.id == pid)
        assert obs[pid].z - cp.z == pytest.approx(-0.05, abs=1e-3)


def test_cloud_window_and_z(las_path, control_path):
    pts = load_control(control_path)
    res = sample_windows(las_path, {p.id: (p.x, p.y) for p in pts}, half=2.5)
    assert res.total_points == 200_000
    assert res.class_counts.keys() == {2, 5} and is_classified(res.class_counts)
    assert len(res.windows["OUT"]) == 0
    w = res.windows["B"]
    assert len(w) > 0 and np.all(np.abs(w.x - (X0 + 25)) <= 2.5)

    cp = next(p for p in pts if p.id == "A")
    o = z_from_cloud("cloud", cp, res.windows["A"], radius=0.5)
    assert o.status is ObsStatus.OK and o.n_points >= 3
    assert o.z - cp.z == pytest.approx(-0.05, abs=0.01)


def test_class_filter_removes_vegetation(las_path, control_path):
    pts = load_control(control_path)
    cp = next(p for p in pts if p.id == "C")  # in the vegetated east
    res = sample_windows(las_path, {cp.id: (cp.x, cp.y)}, half=1)
    all_z = z_from_cloud("c", cp, res.windows[cp.id], 0.5)
    ground_z = z_from_cloud("c", cp, filter_classes(res.windows[cp.id], {2}), 0.5)
    assert all_z.z - cp.z == pytest.approx(4.95, abs=0.02)
    assert ground_z.status is ObsStatus.NO_DATA  # no ground points under the canopy


def test_xyz_reader(xyz_path):
    res = sample_windows(xyz_path, {"B": (X0 + 25, Y0 + 25)}, half=5)
    w = res.windows["B"]
    assert res.total_points == 20_000 and len(w) > 0
    assert tuple(w.rgb[0]) == (10, 20, 30)
    assert res.class_counts == {}


def test_empty_window_is_no_data():
    cp = ControlPoint("A", 0, 0, 0)
    e = np.empty(0)
    o = z_from_cloud("c", cp, Chunk(e, e, e), 0.5)
    assert o.status is ObsStatus.NO_DATA and o.n_points == 0


def test_stats():
    s = Stats.of([0.03, -0.04])
    assert s.rmse == pytest.approx(np.sqrt((0.0009 + 0.0016) / 2))
    assert s.mean == pytest.approx(-0.005)
    assert Stats.of([None]) is None


def test_summary_excludes_disabled_and_splits_roles():
    pts = [
        ControlPoint("G", 0, 0, 0, role=Role.GCP),
        ControlPoint("K", 0, 0, 0, role=Role.CHECKPOINT),
        ControlPoint("X", 0, 0, 0, role=Role.CHECKPOINT, enabled=False),
    ]
    obs = [
        Observation("G", "d", CheckKind.Z, ObsStatus.OK, z=0.01),
        Observation("K", "d", CheckKind.Z, ObsStatus.OK, z=0.05),
        Observation("X", "d", CheckKind.Z, ObsStatus.OK, z=9.0),
        Observation("K", "o", CheckKind.XY, ObsStatus.OK, x=0.03, y=0.04),
    ]
    rows = {(r.dataset_id, r.component, r.group): r.stats for r in summarise(pts, obs)}
    assert rows[("d", "dz", "all")].n == 2
    assert rows[("d", "dz", "checkpoint")].mean == pytest.approx(0.05)
    assert rows[("o", "dxy", "all")].mean == pytest.approx(0.05)
