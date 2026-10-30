import csv
import json
import re
import shutil
from pathlib import Path

import pytest

from conftest import X0, Y0, plane
from reality_check import appsettings
from reality_check.detect import classify_file, detect
from reality_check.export import write_residuals, write_summary
from reality_check.models import CheckKind, ObsStatus
from reality_check.pipeline import make_datasets, run
from reality_check.render import ChipRenderer
from reality_check.report import build_html
from reality_check.review import recompute_cloud_z
from reality_check.session import Session, Settings
from reality_check.stats import trimmed_mean, z_offsets
from reality_check.templates import builtin_templates, resolve


@pytest.fixture
def spots_path(tmp_path: Path) -> str:
    """Spot heights 2 cm below the surface (surface sits 20 mm above). Ids clash with the
    control points on purpose; one outlier on soft ground; EDGE 5 m outside the data (kept),
    OFF 14 m and FAR 200 m outside (disregarded)."""
    pts = [("A", X0 + 5, Y0 + 5), ("B", X0 + 15, Y0 + 30), ("S3", X0 + 20, Y0 + 20), ("S4", X0 + 12, Y0 + 40),
           ("S5", X0 + 22, Y0 + 8), ("S6", X0 + 18, Y0 + 15), ("S7", X0 + 8, Y0 + 25), ("S8", X0 + 24, Y0 + 44),
           ("S9", X0 + 14, Y0 + 12), ("SOFT", X0 + 10, Y0 + 35), ("FAR", X0 - 200, Y0),
           ("EDGE", X0 - 5, Y0 + 20), ("OFF", X0 + 20, Y0 - 14)]
    lines = ["Label,Easting,Northing,Elevation"]
    for pid, x, y in pts:
        z = float(plane(x, y)) - 0.02 - (0.5 if pid == "SOFT" else 0)  # SOFT: surface 520 mm above
        lines.append(f"{pid},{x:.3f},{y:.3f},{z:.3f}")
    p = tmp_path / "pit_spot_heights.csv"
    p.write_text("\n".join(lines) + "\n")
    return str(p)


def _run(control_path, dem_path, las_path, spots_path, **settings):
    ds = make_datasets([], [dem_path], [las_path])
    return run(control_path, ds, Settings(**settings), progress=lambda m: None, spots_path=spots_path)


def test_spot_heights_are_checked_in_dem_and_cloud_and_kept_apart(control_path, dem_path, las_path, spots_path):
    res = _run(control_path, dem_path, las_path, spots_path, cloud_classes=[2])
    s = res.session
    assert len(s.spots) == 13 and len(s.points) == 4
    assert all(o.point_id in {"A", "B", "C", "OUT"} for o in s.observations)  # control untouched
    obs = {(o.point_id, o.dataset_id): o for o in s.spot_observations}
    assert {o.check for o in s.spot_observations} == {CheckKind.Z}
    by_id = {p.id: p for p in s.spots}
    for ds_id in ("dem:dem", "cloud:cloud"):
        assert obs[("A", ds_id)].z - by_id["A"].z == pytest.approx(0.02, abs=2e-3)  # spot A, not control A
        assert obs[("FAR", ds_id)].status is ObsStatus.OUT_OF_EXTENT
    (dem_off,) = [o for o in z_offsets(s.spots, s.spot_observations, 0.2, spots=True) if o.dataset_id == "dem:dem"]
    # 10 values, 20% trim: 2 left out (the SOFT outlier and one other), mean back near +20 mm
    assert (dem_off.n, dem_off.trimmed_out, dem_off.above) == (10, 2, 10)
    assert dem_off.mean == pytest.approx(0.02, abs=2e-3)


def test_spot_heights_far_outside_the_data_are_disregarded(control_path, dem_path, las_path, spots_path):
    s = _run(control_path, dem_path, las_path, spots_path).session
    assert set(s.spots_disregarded) == {"FAR", "OFF"}
    assert s.spots_disregarded["OFF"] == pytest.approx(14, abs=1.5)  # the cloud's coverage, 1 m cells
    by_id = {p.id: p for p in s.spots}
    assert not by_id["FAR"].enabled and not by_id["OFF"].enabled and by_id["EDGE"].enabled
    edge = [o for o in s.spot_observations if o.point_id == "EDGE"]
    assert all(o.status is ObsStatus.OUT_OF_EXTENT for o in edge)  # kept, shown as outside

    s = _run(control_path, dem_path, las_path, spots_path, spot_max_outside=20).session
    assert set(s.spots_disregarded) == {"FAR"}


def test_coverage_distance():
    import numpy as np

    from reality_check.cloud import Coverage

    cov = Coverage(1.0, np.array([[0, 0], [1, 0], [5, 5]]))
    # Whole-cell gaps: a neighbouring cell counts as 0 m, one empty cell between as 1 m.
    assert cov.distance(0.5, 0.5) == 0 and cov.distance(2.9, 0.5) == 0 and cov.distance(3.5, 0.5) == 1
    assert cov.distance(-10.5, 0.5) == pytest.approx(10)
    assert Coverage(1.0, np.empty((0, 2), np.int64)).distance(0, 0) is None


def test_report_csv_and_session_with_spot_heights(tmp_path, control_path, dem_path, las_path, spots_path):
    res = _run(control_path, dem_path, las_path, spots_path)
    s = res.session
    r = ChipRenderer(s, res.windows)
    try:
        html = build_html(s, r, res.warnings)
    finally:
        r.close()
    assert "<h2>Spot heights</h2>" in html and "pit_spot_heights.csv" in html
    assert "above the spot heights on average" in html and "Trimmed mean (90%)" in html
    assert html.count("<figure>") == 4 * 2  # no chips for spot heights

    write_residuals(s, tmp_path / "res.csv")
    rows = list(csv.DictReader(open(tmp_path / "res.csv")))
    assert sum(r["role"] == "spot" for r in rows) == 13 * 2
    assert "<b>Disregarded:</b> 2 spot heights" in html
    assert re.search(r"OFF <span class='muted'>[^<]*nearest data 1[3-5] m", html)
    write_summary(s, tmp_path / "sum.csv")
    rows = list(csv.DictReader(open(tmp_path / "sum.csv")))
    spot = [r for r in rows if r["group"] == "spot"]
    assert len(spot) == 2 and all(r["trimmed_mean_90"] for r in spot)

    s.save(tmp_path / "s.rcheck.json")
    back = Session.load(tmp_path / "s.rcheck.json")
    assert back.spots_path == spots_path and len(back.spots) == 13
    assert back.spots_disregarded == s.spots_disregarded
    assert len(back.spot_observations) == len(s.spot_observations) and back.settings.trim == 0.10


def test_class_change_recomputes_spot_cloud_z(control_path, dem_path, las_path, spots_path):
    res = _run(control_path, dem_path, las_path, spots_path)
    s = res.session
    s.settings.cloud_classes = [5]  # vegetation only: none in the west, where the spot heights are
    recompute_cloud_z(s, res.windows)
    cloud = [o for o in s.spot_observations if o.dataset_id == "cloud:cloud"]
    assert len(cloud) == 13 and all(o.status is not ObsStatus.OK for o in cloud)


def test_trimmed_mean_matches_excel():
    # Excel: TRIMMEAN({4,5,6,7,2,3,4,5,1,2,3}, 0.2) = 3.7778 (11 values, 2 left out)
    assert trimmed_mean([4, 5, 6, 7, 2, 3, 4, 5, 1, 2, 3], 0.2) == pytest.approx(34 / 9)
    assert trimmed_mean([1, 2, 30], 0.1) == pytest.approx(11)  # 0.3 rounds down to 0: nothing left out


def test_templates_and_detect_find_spot_heights_separately(tmp_path, control_path, dem_path, spots_path):
    root = tmp_path / "proj"
    root.mkdir()
    for f in (control_path, dem_path, spots_path):
        shutil.copy(f, root)
    res = resolve(builtin_templates()[0], root)
    assert Path(res.found["control"]).name == "gcp.csv"
    assert Path(res.found["spots"]).name == "pit_spot_heights.csv"
    (root / "pit_spot_heights.csv").unlink()
    res = resolve(builtin_templates()[0], root)
    assert "spots" not in res.found and "spots" not in res.missing  # optional

    assert classify_file(spots_path) == "spots" and classify_file(control_path) == "control"
    det = detect([control_path, spots_path])
    assert det.found["spots"] == spots_path and det.found["control"] == control_path


def test_old_settings_file_gets_spot_rules_on_builtins(tmp_path):
    old = {t.name: t.to_dict() for t in builtin_templates()}
    for d in old.values():
        d.pop("spots")
        d["control"]["exclude"] = []
    custom = dict(old["Project root"], name="My site")
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"defaults": {"tol_z": 0.05}, "templates": list(old.values()) + [custom]}))
    app = appsettings.load(path)
    assert app.defaults.trim == 0.10 and app.defaults.tol_z == 0.05
    root = app.template("Project root")
    assert root.spots.patterns and "*spot*" in root.control.exclude
    assert not app.template("My site").spots.patterns  # user templates are left as they are
