import csv
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from conftest import X0, Y0, plane
from reality_check.cloud import Chunk, sample_windows
from reality_check.control import load_control
from reality_check.export import write_residuals, write_summary
from reality_check.models import ControlPoint, Role
from reality_check.pipeline import make_datasets, run
from reality_check.render import ChipRenderer
from reality_check.report import build_html
from reality_check.sections import (
    MIN_RELIEF,
    Section,
    analyse,
    code_matches,
    dem_profile,
    find_sections,
    fit,
    plain_spots,
)


def _pts(*rows):
    return [ControlPoint(pid, x, y, z, code=code) for pid, x, y, z, code in rows]


# ---------- the code column ----------

def test_headerless_fifth_column_is_code_or_role(tmp_path):
    spots = tmp_path / "spots.csv"
    spots.write_text("CS0001,1,2,3,CS1\nNSD1,4,5,6,DRIVE\n")
    assert [(p.id, p.code, p.role) for p in load_control(spots)] == [("CS0001", "CS1", Role.UNKNOWN),
                                                                     ("NSD1", "DRIVE", Role.UNKNOWN)]
    gcps = tmp_path / "gcp.csv"
    gcps.write_text("A,1,2,3,0\nB,4,5,6,1\n")  # Metashape-style used flag, no header
    assert [(p.role, p.code) for p in load_control(gcps)] == [(Role.CHECKPOINT, ""), (Role.GCP, "")]


def test_code_header(tmp_path):
    f = tmp_path / "a.csv"
    f.write_text("Point,Easting,Northing,Elevation,Code\nP1,1,2,3,CS2\n")
    p = load_control(f)[0]
    assert (p.id, p.code) == ("P1", "CS2")
    g = tmp_path / "b.csv"
    g.write_text("Code,E,N,Z\nP1,1,2,3\n")  # no other name column: the code is the point name
    assert load_control(g)[0].id == "P1"


# ---------- grouping ----------

def test_code_matching_and_grouping():
    assert code_matches("cs12", "CS*") and code_matches("XS1", "CS*, XS*") and not code_matches("DRIVE", "CS*")
    assert not code_matches("", "CS*")
    spots = _pts(("a", 0, 0, 1, "CS1"), ("b", 0, 1, 1, "CS2"), ("c", 0, 2, 1, "CS1"), ("d", 9, 9, 1, "DRIVE"),
                 ("e", 5, 5, 1, "CS3"))  # CS3 has one point: not a section
    secs = find_sections(spots, "CS*")
    assert [(s.code, [p.id for p in s.nodes]) for s in secs] == [("CS1", ["a", "c"])]


def test_plain_spots_leave_out_section_points(control_path, dem_path, tmp_path):
    spots = tmp_path / "spots.csv"
    spots.write_text("S1,500010,7000010,100.3,DRIVE\nC1,500020,7000020,100.6,CS1\nC2,500021,7000020,100.6,CS1\n")
    s = run(control_path, make_datasets([], [dem_path], []), progress=lambda m: None, spots_path=str(spots)).session
    assert [p.id for p in plain_spots(s)] == ["S1"]


# ---------- geometry ----------

def test_chainage_and_projection():
    sec = Section("CS1", _pts(("a", 0, 0, 0, "CS1"), ("b", 3, 4, 0, "CS1"), ("c", 3, 10, 0, "CS1")))
    assert sec.chainage.tolist() == [0, 5, 11]
    x, y = sec.at(np.array([2.5, 8.0]))
    assert (x[0], y[0]) == pytest.approx((1.5, 2.0)) and (x[1], y[1]) == pytest.approx((3.0, 7.0))
    ch, off = sec.project(np.array([3.5, -0.8]), np.array([7.0, -0.6]))
    assert ch[0] == pytest.approx(8.0) and off[0] == pytest.approx(0.5)  # right of the string, looking along it
    assert ch[1] == pytest.approx(-0.96)  # before the first point: the first segment carries on
    assert sec.bearing() == pytest.approx(16.7, abs=0.1)


# ---------- the fit ----------

KERB_Y = Y0 + 20.0


def _kerb(y):
    return 0.15 * np.maximum(0.0, 1 - np.abs(np.asarray(y) - KERB_Y) / 0.4)


@pytest.fixture
def kerb_dem(tmp_path: Path) -> str:
    """A plane with a kerb running east-west at KERB_Y, 0.15 m high and 0.8 m wide, 0.05 m cells."""
    res, n = 0.05, 600
    cols, rows = np.meshgrid(np.arange(n), np.arange(n))
    x = X0 + (cols + 0.5) * res
    y = Y0 + 30 - (rows + 0.5) * res
    p = tmp_path / "kerb.tif"
    with rasterio.open(p, "w", driver="GTiff", width=n, height=n, count=1, dtype="float32", crs="EPSG:7853",
                       transform=from_origin(X0, Y0 + 30, res, res)) as ds:
        ds.write((plane(x, y) + _kerb(y)).astype("float32"), 1)
    return str(p)


def _section_over_kerb(shift_north: float, lower: float) -> Section:
    """Surveyed south to north across the kerb, which the survey places shift_north further north and lower."""
    ys = Y0 + np.concatenate([np.arange(15, 19.6, 0.5), np.arange(19.6, 20.65, 0.1), np.arange(21, 25.1, 0.5)])
    x = X0 + 10
    nodes = [ControlPoint(f"P{i}", x, float(y), float(plane(x, y) + _kerb(y - shift_north) - lower), code="CS1")
             for i, y in enumerate(ys)]
    return Section("CS1", nodes)


def test_fit_finds_a_shift_along_the_section(kerb_dem):
    sec = _section_over_kerb(0.10, 0.02)
    f = fit(sec, dem_profile(sec, "dem:kerb", kerb_dem))
    # The surface's kerb is 0.10 m south of the surveyed one: towards the first point, so negative.
    assert f.significant
    assert f.shift == pytest.approx(-0.10, abs=0.011)
    assert f.vertical == pytest.approx(0.02, abs=0.003)
    assert f.sd_after < 0.2 * f.sd_before


def test_fit_with_no_shift(kerb_dem):
    sec = _section_over_kerb(0.0, 0.0)
    f = fit(sec, dem_profile(sec, "dem:kerb", kerb_dem))
    assert abs(f.mean) < 0.002 and f.rmse < 0.005
    assert not f.significant  # nothing to improve on


def test_flat_section_gives_no_shift(dem_path):
    nodes = [ControlPoint(f"P{i}", X0 + 10, Y0 + 10 + i, float(plane(X0 + 10, Y0 + 10 + i)), code="CS1")
             for i in range(8)]
    sec = Section("CS1", nodes)  # up a steady 2% slope: 140 mm of rise, but no shape
    assert sec.relief < MIN_RELIEF
    f = fit(sec, dem_profile(sec, "dem:dem", dem_path))
    assert f.shift is None and "shape" in f.reason and f.rmse < 0.002


# ---------- end to end ----------

def test_sections_through_the_pipeline_report_and_exports(control_path, dem_path, las_path, tmp_path):
    spots = tmp_path / "spots.csv"
    rows = ["S1,500005.000,7000005.000,{:.3f},DRIVE".format(float(plane(X0 + 5, Y0 + 5)))]
    for i in range(10):  # a cross section across the western half (ground class, no vegetation)
        x, y = X0 + 10 + i, Y0 + 30
        rows.append(f"C{i:02d},{x:.3f},{y:.3f},{float(plane(x, y)) + 0.01:.3f},CS1")
    spots.write_text("\n".join(rows) + "\n")
    res = run(control_path, make_datasets([], [dem_path], [las_path]), progress=lambda m: None,
              spots_path=str(spots))
    s = res.session
    assert any(k.startswith("section:CS1:") for k in res.windows["cloud:cloud"])
    (r,) = analyse(s, res.windows)
    assert len(r.profiles) == 2
    for ds_id, f in r.fits.items():
        assert f.mean == pytest.approx(-0.01, abs=0.004), ds_id  # surveyed 10 mm above the surface
        assert f.shift is None  # a plane has no shape to fit

    renderer = ChipRenderer(s, res.windows)
    try:
        html = build_html(s, renderer)
    finally:
        renderer.close()
    assert "Cross sections" in html and "<svg class='cs'" in html and "class='cs-join'" in html
    assert "sections nojoin" not in html
    s.settings.section_join = False
    renderer = ChipRenderer(s, res.windows)
    try:
        assert "sections nojoin" in build_html(s, renderer)
    finally:
        renderer.close()

    write_residuals(s, tmp_path / "res.csv", res.windows)
    rows = list(csv.DictReader(open(tmp_path / "res.csv")))
    sec_rows = [r for r in rows if r["role"] == "section"]
    assert len(sec_rows) == 20 and {r["code"] for r in sec_rows} == {"CS1"}
    assert {r["point_id"] for r in rows if r["role"] == "spot"} == {"S1"}
    write_summary(s, tmp_path / "sum.csv", res.windows)
    groups = {r["group"] for r in csv.DictReader(open(tmp_path / "sum.csv"))}
    assert "section CS1" in groups and "spot" in groups


def test_sample_windows_matches_a_brute_force_search(las_path):
    rng = np.random.default_rng(3)
    centres = {f"w{i}": (X0 + rng.uniform(-5, 55), Y0 + rng.uniform(-5, 55)) for i in range(40)}
    halves = {k: float(rng.uniform(0.3, 6)) for k in centres}
    got = sample_windows(las_path, centres, halves, chunk_size=30_000).windows
    everything = sample_windows(las_path, {"all": (X0 + 25, Y0 + 25)}, 100).windows["all"]
    for k, (cx, cy) in centres.items():
        h = halves[k]
        m = (np.abs(everything.x - cx) <= h) & (np.abs(everything.y - cy) <= h)
        want = np.sort(everything.x[m])
        assert np.array_equal(np.sort(got[k].x), want), k
    assert isinstance(got["w0"], Chunk)
