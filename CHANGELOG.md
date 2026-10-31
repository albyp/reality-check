# Changelog

All notable changes to RealityCheck. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.0] - 2026-10-31

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
- Setup: *Clear* button. Empties the inputs, drops the results and closes
  every file RealityCheck holds open, so the files can be replaced or
  updated outside RealityCheck. Asks first if review work is unsaved.
- Report: Z comparison table (control Z against each DEM / point cloud Z,
  point by point, with dZ, above/below and the mean offset), and a line
  per surface under the summary saying whether it sits above or below the
  control on average, with the median and how many points are above and
  below (checkpoints if set, else all enabled points).
- Spot heights: an optional second CSV of surveyed ground levels with no
  physical mark. Z checks only, against the DEM and point cloud (sampled in
  the same pass over the cloud), kept apart from the control point
  statistics. The report gets a summary line per surface and a compact
  Spot heights table (dZ per surface, mean, median, SD, RMSE, counts above
  and below). Setup input, auto-detect (`*spot*`, `*_sh`), templates
  (optional: never reported missing), Batch, CSV exports (role `spot`), the
  session file, and `run --spots` on the command line.
- Average offsets use a trimmed mean, 90% by default (Excel `TRIMMEAN` with
  10%), marked with `*` and explained in a note. Set on the Settings tab or
  as `trim` in `settings.json`; `run --trim` on the command line. The summary
  CSV gets a `trimmed_mean_90` column.
- Spot heights more than 10 m from all DEM and point cloud data are
  disregarded: left out of the table and the averages, and listed under the
  table with their distance to the nearest data (also in the run log and the
  session file). The cloud's coverage is recorded on a 1 m grid in the same
  pass. Set as `spot_max_outside` in `settings.json`, on the Settings tab, or
  `run --spot-max-outside`.
- Templates: a folder can contain wildcards for the part that changes per
  project (e.g. `04_Agisoft/Results_*`), and blank file patterns mean the
  usual files for that input. A control rule never picks a spot heights file
  when another file matches.
- Template editor: an example project folder, a folder button per input that
  browses inside it and fills in the relative path, the default patterns shown
  in grey, and *Test* showing what each input finds in the example (replaces
  *Test on a folder*).
- Saved copies of the built-in templates in an existing `settings.json` get
  the spot heights rule, and their control rule skips spot height files.

### Changed
- Site overview: drawn at its printed size, so text and symbols are always
  the same size on paper (labels and legend about 7.5 pt) and the map fills
  the page width. A long, narrow site is cut into numbered strips at one
  scale: north-south sites into strips side by side (north first), east-west
  sites into strips stacked down the page (west first), up to four per page,
  with a slight overlap at each join. A long site with crowded points
  continues on more pages. A site is only cut up when that makes the map at
  least 1.5 times larger.

### Fixed
- RealityCheck's own exports (`*_RealityCheck*`) are never picked up as
  inputs when a folder is scanned.
- Saved templates now appear straight away in the Setup and Batch template
  lists (they only appeared after a restart).
- Folder pickers (project folder, batch folders, output folder) use the
  native Windows folder dialog in browser mode, in front of the browser,
  like the file dialogs fixed in 0.2.1.
- The browser-mode folder dialog now opens at the suggested folder (a
  64-bit address overflowed an untyped Windows call, so it was ignored).

## [0.2.1] - 2026-10-29

### Fixed
- Browse buttons did nothing when RealityCheck ran in a browser tab (the
  fallback when its own window cannot start). The file dialog opened behind
  the browser. Browse, Save and export now use the Windows file dialogs
  directly, owned by the browser window, so they open on top of it. A dialog
  failure now shows a message instead of doing nothing.
- When the desktop window cannot start, RealityCheck says so: a banner
  explains that it is running in the browser, that drag and drop is not
  available there, how to get the window (install the Microsoft Edge
  WebView2 Runtime), and the reason. The reason is also written to the log.

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
