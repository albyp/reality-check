"""Command line interface.

    reality-check gui
    reality-check run --control gcp.csv --ortho ortho.tif --dem dem.tif --cloud cloud.laz --out results
    reality-check classes cloud.laz
    reality-check crs list | export FILE | import FILE
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from reality_check import __version__
from reality_check.cloud import CLASS_NAMES, is_classified, sample_windows
from reality_check.crs import CRSLibrary
from reality_check.export import write_residuals, write_summary
from reality_check.pipeline import make_datasets, run
from reality_check.render import ChipRenderer
from reality_check.report import write_html, write_pdf
from reality_check.session import SESSION_SUFFIX, Settings
from reality_check.stats import summarise


def _ints(s: str) -> list[int]:
    return [int(v) for v in s.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="reality-check", description="QA/QC for photogrammetry and LiDAR")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run checks and write results")
    r.add_argument("--control", required=True, help="control point CSV")
    r.add_argument("--ortho", action="append", default=[], help="orthomosaic GeoTIFF (repeatable)")
    r.add_argument("--dem", action="append", default=[], help="DEM GeoTIFF (repeatable)")
    r.add_argument("--cloud", action="append", default=[], help="point cloud LAS/LAZ/XYZ (repeatable)")
    r.add_argument("--out", required=True, type=Path, help="output folder")
    r.add_argument("--radius", type=float, default=0.5, help="cloud Z search radius in metres (default 0.5)")
    r.add_argument("--classes", type=_ints, default=None, help="cloud classes to use, e.g. 2,11 (default all)")
    r.add_argument("--control-crs", default=None, help="CRS library entry the control points are in")
    r.add_argument("--tol-z", type=float, default=None, help="Z tolerance in metres, e.g. 0.05")
    r.add_argument("--tol-xy", type=float, default=None, help="XY tolerance in metres, e.g. 0.05")
    r.add_argument("--chip-size", type=float, default=2.0, help="report chip width in metres (default 2)")
    r.add_argument("--pdf", action="store_true", help="also write report.pdf")
    r.add_argument("--csv", action="store_true", help="also write residuals.csv and summary.csv")

    g = sub.add_parser("gui", help="open the review GUI (desktop window)")
    g.add_argument("--browser", action="store_true", help="use a browser tab instead of the desktop window")
    g.add_argument("--port", type=int, default=8765, help="browser mode port (default 8765)")
    g.add_argument("--no-browser", action="store_true", help="browser mode: do not open a tab")

    c = sub.add_parser("classes", help="list classes in a point cloud")
    c.add_argument("cloud")

    k = sub.add_parser("crs", help="manage the CRS library")
    ksub = k.add_subparsers(dest="crs_cmd", required=True)
    ksub.add_parser("list")
    e = ksub.add_parser("export")
    e.add_argument("file", type=Path)
    i = ksub.add_parser("import")
    i.add_argument("file", type=Path)
    i.add_argument("--overwrite", action="store_true")
    return ap


def cmd_run(a) -> int:
    datasets = make_datasets(a.ortho, a.dem, a.cloud)
    if not datasets:
        print("error: give at least one --ortho, --dem or --cloud", file=sys.stderr)
        return 2
    settings = Settings(
        cloud_radius=a.radius, cloud_classes=a.classes, control_crs=a.control_crs,
        tol_z=a.tol_z, tol_xy=a.tol_xy, report_chip_size=a.chip_size,
    )
    a.out.mkdir(parents=True, exist_ok=True)
    result = run(a.control, datasets, settings)
    s = result.session
    s.save(a.out / f"session{SESSION_SUFFIX}")
    renderer = ChipRenderer(s, result.windows)
    try:
        write_html(s, renderer, a.out / "report.html", result.warnings)
        if a.pdf:
            write_pdf(s, renderer, a.out / "report.pdf", result.warnings)
    finally:
        renderer.close()
    if a.csv:
        write_residuals(s, a.out / "residuals.csv")
        write_summary(s, a.out / "summary.csv")

    print()
    print(f"{'dataset':38} {'check':5} {'comp':4} {'group':10} {'n':>3} {'mean':>8} {'sd':>8} {'rmse':>8}")
    for row in summarise(s.points, s.observations):
        st = row.stats
        print(f"{row.dataset_id:38} {row.check.value:5} {row.component:4} {row.group:10} {st.n:3d} "
              f"{st.mean:8.3f} {st.sd:8.3f} {st.rmse:8.3f}")
    bad = [o for o in s.observations if o.status.value != "ok"]
    for o in bad:
        print(f"  {o.point_id} in {o.dataset_id}: {o.status.value}")
    print(f"\nResults written to {a.out}")
    return 0


def cmd_classes(a) -> int:
    res = sample_windows(a.cloud, {}, 0)
    counts = res.class_counts
    print(f"{res.total_points:,} points")
    for k in sorted(counts):
        print(f"  {k:3d}  {CLASS_NAMES.get(k, 'User defined'):28} {counts[k]:>14,}")
    if not is_classified(counts):
        print("Cloud is not classified: all points will be used.")
    return 0


def cmd_crs(a) -> int:
    lib = CRSLibrary.load()
    if a.crs_cmd == "list":
        print(f"CRS library: {lib.path}")
        for e in lib.entries.values():
            what = e.definition if e.kind == "crs" else f"local grid on {e.grid.base_crs}"
            print(f"  {e.name}: {what} {e.vertical}")
    elif a.crs_cmd == "export":
        lib.export(a.file)
        print(f"Exported {len(lib.entries)} entries to {a.file}")
    elif a.crs_cmd == "import":
        skipped = lib.import_file(a.file, overwrite=a.overwrite)
        lib.save()
        print(f"Imported into {lib.path}" + (f"; skipped existing: {', '.join(skipped)}" if skipped else ""))
    return 0


def cmd_gui(a) -> int:
    from reality_check.gui import start, start_desktop

    if a.browser or a.no_browser:
        start(port=a.port, show=not a.no_browser)
    else:
        start_desktop()
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    double_clicked = not argv
    if double_clicked:  # started from Explorer: open the GUI
        argv = ["gui"]
    a = build_parser().parse_args(argv)
    try:
        return {"run": cmd_run, "classes": cmd_classes, "crs": cmd_crs, "gui": cmd_gui}[a.cmd](a)
    except Exception:
        if not double_clicked or getattr(sys, "frozen", False):  # the frozen exe shows its own error box
            raise
        # Keep the console open so the error can be read, and keep a copy on disk.
        import traceback

        log = _crash_log(traceback.format_exc())
        print(traceback.format_exc())
        print(f"RealityCheck stopped with an error. A copy is saved in {log}")
        input("Press Enter to close...")
        return 1


def _crash_log(text: str) -> Path:
    import os
    from datetime import datetime

    folder = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "RealityCheck"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "crash.log"
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} ---\n{text}")
    return path
