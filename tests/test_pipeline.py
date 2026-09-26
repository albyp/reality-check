import csv
from pathlib import Path

import pytest

from reality_check.cli import main
from reality_check.models import CheckKind, ObsSource, ObsStatus, Role
from reality_check.pipeline import make_datasets, run
from reality_check.render import ChipRenderer
from reality_check.report import build_html, find_browser, write_pdf
from reality_check.review import find_obs, point_results, recompute_cloud_z, reset_xy, set_manual_xy, set_not_found
from reality_check.session import Session, Settings

SAMPLE = Path(__file__).resolve().parents[1] / "sample_data"


def test_cli_run_end_to_end(tmp_path, control_path, ortho_path, dem_path, las_path):
    out = tmp_path / "out"
    rc = main(["run", "--control", control_path, "--ortho", ortho_path, "--dem", dem_path,
               "--cloud", las_path, "--out", str(out), "--classes", "2,5", "--tol-z", "0.03", "--csv"])
    assert rc == 0
    assert sorted(p.name for p in out.iterdir()) == ["report.html", "residuals.csv", "session.rcheck.json",
                                                      "summary.csv"]
    rows = list(csv.DictReader(open(out / "residuals.csv")))
    dem_rows = {r["point_id"]: r for r in rows if r["dataset_id"] == "dem:dem"}
    assert float(dem_rows["A"]["dz"]) == pytest.approx(-0.05, abs=1e-3)
    assert dem_rows["OUT"]["status"] == "out_of_extent"

    html = (out / "report.html").read_text(encoding="utf-8")
    assert "RealityCheck QA/QC report" in html and html.count("<figure>") == 4 * 3
    assert "exceeds tolerance" in html  # 5 cm residuals vs 3 cm tolerance

    s = Session.load(out / "session.rcheck.json")
    assert len(s.points) == 4 and s.settings.cloud_classes == [2, 5] and s.settings.tol_z == 0.03


def _run(control_path, ortho_path, las_path, **settings):
    ds = make_datasets([ortho_path], [], [las_path])
    return run(control_path, ds, Settings(**settings), progress=lambda m: None)


def test_review_manual_xy_not_found_reset(control_path, ortho_path, las_path):
    res = _run(control_path, ortho_path, las_path)
    s = res.session
    set_manual_xy(s, "B", "ortho:ortho", s.point("B").x + 0.03, s.point("B").y - 0.04)
    pr = {p.point.id: p for p in point_results(s)}["B"]
    assert pr.dxy["ortho:ortho"] == pytest.approx(0.05) and pr.manual

    set_not_found(s, "B", "ortho:ortho")
    assert find_obs(s, "B", "ortho:ortho", CheckKind.XY).status is ObsStatus.NOT_FOUND
    reset_xy(s, "B", "ortho:ortho")  # no automatic result yet, so the observation goes
    assert find_obs(s, "B", "ortho:ortho", CheckKind.XY) is None


def test_reset_restores_automatic_result(control_path, ortho_path, las_path):
    s = _run(control_path, ortho_path, las_path).session
    o = set_manual_xy(s, "A", "ortho:ortho", 1, 2)
    o.source, o.auto_status = ObsSource.AUTO, None  # pretend detection produced this
    set_manual_xy(s, "A", "ortho:ortho", 5, 6)
    reset_xy(s, "A", "ortho:ortho")
    o = find_obs(s, "A", "ortho:ortho", CheckKind.XY)
    assert (o.x, o.y, o.source) == (1, 2, ObsSource.AUTO)


def test_class_change_recomputes_cloud_z(control_path, ortho_path, las_path):
    res = _run(control_path, ortho_path, las_path)
    s = res.session
    before = find_obs(s, "C", "cloud:cloud", CheckKind.Z)
    assert before.z - s.point("C").z == pytest.approx(4.95, abs=0.02)  # vegetation on top
    s.settings.cloud_classes = [2]
    recompute_cloud_z(s, res.windows)
    assert find_obs(s, "C", "cloud:cloud", CheckKind.Z).status is ObsStatus.NO_DATA
    assert find_obs(s, "A", "cloud:cloud", CheckKind.Z).status is ObsStatus.OK


def test_report_flags_and_roles(control_path, ortho_path, las_path):
    res = _run(control_path, ortho_path, las_path, tol_xy=0.02)
    s = res.session
    s.point("A").role = Role.CHECKPOINT
    s.point("B").needs_touch_up = True
    s.point("C").enabled = False
    set_manual_xy(s, "A", "ortho:ortho", s.point("A").x + 0.03, s.point("A").y)
    r = ChipRenderer(s, res.windows)
    try:
        html = build_html(s, r)
    finally:
        r.close()
    assert "needs touch-up" in html and "is disabled" in html and "manual" in html
    assert "dXY 30 mm" in html  # exceeds the 20 mm tolerance


@pytest.mark.skipif(find_browser() is None, reason="no Edge/Chrome for PDF export")
def test_pdf_export(tmp_path, control_path, ortho_path, las_path):
    res = _run(control_path, ortho_path, las_path)
    r = ChipRenderer(res.session, res.windows)
    try:
        pdf = write_pdf(res.session, r, tmp_path / "r.pdf")
    finally:
        r.close()
    assert pdf.read_bytes()[:4] == b"%PDF"


def test_session_round_trip_keeps_review_state(tmp_path, control_path, dem_path):
    out = tmp_path / "out"
    main(["run", "--control", control_path, "--dem", dem_path, "--out", str(out)])
    path = out / "session.rcheck.json"
    s = Session.load(path)
    s.point("A").enabled = False
    s.point("B").needs_touch_up = True
    o = set_manual_xy(s, "B", "dem:dem", 1, 2)
    o.auto_status = ObsStatus.NOT_FOUND
    s.save(path)
    s2 = Session.load(path)
    assert not s2.point("A").enabled and s2.point("B").needs_touch_up
    assert find_obs(s2, "B", "dem:dem", CheckKind.XY).auto_status is ObsStatus.NOT_FOUND


@pytest.mark.skipif(not (SAMPLE / "260924_bz_pit_gcp_export.csv").exists(), reason="sample data not present")
def test_sample_dataset_z_accuracy(tmp_path):
    out = tmp_path / "out"
    rc = main(["run", "--control", str(SAMPLE / "260924_bz_pit_gcp_export.csv"),
               "--dem", str(SAMPLE / "260924_bz_pit_dem.tif"), "--cloud", str(SAMPLE / "260924_bz_pit.laz"),
               "--out", str(out), "--csv"])
    assert rc == 0
    rows = list(csv.DictReader(open(out / "summary.csv")))
    rmse = {r["dataset_id"]: float(r["rmse"]) for r in rows if r["group"] == "all"}
    assert rmse["dem:260924_bz_pit_dem"] < 0.05
    assert rmse["cloud:260924_bz_pit"] < 0.05
