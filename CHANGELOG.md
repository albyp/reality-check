# Changelog

All notable changes to RealityCheck. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Project templates: for each input, a folder relative to the project
  folder plus file patterns and exclusions. Built in: *Project root*
  (default), *Exports folder* and a draft *Pix4D* layout. In Setup, choose a
  template and drop or load a project folder; files the template cannot find
  open a dialog to choose them or continue without them (orthomosaic and DEM
  are optional; control points and one dataset are required).
- `settings.json` next to the exe (or `%APPDATA%\RealityCheck` if that folder
  is read-only) with default tolerances, cloud radius, report chip width,
  the default template and all templates. Created on first run.
- Settings tab: edit defaults; add, edit, duplicate, delete and test
  templates on a folder; restore the built-in templates.
- Batch tab: run many project folders with one template. Drag folders in,
  see what each project resolved to, choose PDF/HTML/CSV and an optional
  single output folder, and get a results table with each report and a link
  to open the session in Review.
- Command line: `batch` and `templates`.

### Fixed
- RealityCheck's own exports (`*_RealityCheck*`) are never picked up as
  inputs when a folder is scanned.

## [0.2.0] - 2026-09-26

### Added
- Project title in Setup (and `--title` on the command line). Used in the
  report heading and in export names: `<title>_RealityCheck.pdf`, `.html`,
  `_residuals.csv`, `_summary.csv` and `.rcheck.json`. Defaults to the control
  file name.
- Site overview in the report: the orthomosaic (or DEM hillshade) with every
  control point plotted. Horizontal error is drawn as an exaggerated ellipse
  (semi-axes dX, dY) with a direction line, dZ as the fill colour, and a
  legend with ground scale, exaggeration factor and dZ colour bar.
- Save and open dialogs start in the control file's folder.
- Metashape's `Enable` column in a marker export sets each point's role:
  `1` = GCP (used as control), `0` = checkpoint.

### Changed
- The site overview has a white background outside the imagery, so a printed
  report uses no ink there.
- Printed reports keep each heading with its content. A table longer than a
  page prints in page-sized parts, each with its heading ("(continued…)"
  after the first) and header row; on screen it stays one table.

### Fixed
- PDF export from the desktop window. Inside the GUI process, Edge's
  launcher could exit (code 0) before its helper had written the PDF, or its
  helpers could run on long after the PDF was done, so the export failed or
  hung. Edge now runs detached, the export waits for a complete PDF (ending
  in `%%EOF`, size stable), then closes that print job's Edge processes and
  moves the file into place.
- Errors in the packaged exe are written to
  `%LOCALAPPDATA%\RealityCheck\realitycheck.log` through a dedicated log
  handler; the red error box names the error type.
- Exports from the desktop window use the window's own file dialog. The
  previous dialog could open behind the app or fail to appear, so exports
  looked like they did nothing.
- Export errors are written to the log, and a file locked by another program
  (Excel, a PDF viewer) gets a clear message.

## [0.1.0] - 2026-09-26

First public release.

### Added
- Licensed under GPL-3.0. Third-party components are listed in
  THIRD_PARTY_NOTICES.md; both files ship in the release zip.
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
