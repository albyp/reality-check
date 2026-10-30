from reality_check.models import CheckKind, ControlPoint, Observation, ObsStatus, Role
from reality_check.stats import z_offsets


def _obs(pid, z, ds="dem", status=ObsStatus.OK):
    return Observation(pid, ds, CheckKind.Z, status, z=z)


def test_offset_counts_above_below_and_level():
    pts = [ControlPoint(f"P{i}", 0, 0, 100.0) for i in range(4)]
    obs = [_obs("P0", 100.030), _obs("P1", 100.010), _obs("P2", 99.990), _obs("P3", 100.0002)]
    (o,) = z_offsets(pts, obs)
    assert (o.group, o.n, o.above, o.below, o.level) == ("all", 4, 2, 1, 1)
    assert abs(o.mean - 0.00755) < 1e-9
    assert "8 mm above the control" in o.sentence("DEM")


def test_offset_uses_checkpoints_and_skips_disabled_and_failed():
    pts = [ControlPoint("G", 0, 0, 10.0, role=Role.GCP), ControlPoint("C1", 0, 0, 10.0, role=Role.CHECKPOINT),
           ControlPoint("C2", 0, 0, 10.0, role=Role.CHECKPOINT, enabled=False),
           ControlPoint("C3", 0, 0, 10.0, role=Role.CHECKPOINT)]
    obs = [_obs("G", 10.5), _obs("C1", 9.98), _obs("C2", 11.0), _obs("C3", 0, status=ObsStatus.NO_DATA)]
    (o,) = z_offsets(pts, obs)
    assert (o.group, o.n, o.below) == ("checkpoint", 1, 1)
    assert o.sentence("point cloud").startswith("The point cloud sits 20 mm below the control on average")


def test_offset_ignores_xy_and_reports_level():
    pts = [ControlPoint("A", 0, 0, 5.0)]
    obs = [Observation("A", "ortho", CheckKind.XY, ObsStatus.OK, x=0.1, y=0), _obs("A", 5.0001)]
    (o,) = z_offsets(pts, obs)
    assert o.dataset_id == "dem" and "level with the control" in o.sentence("DEM")
