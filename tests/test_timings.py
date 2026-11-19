import time

import numpy as np
import pytest

from conftest import X0, Y0, plane
from reality_check.cloud import Coverage
from reality_check.overview import render_overview
from reality_check.pipeline import make_datasets, run
from reality_check.render import ChipRenderer
from reality_check.report import build_html
from reality_check.timings import Timings, fmt


def test_steps_phases_and_total():
    t = Timings()
    with t.step("Read", "Control points", "gcp.csv"):
        time.sleep(0.01)
    t.note("4 points")
    t.add("Compute", "Something", 1.5)
    assert [s.name for s in t.steps] == ["Control points", "Something"]
    assert t.steps[0].detail == "gcp.csv · 4 points" and t.steps[0].seconds >= 0.01
    assert t.total == pytest.approx(1.5 + t.steps[0].seconds)
    t.start_phase("report")
    t.add("Draw", "Map", 2.0)
    t.start_phase("report")  # a rebuilt report replaces the last one's steps, not the run's
    t.add("Draw", "Map", 1.0)
    assert [s.name for s in t.steps] == ["Control points", "Something", "Map"] and t.by_group()["Draw"] == 1.0
    assert "Total" in t.table()
    assert (fmt(0.0123), fmt(12.34), fmt(75)) == ("12 ms", "12.3 s", "1 min 15 s")


def test_run_and_report_record_their_steps(control_path, ortho_path, dem_path, las_path):
    res = run(control_path, make_datasets([ortho_path], [dem_path], [las_path]), progress=lambda m: None)
    names = [s.name for s in res.timings.steps]
    assert names[0] == "Control points" and "Point cloud" in names and "DEM Z check" in names
    r = ChipRenderer(res.session, res.windows)
    try:
        html = build_html(res.session, r, timings=res.timings)
    finally:
        r.close()
    groups = {s.group for s in res.timings.steps}
    assert groups == {"Read", "Compute", "Draw"}
    assert "Site overview background" in [s.name for s in res.timings.steps]
    assert "<details class='screen-only timings'>" in html and "Processing times" in html


def test_coverage_inside_lookup_matches_the_full_search():
    rng = np.random.default_rng(2)
    cells = np.unique(rng.integers(0, 60, (900, 2)), axis=0)
    fast = Coverage(1.0, cells)
    slow = Coverage(1.0, cells)
    for x, y in rng.uniform(-20, 80, (200, 2)):
        a = fast.distance(x, y)
        ix, iy = np.floor(x), np.floor(y)
        gx = np.maximum(np.abs(cells[:, 0] - ix) - 1, 0)
        gy = np.maximum(np.abs(cells[:, 1] - iy) - 1, 0)
        assert a == pytest.approx(float(np.sqrt((gx ** 2 + gy ** 2).min())))
    assert slow.distance(cells[0, 0] + 0.5, cells[0, 1] + 0.5) == 0.0


def test_short_section_gets_a_readable_mark(control_path, dem_path, tmp_path):
    spots = tmp_path / "spots.csv"
    rows = [f"C{i},{X0 + 20 + i * 0.5:.3f},{Y0 + 20:.3f},{float(plane(X0 + 20 + i * 0.5, Y0 + 20)):.3f},CS1"
            for i in range(4)]
    spots.write_text("\n".join(rows) + "\n")
    s = run(control_path, make_datasets([], [dem_path], []), progress=lambda m: None, spots_path=str(spots)).session
    plain = render_overview(s).sheets[0]
    s.spots = []
    without = render_overview(s).sheets[0]
    diff = np.any(np.asarray(plain) != np.asarray(without), axis=2)
    ys, xs = np.nonzero(diff)
    # A 1.5 m section on a ~150 m map is under 2 mm long; its mark is at least 22 mm.
    assert xs.max() - xs.min() >= 22 * 8 * 0.95
