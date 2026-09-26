"""Coordinate systems: dataset CRS checks, user CRS library, local mine grids.

The CRS library is a JSON file stored per user. It can be exported and
imported so definitions move between computers.

Two entry types:
- "crs": any definition pyproj accepts (EPSG code, WKT, PROJ string).
- "local_grid": a 2D similarity transform (Helmert) from a local grid to a
  base CRS, as used for mine grids.
"""

from __future__ import annotations

import json
import math
import os
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pyproj import CRS


def default_library_path() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / ".config")
    return Path(base) / "RealityCheck" / "crs_library.json"


@dataclass
class LocalGrid:
    """Local grid to base CRS.

    base = origin_base + scale * R(rotation) * (grid - origin_grid)

    rotation_deg is the angle from base grid north to local grid north,
    positive clockwise (a surveyor's bearing). z_offset is added to local
    heights to give base heights.
    """

    base_crs: str
    origin_grid: tuple[float, float]
    origin_base: tuple[float, float]
    rotation_deg: float = 0.0
    scale: float = 1.0
    z_offset: float = 0.0

    def to_base(self, x: float, y: float, z: float) -> tuple[float, float, float]:
        t = math.radians(self.rotation_deg)
        dx, dy = x - self.origin_grid[0], y - self.origin_grid[1]
        # Local north axis sits at bearing t in the base frame.
        e = self.origin_base[0] + self.scale * (dx * math.cos(t) + dy * math.sin(t))
        n = self.origin_base[1] + self.scale * (-dx * math.sin(t) + dy * math.cos(t))
        return e, n, z + self.z_offset

    def from_base(self, e: float, n: float, z: float) -> tuple[float, float, float]:
        t = math.radians(self.rotation_deg)
        de, dn = (e - self.origin_base[0]) / self.scale, (n - self.origin_base[1]) / self.scale
        x = self.origin_grid[0] + de * math.cos(t) - dn * math.sin(t)
        y = self.origin_grid[1] + de * math.sin(t) + dn * math.cos(t)
        return x, y, z - self.z_offset


@dataclass
class CRSEntry:
    name: str
    kind: str  # "crs" | "local_grid"
    definition: str = ""  # kind == "crs"
    grid: LocalGrid | None = None  # kind == "local_grid"
    vertical: str = ""  # free text, e.g. "AHD (AUSGeoid2020)"
    note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.grid is None:
            d.pop("grid")
        return d

    @classmethod
    def from_dict(cls, d: dict) -> CRSEntry:
        grid = d.get("grid")
        if grid is not None:
            grid = LocalGrid(
                base_crs=grid["base_crs"],
                origin_grid=tuple(grid["origin_grid"]),
                origin_base=tuple(grid["origin_base"]),
                rotation_deg=grid.get("rotation_deg", 0.0),
                scale=grid.get("scale", 1.0),
                z_offset=grid.get("z_offset", 0.0),
            )
        return cls(
            name=d["name"],
            kind=d["kind"],
            definition=d.get("definition", ""),
            grid=grid,
            vertical=d.get("vertical", ""),
            note=d.get("note", ""),
        )

    def validate(self) -> None:
        if self.kind == "crs":
            CRS.from_user_input(self.definition)
        elif self.kind == "local_grid":
            if self.grid is None:
                raise ValueError(f"{self.name}: local_grid entry has no grid parameters")
            CRS.from_user_input(self.grid.base_crs)
            if self.grid.scale <= 0:
                raise ValueError(f"{self.name}: scale must be positive")
        else:
            raise ValueError(f"{self.name}: unknown kind {self.kind!r}")


@dataclass
class CRSLibrary:
    path: Path = field(default_factory=default_library_path)
    entries: dict[str, CRSEntry] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None = None) -> CRSLibrary:
        lib = cls(path or default_library_path())
        if lib.path.exists():
            lib.entries = _read_entries(lib.path)
        return lib

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        _write_entries(self.path, self.entries.values())

    def add(self, entry: CRSEntry, overwrite: bool = False) -> None:
        entry.validate()
        if entry.name in self.entries and not overwrite:
            raise ValueError(f"CRS {entry.name!r} already exists")
        self.entries[entry.name] = entry

    def remove(self, name: str) -> None:
        del self.entries[name]

    def export(self, dest: Path, names: list[str] | None = None) -> None:
        chosen = [self.entries[n] for n in names] if names else list(self.entries.values())
        _write_entries(dest, chosen)

    def import_file(self, src: Path, overwrite: bool = False) -> list[str]:
        """Merge entries from src. Returns names that were skipped."""
        skipped = []
        for name, entry in _read_entries(src).items():
            if name in self.entries and not overwrite:
                skipped.append(name)
                continue
            self.add(entry, overwrite=True)
        return skipped


def _read_entries(path: Path) -> dict[str, CRSEntry]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = [CRSEntry.from_dict(d) for d in data.get("entries", [])]
    return {e.name: e for e in entries}


def _write_entries(path: Path, entries) -> None:
    payload = {"format": "reality-check-crs", "version": 1, "entries": [e.to_dict() for e in entries]}
    Path(path).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def same_horizontal_crs(a: CRS | None, b: CRS | None) -> bool:
    """True if a and b describe the same horizontal CRS.

    Survey software often writes a custom WKT (for example "GDA2020 / MGA
    zone 53 AUSGeoid2020") with no EPSG code. Strict equality then fails, so
    fall back to the matched EPSG code, then to the PROJ string.
    """
    if a is None or b is None:
        return a is b
    a, b = _horizontal(a), _horizontal(b)
    if a.equals(b, ignore_axis_order=True):
        return True
    ea, eb = a.to_epsg(min_confidence=20), b.to_epsg(min_confidence=20)
    if ea is not None and ea == eb:
        return True
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return a.to_proj4() == b.to_proj4()


def _horizontal(c: CRS) -> CRS:
    # GDAL adds TOWGS84 to many WKTs, which pyproj reads as a BoundCRS wrapper.
    if c.is_bound and c.source_crs is not None:
        c = c.source_crs
    if c.is_compound:
        c = c.sub_crs_list[0]
        if c.is_bound and c.source_crs is not None:
            c = c.source_crs
    return c


def describe(c: CRS | None) -> str:
    if c is None:
        return "none"
    epsg = _horizontal(c).to_epsg(min_confidence=20)
    return f"{c.name} (EPSG:{epsg})" if epsg else c.name
