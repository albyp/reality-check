# RealityCheck

*QA/QC for photogrammetry and LiDAR.*

RealityCheck compares surveyed control points against the datasets delivered
from a UAV (or any photogrammetry / LiDAR) survey: orthomosaic, DEM and point
cloud. It reports vertical (Z) and horizontal (XY) error per point, with
snapshots of every point in every dataset as evidence, in one report.

The tool runs automatically first. You then review each point, correct the
target centre where needed, switch off targets that don't fit, flag faded
targets for a touch-up, and export the report.

> Status: early development. Z checks, the review GUI, manual XY picks and
> the report work. Automatic target detection is next.

## Download (Windows)

1. Download `RealityCheck-<version>-win64.zip` from the
   [Releases](https://github.com/albyp/reality-check/releases) page.
2. Extract the zip. Running the exe from inside the zip does not work: the
   `_internal` folder must stay next to `RealityCheck.exe`.
3. Double-click `RealityCheck.exe`.

The exe is not code-signed yet, so Windows SmartScreen may show "Windows
protected your PC". Click *More info*, then *Run anyway*.

## Inputs

| Input | Formats |
|---|---|
| Control points | CSV / TXT (header names such as `Label, X/Easting, Y/Northing, Z/Altitude` are recognised) |
| Orthomosaic | GeoTIFF |
| DEM | GeoTIFF |
| Point cloud | LAS, LAZ, ASCII XYZ |

All datasets in one run are assumed to share the control points' coordinate
system. RealityCheck warns if the files say otherwise.

## Using it

Double-click `RealityCheck.exe` (keep the `_internal` folder next to it), or
run `reality-check gui`. The app opens in its own window and runs only on
your own PC. `reality-check gui --browser` opens it in a browser tab instead.

1. **Setup**: drag the files, or the whole project folder, onto the window.
   Files are matched automatically (`*_dem.tif` → DEM, other `.tif` →
   orthomosaic, `.las/.laz` → point cloud, `.csv` → control points). The
   Browse buttons still work. Set tolerances, then *Run checks*. Dropping a
   `.rcheck.json` session file opens it.
2. **Review**: click a point (or press N / P). Use the chip width slider to
   zoom. Click the target centre on the orthomosaic or point cloud to set the
   measured position. *Not found*, *Reset*, *Enabled*, *Needs touch-up*,
   role and note are per point.
3. **Report**: preview the report and export PDF, HTML or CSV.
4. **Save session** keeps all review work in a `.rcheck.json` file.

### Reading the numbers

- **Residual** = dataset minus surveyed point. Positive dZ means the surface
  is above the surveyed point.
- **Bias** = mean residual: a systematic offset (for example a datum or geoid
  mix-up shows up as a large bias).
- **RMSE** combines bias and scatter: RMSE² ≈ bias² + SD².
- **GCPs** were used in processing, so their residuals are near zero by
  design. Only **checkpoints** give an independent accuracy figure. Add a
  `used` column to the control CSV (`yes` = GCP, `no` = checkpoint) or set
  the role per point in the GUI.

## Command line

```
reality-check run --control gcp.csv --ortho ortho.tif --dem dem.tif --cloud cloud.laz --out results --tol-z 0.05 --pdf
reality-check classes cloud.laz
reality-check crs list
```

`run` writes `report.html` and `session.rcheck.json` (open it in the GUI to
review). Options: `--pdf`, `--csv`, `--radius` (cloud Z search radius,
default 0.5 m), `--classes 2,11`, `--tol-z`, `--tol-xy`, `--chip-size`,
`--control-crs NAME` (control points in a local grid from the CRS library).

## Development (Windows)

```
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\reality-check gui
```

Build the distributable folder (`dist\RealityCheck\RealityCheck.exe`):

```
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

The exe has no console window. Its output goes to
`%LOCALAPPDATA%\RealityCheck\realitycheck.log`, and a crash shows an error
box. Run from a terminal with arguments (`RealityCheck.exe run ...`), it
prints to that terminal.

RealityCheck always uses the GDAL/PROJ data bundled with rasterio. Mining
software such as GEOVIA Surpac sets `PROJ_LIB` / `GDAL_DATA` system-wide to
older copies, which would otherwise break every CRS lookup.

## Tasks

Done items are listed in [CHANGELOG.md](CHANGELOG.md).

### Next
- [ ] Site templates: save a folder layout per site (for example
      `project/exports/[name.tif, name.laz, name_dem.tif]`) so dropping a
      project folder maps files by the site's rule, not the built-in guess.
- [ ] Automatic target-centre detection for painted X crosses on ortho and
      point cloud chips, with a confidence score; low confidence → "not found".
- [ ] Hide/mark GCPs vs checkpoints in the summary cards once roles are set.
- [ ] GUI: add more than one dataset of each kind.
- [ ] GUI: CRS library screen (add, edit, import, export, local grids).
- [ ] Installer (for example Inno Setup) and a GitHub release.

### Later
- [ ] ML target detector trained on confirmed picks; support other target types.
- [ ] Audit strings: surveyed lines (for example road cross-sections) compared
      against DEM / cloud profiles along the same path.
- [ ] Check grids: flat-ground point grids (for example 5 x 5) for lidar Z
      checks that do not depend on RGB alignment.
- [ ] Datum transforms for control points (GDA94 ↔ GDA2020).
- [ ] Batch QA of several surveys and trends across surveys.

## License

Copyright (C) 2026 Alby Palmer.

RealityCheck is free software: you can redistribute it and/or modify it under
the terms of the GNU General Public License version 3, as published by the
Free Software Foundation. It is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See [LICENSE](LICENSE).

Bundled third-party components and their licenses are listed in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
