import json
import shutil
from pathlib import Path

import pytest

from reality_check import appsettings
from reality_check.batch import BatchOptions, plan, run_batch
from reality_check.session import Settings
from reality_check.templates import RoleRule, Template, builtin_templates, resolve


def _tpl(name: str) -> Template:
    return next(t for t in builtin_templates() if t.name.startswith(name))


def _exports_project(root: Path, ortho, dem, las, control, name="260924_pit", with_dem=True, with_ortho=True) -> Path:
    proj = root / name
    (proj / "exports").mkdir(parents=True)
    shutil.copy(control, proj / f"{name}_gcp.csv")
    if with_ortho:
        shutil.copy(ortho, proj / "exports" / f"{name}.tif")
    if with_dem:
        shutil.copy(dem, proj / "exports" / f"{name}_dem.tif")
    shutil.copy(las, proj / "exports" / f"{name}.laz")
    return proj


def test_exports_template_maps_every_input(tmp_path, ortho_path, dem_path, las_path, control_path):
    proj = _exports_project(tmp_path, ortho_path, dem_path, las_path, control_path)
    res = resolve(_tpl("Exports folder"), proj)
    assert {k: Path(v).name for k, v in res.found.items()} == {
        "control": "260924_pit_gcp.csv", "ortho": "260924_pit.tif", "dem": "260924_pit_dem.tif",
        "cloud": "260924_pit.laz"}
    assert res.missing == [] and res.runnable


def test_missing_ortho_and_dem_still_runnable_with_cloud(tmp_path, ortho_path, dem_path, las_path, control_path):
    proj = _exports_project(tmp_path, ortho_path, dem_path, las_path, control_path, with_dem=False, with_ortho=False)
    res = resolve(_tpl("Exports folder"), proj)
    assert sorted(res.missing) == ["dem", "ortho"] and res.runnable


def test_no_control_is_not_runnable(tmp_path, las_path):
    (tmp_path / "p").mkdir()
    shutil.copy(las_path, tmp_path / "p" / "c.laz")
    res = resolve(_tpl("Project root"), tmp_path / "p")
    assert not res.runnable and res.problem() == "No control point file."


def test_project_root_template_separates_ortho_and_dem(tmp_path, ortho_path, dem_path, control_path):
    p = tmp_path / "p"
    p.mkdir()
    shutil.copy(ortho_path, p / "site.tif")
    shutil.copy(dem_path, p / "site_dem.tif")
    shutil.copy(control_path, p / "gcp.csv")
    res = resolve(_tpl("Project root"), p)
    assert Path(res.found["ortho"]).name == "site.tif" and Path(res.found["dem"]).name == "site_dem.tif"


def test_pix4d_layout(tmp_path, ortho_path, dem_path, las_path, control_path):
    p = tmp_path / "flight"
    for sub in ("3_dsm_ortho/2_mosaic", "3_dsm_ortho/1_dsm", "2_densification/point_cloud"):
        (p / sub).mkdir(parents=True)
    shutil.copy(ortho_path, p / "3_dsm_ortho/2_mosaic/flight_transparent_mosaic_group1.tif")
    shutil.copy(dem_path, p / "3_dsm_ortho/1_dsm/flight_dsm.tif")
    shutil.copy(las_path, p / "2_densification/point_cloud/flight_group1_densified_point_cloud.laz")
    shutil.copy(control_path, p / "gcp.csv")
    res = resolve(_tpl("Pix4D"), p)
    assert res.missing == [] and len(res.found) == 4


def test_missing_folder_is_noted(tmp_path, control_path):
    (tmp_path / "p").mkdir()
    shutil.copy(control_path, tmp_path / "p" / "gcp.csv")
    res = resolve(_tpl("Exports folder"), tmp_path / "p")
    assert "folder 'exports' not found" in " ".join(res.notes)


def test_template_round_trip():
    t = Template("Mine", "desc", cloud=RoleRule("lidar", ["*.laz"], ["*_raw*"], recursive=True))
    assert Template.from_dict(json.loads(json.dumps(t.to_dict()))) == t


def test_settings_created_with_builtins_and_saved(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    monkeypatch.setenv("RC_SETTINGS", str(path))
    s = appsettings.load()
    assert path.exists() and [t.name for t in s.templates][:2] == ["Project root", "Exports folder"]
    s.defaults.tol_z = 0.05
    s.upsert(Template("Site A", cloud=RoleRule("lidar", ["*.laz"])))
    s.default_template = "Site A"
    s.save()
    s2 = appsettings.load()
    assert s2.defaults.tol_z == 0.05 and s2.default_template == "Site A" and s2.template("Site A")


def test_settings_rename_and_duplicate_names(tmp_path):
    s = appsettings.AppSettings(tmp_path / "s.json")
    t = s.template("Project root")
    s.upsert(Template("Root v2", control=t.control), old_name="Project root")
    assert s.template("Root v2") and not s.template("Project root")
    with pytest.raises(ValueError):
        s.upsert(Template("Exports folder"))
    assert s.restore_builtins() == ["Project root"]


def test_unreadable_settings_file_is_kept_aside(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json")
    s = appsettings.load(path)
    assert s.templates and (tmp_path / "settings.json.bad").exists()


def test_batch_runs_projects_and_skips_bad_ones(tmp_path, ortho_path, dem_path, las_path, control_path):
    a = _exports_project(tmp_path, ortho_path, dem_path, las_path, control_path, name="pit_a")
    b = _exports_project(tmp_path, ortho_path, dem_path, las_path, control_path, name="pit_b", with_dem=False)
    c = tmp_path / "empty"
    c.mkdir()
    tpl = _tpl("Exports folder")
    assert [p.resolution.runnable for p in plan(tpl, [a, b, c])] == [True, True, False]

    items = run_batch(tpl, [a, b, c], Settings(tol_z=0.03), BatchOptions(pdf=False, html=True, csv=True),
                      progress=lambda m: None)
    assert [i.status for i in items] == ["done", "done", "skipped"]
    assert (a / "pit_a_RealityCheck.html").exists() and (a / "pit_a_RealityCheck_residuals.csv").exists()
    assert (b / "pit_b_RealityCheck.rcheck.json").exists() and "dem" in items[1].message
    assert any("DEM Z RMSE" in h for h in items[0].headline) and "FAIL" in items[0].headline[0]
    assert items[2].message == "No control point file."


def test_own_outputs_are_never_inputs(tmp_path, control_path, las_path):
    p = tmp_path / "p"
    p.mkdir()
    shutil.copy(control_path, p / "gcp.csv")
    shutil.copy(las_path, p / "c.laz")
    (p / "p_RealityCheck_residuals.csv").write_text("point_id,dz\nA,0.01\n")
    (p / "p_RealityCheck_summary.csv").write_text("x\n")
    res = resolve(_tpl("Project root"), p)
    assert Path(res.found["control"]).name == "gcp.csv" and res.notes == []
