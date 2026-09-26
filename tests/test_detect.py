import shutil
from pathlib import Path

from reality_check.detect import classify_file, detect


def _project(tmp_path, ortho_path, dem_path, las_path, control_path) -> Path:
    """Alby's site layout: project/exports/[name.tif, name.laz, name_dem.tif], control CSV at the top."""
    proj = tmp_path / "260924_pit"
    exports = proj / "exports"
    exports.mkdir(parents=True)
    shutil.copy(ortho_path, exports / "260924_pit.tif")
    shutil.copy(dem_path, exports / "260924_pit_dem.tif")
    shutil.copy(las_path, exports / "260924_pit.laz")
    shutil.copy(control_path, proj / "260924_pit_gcp_export.csv")
    (proj / "notes.docx").write_text("x")
    return proj


def test_folder_maps_site_template(tmp_path, ortho_path, dem_path, las_path, control_path):
    proj = _project(tmp_path, ortho_path, dem_path, las_path, control_path)
    det = detect([str(proj)])
    assert {k: Path(v).name for k, v in det.found.items()} == {
        "control": "260924_pit_gcp_export.csv",
        "ortho": "260924_pit.tif",
        "dem": "260924_pit_dem.tif",
        "cloud": "260924_pit.laz",
    }
    assert det.notes == []


def test_dem_by_band_count_without_name_hint(tmp_path, dem_path, ortho_path):
    a = tmp_path / "surface.tif"
    shutil.copy(dem_path, a)
    b = tmp_path / "mosaic.tif"
    shutil.copy(ortho_path, b)
    assert classify_file(a) == "dem" and classify_file(b) == "ortho"


def test_explicit_files_win_and_extras_are_noted(tmp_path, ortho_path, dem_path, las_path, control_path):
    proj = _project(tmp_path, ortho_path, dem_path, las_path, control_path)
    other = tmp_path / "other_cloud.laz"
    shutil.copy(las_path, other)
    shutil.copy(las_path, proj / "exports" / "second.laz")
    det = detect([str(proj), str(other)])
    assert Path(det.found["cloud"]).name == "other_cloud.laz"  # dropped explicitly
    det = detect([str(proj)])
    assert any("2 possible point cloud files" in n for n in det.notes)


def test_unsupported_and_missing(tmp_path):
    f = tmp_path / "readme.md"
    f.write_text("x")
    det = detect([str(f), str(tmp_path / "nope.tif")])
    assert det.found == {} and len(det.notes) == 2


def test_small_txt_control_vs_large_txt_cloud(tmp_path, xyz_path):
    ctrl = tmp_path / "pts.txt"
    ctrl.write_text("P1 1 2 3\nP2 4 5 6\n")
    assert classify_file(ctrl) == "control"
    big = tmp_path / "cloud.txt"
    shutil.copy(xyz_path, big)
    with open(big, "a") as fh:
        fh.write("0 0 0 0 0 0\n" * 300_000)  # > 5 MB
    assert classify_file(big) == "cloud"
