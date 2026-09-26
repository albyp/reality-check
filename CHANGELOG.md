# Changelog

All notable changes to RealityCheck. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Control point loader for CSV/TXT. Recognises common survey headers
  (`Label`, `X/Easting`, `Z/Altitude`, ...), a leading `#` on the header,
  files without a header, whitespace-separated files, and an optional
  `used` / `role` column for GCP vs checkpoint.
- Orthomosaic and DEM (GeoTIFF) access with windowed reads. Files are never
  loaded whole.
- Point cloud streaming for LAS, LAZ and ASCII XYZ. One pass collects the
  points around every control point and counts classes.
- Z checks: DEM (bilinear) and point cloud (median within a radius, with a
  robust spread value).
- Point cloud class filter. A classified cloud shows on/off toggles in the
  GUI; an unclassified cloud uses all points.
- Statistics per dataset: bias (mean), SD, RMSE, max residual, split by GCP
  and checkpoint. Disabled points are excluded.
- Z and XY tolerances with per-point and per-dataset pass/fail.
- Review GUI (NiceGUI, localhost only): setup and run, point table,
  snapshots of each point in every dataset, chip width slider (0.5–10 m),
  manual target-centre pick, "Not found", reset, enable/disable, "needs
  touch-up" flag, role and note per point, keyboard N / P navigation.
- Single-file HTML report: summary cards, statistics, items to review,
  point table and a section per point with snapshots. PDF export through
  Microsoft Edge (or Chrome) in headless mode. Residuals and summary CSV
  export.
- Session files (`*.rcheck.json`) that keep all review state.
- CRS library (per user, `%APPDATA%\RealityCheck`) with export/import, and
  local mine grids (2D Helmert) for control points.
- Command line: `run`, `gui`, `classes`, `crs list|export|import`.
- Desktop window (pywebview / WebView2) with drag and drop of files or a
  whole project folder. Files are matched to inputs automatically; dropping
  a session file opens it. Browser mode remains (`gui --browser`).
- PyInstaller one-folder build (`packaging\build.ps1` →
  `dist\RealityCheck\RealityCheck.exe`). Windowed exe: opens the GUI when
  started with no arguments, logs to `%LOCALAPPDATA%\RealityCheck`, shows an
  error box on a crash, and prints to the terminal when run with arguments.

### Fixed
- The GUI server listens on 127.0.0.1 only, not on the site network.
- A second launch while RealityCheck is running no longer fails on a busy
  port: browser mode reuses the running instance or picks another port, and
  the desktop window always uses a free port.
- The build script removes PyInstaller's intermediate exe, which could not
  run on its own and closed immediately when double-clicked.
- GIS software that sets `PROJ_LIB` / `GDAL_DATA` system-wide (for example
  GEOVIA Surpac) no longer breaks CRS lookups. RealityCheck always uses the
  PROJ/GDAL data bundled with rasterio.
- CRS comparison handles GDAL's `TOWGS84` (BoundCRS) and compound CRS, so
  identical datasets are no longer reported as mismatched.
