"""Work out which input each dropped file is, or find the inputs in a dropped folder.

Rules (a built-in default; user templates come later):
- Control points: .csv, or a small .txt that loads as control points.
  Names containing gcp / control / check are preferred.
- DEM: GeoTIFF with dem / dsm / dtm in the name, or a single-band raster.
- Orthomosaic: any other GeoTIFF with 3 or more bands.
- Point cloud: .las / .laz (preferred), then .xyz / .pts / large .txt.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

ROLES = ("control", "ortho", "dem", "cloud")
TIF = {".tif", ".tiff"}
LAS = {".las", ".laz"}
XYZ = {".xyz", ".pts"}
SMALL_TEXT = 5 * 1024 * 1024  # bytes; larger .txt files are treated as point clouds
MAX_DEPTH = 4
_DEM_NAME = re.compile(r"(^|[^a-z])(dem|dsm|dtm)([^a-z]|$)", re.I)
_CONTROL_NAME = re.compile(r"gcp|control|check|ctrl", re.I)


@dataclass
class Detection:
    found: dict[str, str] = field(default_factory=dict)  # role -> path
    notes: list[str] = field(default_factory=list)  # what was picked from several, what was ignored


def classify_file(path: str | Path) -> str | None:
    """Role of a single file, or None if it is not a supported input."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext == ".csv":
        return "control"
    if ext in LAS or ext in XYZ:
        return "cloud"
    if ext == ".txt":
        return "control" if p.stat().st_size < SMALL_TEXT and _loads_as_control(p) else "cloud"
    if ext in TIF:
        return "dem" if _is_dem(p) else "ortho"
    return None


def _is_dem(p: Path) -> bool:
    if _DEM_NAME.search(p.stem):
        return True
    try:
        from reality_check.raster import raster_info

        info = raster_info(str(p))
    except Exception:  # noqa: BLE001 - unreadable raster: guess from the name only
        return False
    return info.count == 1 or info.dtype.startswith("float")


def _loads_as_control(p: Path) -> bool:
    try:
        from reality_check.control import load_control

        return len(load_control(p)) > 0
    except Exception:  # noqa: BLE001
        return False


def _walk(folder: Path) -> list[Path]:
    out = []
    base_depth = len(folder.parts)
    for root, dirs, files in os.walk(folder):
        depth = len(Path(root).parts) - base_depth
        if depth >= MAX_DEPTH:
            dirs[:] = []
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        out += [Path(root) / f for f in files]
    return sorted(out)


def _rank(role: str, p: Path) -> tuple:
    """Lower sorts first: the preferred candidate for a role."""
    if role == "control":
        return (not _CONTROL_NAME.search(p.stem), len(p.parts), p.name.lower())
    if role == "cloud":
        return (p.suffix.lower() not in LAS, -p.stat().st_size, p.name.lower())
    return (-p.stat().st_size, p.name.lower())  # ortho / DEM: the largest


def detect(paths: list[str | Path]) -> Detection:
    """Map dropped files and folders to inputs. Explicit files win over files found in folders."""
    det = Detection()
    candidates: dict[str, list[Path]] = {r: [] for r in ROLES}
    explicit: dict[str, list[Path]] = {r: [] for r in ROLES}

    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in _walk(p):
                role = _classify_quiet(f)
                if role:
                    candidates[role].append(f)
        elif p.is_file():
            role = _classify_quiet(p)
            if role:
                explicit[role].append(p)
            else:
                det.notes.append(f"Ignored {p.name}: not a supported file type.")
        else:
            det.notes.append(f"Ignored {raw}: not found.")

    for role in ROLES:
        pool = explicit[role] or candidates[role]
        if not pool:
            continue
        pool = sorted(pool, key=lambda f: _rank(role, f))
        det.found[role] = str(pool[0])
        if len(pool) > 1:
            others = ", ".join(f.name for f in pool[1:4]) + (" …" if len(pool) > 4 else "")
            det.notes.append(f"{len(pool)} possible {_LABEL[role]} files; using {pool[0].name} (also found: {others}).")
    return det


def is_own_output(p: Path) -> bool:
    """RealityCheck's own exports (<title>_RealityCheck*.csv etc.) are never inputs."""
    return "_realitycheck" in p.name.lower()


def _classify_quiet(p: Path) -> str | None:
    if is_own_output(p):
        return None
    try:
        return classify_file(p)
    except OSError:
        return None


_LABEL = {"control": "control point", "ortho": "orthomosaic", "dem": "DEM", "cloud": "point cloud"}
