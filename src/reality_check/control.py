"""Load control points from CSV.

Headers are matched by common survey names (for example "Label",
"X/Easting", "Z/Altitude"). A leading "#" on the header line is allowed.
Files without a header are read as id, x, y, z.

An optional role column marks each point as a GCP or a checkpoint. Metashape's
marker export calls it "Enable": 1 = the marker was used as a control point
(GCP), 0 = it was a check point. Every point stays enabled in RealityCheck;
checkpoints are the independent ones the QA needs most.

An optional code column holds the survey code (for example "CS1" for the
points of cross section 1). In a file without a header, a fifth column is
the role if every value in it is 0 or 1, and the code if it holds text.
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from reality_check.models import ControlPoint, Role

_ALIASES = {
    "id": {"id", "label", "name", "point", "pointid", "pt", "ptid", "station"},
    "x": {"x", "e", "east", "easting", "xeasting"},
    "y": {"y", "n", "north", "northing", "ynorthing"},
    "z": {"z", "h", "rl", "elev", "elevation", "height", "alt", "altitude", "zaltitude"},
    "role": {"role", "type", "used", "usage", "enable", "enabled", "gcp"},
    "code": {"code", "pcode", "fcode", "featurecode", "feature", "desc", "description", "descriptor"},
}

_GCP_VALUES = {"gcp", "control", "used", "yes", "y", "true", "1"}
_CHECK_VALUES = {"checkpoint", "check", "cp", "chk", "no", "n", "false", "0"}


def _norm(header: str) -> str:
    return re.sub(r"[^a-z]", "", header.lower())


def _match_columns(header: list[str]) -> dict[str, int]:
    cols: dict[str, int] = {}
    for i, raw in enumerate(header):
        key = _norm(raw)
        for field, names in _ALIASES.items():
            if field not in cols and key in names:
                cols[field] = i
                break
    return cols


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def parse_role(value: str) -> Role:
    v = value.strip().lower()
    if v in _GCP_VALUES:
        return Role.GCP
    if v in _CHECK_VALUES:
        return Role.CHECKPOINT
    return Role.UNKNOWN


def load_control(path: str | Path) -> list[ControlPoint]:
    text = Path(path).read_text(encoding="utf-8-sig")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise ValueError(f"{path}: no control points")

    lines[0] = lines[0].lstrip("#")
    delimiter = next((d for d in (",", ";", "\t") if d in lines[0]), None)
    if delimiter is None:  # whitespace separated
        rows = [ln.split() for ln in lines]
    else:
        rows = [[c.strip() for c in r] for r in csv.reader(lines, delimiter=delimiter)]

    first = rows[0]
    has_header = not all(_is_number(c) for c in first[1:4])
    if has_header:
        cols = _match_columns(first)
        if "id" not in cols and "code" in cols:  # "Code, E, N, Z": the code is the point name
            cols["id"] = cols.pop("code")
        missing = {"id", "x", "y", "z"} - cols.keys()
        if missing:
            raise ValueError(f"{path}: cannot find column(s) {sorted(missing)} in header {first}")
        body = rows[1:]
    else:
        cols = {"id": 0, "x": 1, "y": 2, "z": 3}
        body = rows
        fifth = [r[4] for r in body if len(r) > 4 and r[4]]
        if fifth and all(v in ("0", "1") for v in fifth):
            cols["role"] = 4
        elif fifth and not all(_is_number(v) for v in fifth):
            cols["code"] = 4

    points: list[ControlPoint] = []
    seen: set[str] = set()
    for n, r in enumerate(body, start=2 if has_header else 1):
        if len(r) <= max(cols[k] for k in ("id", "x", "y", "z")):
            raise ValueError(f"{path}: line {n} has too few columns: {r}")
        pid = r[cols["id"]]
        if pid in seen:
            raise ValueError(f"{path}: duplicate point id {pid!r} on line {n}")
        seen.add(pid)
        role = parse_role(r[cols["role"]]) if "role" in cols and cols["role"] < len(r) else Role.UNKNOWN
        code = r[cols["code"]].strip() if "code" in cols and cols["code"] < len(r) else ""
        points.append(
            ControlPoint(pid, float(r[cols["x"]]), float(r[cols["y"]]), float(r[cols["z"]]), role=role, code=code)
        )
    return points
