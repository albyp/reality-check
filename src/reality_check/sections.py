"""Cross sections: surveyed strings compared with the surface along the same line.

A cross section is a string of spot heights that share a code, for example
CS1, CS2 (the pattern is settings.section_codes, "CS*" by default). The
points are joined in the order they appear in the file, which is the order
they were surveyed. Sections are most useful across ground with shape:
kerbs, windrows, drains, bund crests. There, a surface that sits too high,
too low or shifted sideways shows clearly, where a flat spot height cannot.

Per section and surface:
- dZ at each surveyed point. For a point cloud this is the median of the
  corridor points at that chainage (the corridor follows the string, so a
  kerb top is not averaged with the road beside it, as the round cloud Z
  window of a spot height would).
- The profile along the string: the DEM sampled every PROFILE_STEP, and the
  cloud points within the corridor (settings.section_width wide).
- A best fit: how far along the section, and how far up or down, the surface
  has to move to match the surveyed string. Along-section shift is a
  horizontal check without a target. It is only reported where the section
  has enough relief (MIN_RELIEF) to pin it down.

Cloud corridor points are kept in the shared windows dict under
SECTION_PREFIX, so the cloud is still streamed once.
"""

from __future__ import annotations

import fnmatch
import math
from dataclasses import dataclass, field

import numpy as np
import rasterio

from reality_check.cloud import Chunk, filter_classes
from reality_check.models import ControlPoint, DatasetKind
from reality_check.raster import sample_bilinear
from reality_check.session import Session

SECTION_PREFIX = "section:"  # windows: section:<code>:<n>
WINDOW_STEP = 1.0  # m between the square windows that cover a section's corridor
PROFILE_STEP = 0.05  # m between DEM samples along a section
BIN = 0.1  # m; cloud profile: median Z per chainage bin
MAX_SHIFT = 0.5  # m; best-fit search range along the section
SHIFT_STEP = 0.005  # m
MIN_RELIEF = 0.10  # m of shape about a straight line; with less, a shift along the section cannot be pinned down
MAX_DRAWN_POINTS = 4000  # cloud points per section in the report chart


@dataclass
class Section:
    code: str
    nodes: list[ControlPoint]  # in file (survey) order
    chainage: np.ndarray = field(default_factory=lambda: np.zeros(0))  # m along the string, per node

    def __post_init__(self):
        x, y = self.xy.T
        self.chainage = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(x), np.diff(y)))])

    @property
    def xy(self) -> np.ndarray:
        return np.array([(p.x, p.y) for p in self.nodes], float).reshape(-1, 2)

    @property
    def length(self) -> float:
        return float(self.chainage[-1])

    @property
    def relief(self) -> float:
        """Height range of the surveyed points about a straight line fitted along the section. A steady slope
        has none: shifting a straight slope along itself only looks like a vertical offset."""
        z = np.array([p.z for p in self.nodes])
        if len(z) < 3 or self.length == 0:
            return 0.0
        a, b = np.polyfit(self.chainage, z, 1)
        r = z - (a * self.chainage + b)
        return float(r.max() - r.min())

    def at(self, ch: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Plan position at chainage ch along the string (extrapolated straight past either end)."""
        x, y = self.xy.T
        ch = np.atleast_1d(np.asarray(ch, float))
        i = np.clip(np.searchsorted(self.chainage, ch, side="right") - 1, 0, len(x) - 2)
        seg = np.maximum(self.chainage[i + 1] - self.chainage[i], 1e-9)
        t = (ch - self.chainage[i]) / seg
        return x[i] + t * (x[i + 1] - x[i]), y[i] + t * (y[i + 1] - y[i])

    def project(self, px: np.ndarray, py: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Chainage and perpendicular offset (m, + to the right looking along the string) of points."""
        x, y = self.xy.T
        best_d = np.full(len(px), np.inf)
        ch = np.zeros(len(px))
        off = np.zeros(len(px))
        for i in range(len(x) - 1):
            dx, dy = x[i + 1] - x[i], y[i + 1] - y[i]
            seg2 = dx * dx + dy * dy
            if seg2 == 0:
                continue
            t = ((px - x[i]) * dx + (py - y[i]) * dy) / seg2
            # Past either end of the string the first / last segment carries on straight.
            lo = -np.inf if i == 0 else 0.0
            hi = np.inf if i == len(x) - 2 else 1.0
            t = np.clip(t, lo, hi)
            qx, qy = x[i] + t * dx, y[i] + t * dy
            d = np.hypot(px - qx, py - qy)
            better = d < best_d
            seg = math.sqrt(seg2)
            best_d[better] = d[better]
            ch[better] = self.chainage[i] + t[better] * seg
            off[better] = ((px - x[i]) * dy - (py - y[i]) * dx)[better] / seg
        return ch, off

    def bearing(self) -> float:
        """Whole-circle bearing (degrees from grid north) from the first point to the last."""
        (x0, y0), (x1, y1) = self.xy[0], self.xy[-1]
        return math.degrees(math.atan2(x1 - x0, y1 - y0)) % 360


def code_matches(code: str, patterns: str) -> bool:
    """patterns: comma-separated wildcards, case-insensitive (e.g. "CS*, XS*")."""
    code = code.strip().upper()
    return bool(code) and any(fnmatch.fnmatchcase(code, p.strip().upper()) for p in patterns.split(",") if p.strip())


def find_sections(spots: list[ControlPoint], patterns: str) -> list[Section]:
    """Spot heights whose code matches, grouped by code, in file order. A code with one point is not a section."""
    groups: dict[str, list[ControlPoint]] = {}
    for p in spots:
        if code_matches(p.code, patterns):
            groups.setdefault(p.code.strip(), []).append(p)
    return [Section(code, nodes) for code, nodes in groups.items() if len(nodes) >= 2]


def section_ids(session: Session) -> set[str]:
    return {p.id for s in find_sections(session.spots, session.settings.section_codes) for p in s.nodes}


def plain_spots(session: Session) -> list[ControlPoint]:
    """Spot heights that are not part of a cross section."""
    ids = section_ids(session)
    return [p for p in session.spots if p.id not in ids]


def corridor_windows(section: Section, width: float) -> tuple[dict[str, tuple[float, float]], float]:
    """Square cloud windows along the string that together cover the corridor, and their half size."""
    n = max(int(math.ceil(section.length / WINDOW_STEP)), 1)
    x, y = section.at(np.linspace(0, section.length, n + 1))
    half = WINDOW_STEP / 2 + width / 2 + 0.05
    return {f"{SECTION_PREFIX}{section.code}:{i}": (float(x[i]), float(y[i])) for i in range(n + 1)}, half


def corridor_points(section: Section, windows: dict[str, Chunk], width: float,
                    classes: set[int] | None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cloud points within width/2 of the string: chainage, offset, z (duplicates from overlapping windows removed)."""
    prefix = f"{SECTION_PREFIX}{section.code}:"
    parts = [filter_classes(c, classes) for k, c in windows.items() if k.startswith(prefix)]
    parts = [c for c in parts if len(c)]
    if not parts:
        return np.zeros(0), np.zeros(0), np.zeros(0)
    xyz = np.unique(np.column_stack([np.concatenate([c.x for c in parts]), np.concatenate([c.y for c in parts]),
                                     np.concatenate([c.z for c in parts])]), axis=0)
    ch, off = section.project(xyz[:, 0], xyz[:, 1])
    keep = (np.abs(off) <= width / 2) & (ch >= -MAX_SHIFT) & (ch <= section.length + MAX_SHIFT)
    return ch[keep], off[keep], xyz[keep, 2]


@dataclass
class Profile:
    """One surface along one section."""
    dataset_id: str
    kind: DatasetKind
    ch: np.ndarray  # surface line: chainage
    z: np.ndarray  # surface line: Z (NaN where there is no data)
    points_ch: np.ndarray = field(default_factory=lambda: np.zeros(0))  # cloud: corridor points
    points_z: np.ndarray = field(default_factory=lambda: np.zeros(0))

    def z_at(self, ch) -> np.ndarray:
        ok = np.isfinite(self.z)
        if ok.sum() < 2:
            return np.full(np.shape(ch), np.nan)
        c, z = self.ch[ok], self.z[ok]
        out = np.interp(ch, c, z, left=np.nan, right=np.nan)
        # No interpolating across gaps in the data wider than two steps.
        step = np.median(np.diff(self.ch)) if len(self.ch) > 1 else BIN
        j = np.clip(np.searchsorted(c, ch), 1, len(c) - 1)
        out[(c[j] - c[j - 1]) > 2.5 * step] = np.nan
        return out

    def z_near(self, ch) -> np.ndarray:
        """Surface Z at given chainages. Cloud: median of the corridor points within BIN/2 of
        each chainage (at least 3 points), centred on it rather than on a fixed bin. DEM: z_at."""
        ch = np.atleast_1d(np.asarray(ch, float))
        if not len(self.points_ch):
            return self.z_at(ch)
        out = np.full(len(ch), np.nan)
        for i, c in enumerate(ch):
            z = self.points_z[np.abs(self.points_ch - c) <= BIN / 2]
            if len(z) >= 3:
                out[i] = np.median(z)
        return out


def dem_profile(section: Section, ds_id: str, path: str) -> Profile:
    with rasterio.open(path) as r:
        step = max(PROFILE_STEP, min(abs(r.res[0]), abs(r.res[1])) / 4)
        ch = np.arange(-MAX_SHIFT, section.length + MAX_SHIFT + step / 2, step)
        x, y = section.at(ch)
        z = np.array([sample_bilinear(r, float(a), float(b)) for a, b in zip(x, y)], dtype=object)
    return Profile(ds_id, DatasetKind.DEM, ch, np.array([np.nan if v is None else v for v in z], float))


def cloud_profile(section: Section, ds_id: str, windows: dict[str, Chunk], width: float,
                  classes: set[int] | None) -> Profile:
    ch, _, z = corridor_points(section, windows, width, classes)
    edges = np.arange(-MAX_SHIFT, section.length + MAX_SHIFT + BIN, BIN)
    centres = (edges[:-1] + edges[1:]) / 2
    med = np.full(len(centres), np.nan)
    if len(ch):
        idx = np.digitize(ch, edges) - 1
        order = np.argsort(idx)
        idx, zs = idx[order], z[order]
        bounds = np.searchsorted(idx, np.arange(len(centres) + 1))
        for b in range(len(centres)):
            if bounds[b + 1] - bounds[b] >= 3:
                med[b] = np.median(zs[bounds[b]:bounds[b + 1]])
    return Profile(ds_id, DatasetKind.CLOUD, centres, med, ch, z)


@dataclass
class Fit:
    """How the surface compares with one surveyed section."""
    n: int  # surveyed points with a surface value
    mean: float  # dZ = surface - surveyed, m
    rmse: float
    max_abs: float
    shift: float | None = None  # m along the section that best matches the shapes (None = not determinable)
    vertical: float | None = None  # m: the vertical offset left after that shift
    sd_before: float | None = None  # spread of dZ about its mean, as surveyed
    sd_after: float | None = None  # and after the shift
    reason: str = ""  # why there is no shift

    @property
    def significant(self) -> bool:
        """The shift tightens the match clearly; otherwise the scatter is noise, not a shift."""
        return self.shift is not None and self.sd_before is not None and self.sd_after <= SIGNIFICANT * self.sd_before


SIGNIFICANT = 0.8  # a shift counts when it cuts the spread of dZ by at least 20%


def node_dz(section: Section, profile: Profile) -> np.ndarray:
    z = np.array([p.z for p in section.nodes])
    return profile.z_near(section.chainage) - z


def fit(section: Section, profile: Profile) -> Fit | None:
    enabled = np.array([p.enabled for p in section.nodes])
    dz = node_dz(section, profile)
    ok = enabled & np.isfinite(dz)
    if ok.sum() == 0:
        return None
    d = dz[ok]
    f = Fit(int(ok.sum()), float(d.mean()), float(np.sqrt((d ** 2).mean())), float(np.abs(d).max()))
    if section.relief < MIN_RELIEF:
        f.reason = f"the section has too little shape ({section.relief * 1000:.0f} mm about a straight line)"
        return f
    if ok.sum() < 4:
        f.reason = "too few surveyed points"
        return f
    ch = section.chainage[ok]
    z = np.array([p.z for p in section.nodes])[ok]
    shifts = np.arange(-MAX_SHIFT, MAX_SHIFT + SHIFT_STEP / 2, SHIFT_STEP)
    sds = np.full(len(shifts), np.inf)
    for i, s in enumerate(shifts):
        r = profile.z_at(ch + s) - z
        if np.isfinite(r).all():
            sds[i] = r.std()
    if not np.isfinite(sds).any():
        f.reason = "the surface does not cover the section"
        return f
    best = int(np.argmin(sds))
    zero = int(np.argmin(np.abs(shifts)))
    f.sd_before = float(sds[zero]) if np.isfinite(sds[zero]) else float(d.std())  # same profile as the search
    if best in (0, len(shifts) - 1) or not np.isfinite(sds[[max(best - 1, 0), min(best + 1, len(sds) - 1)]]).all():
        f.reason = f"no clear best fit within ±{MAX_SHIFT:g} m"
        return f
    f.shift = float(shifts[best])
    f.sd_after = float(sds[best])
    f.vertical = float(np.mean(profile.z_at(ch + f.shift) - z))
    return f


@dataclass
class SectionResult:
    section: Section
    profiles: list[Profile]
    fits: dict[str, Fit | None]  # dataset id -> fit
    dz: dict[str, np.ndarray]  # dataset id -> dZ per node (NaN = no data)


def summary_line(code: str, surface: str, f: Fit | None) -> str:
    """One line, e.g. "CS1 vs DEM: dZ -7 mm, RMSE 25 mm (14 points); no significant shift along"."""
    if f is None:
        return f"{code} vs {surface}: no surface data along the section"
    line = f"{code} vs {surface}: dZ {f.mean * 1000:+.0f} mm, RMSE {f.rmse * 1000:.0f} mm ({f.n} points)"
    if f.shift is None:
        return f"{line}; no along-section fit ({f.reason})"
    if f.significant:
        return f"{line}; best fit {f.shift * 1000:+.0f} mm along, {f.vertical * 1000:+.0f} mm vertical"
    return f"{line}; no significant shift along"


def section_lines(session: Session, windows: dict[str, dict[str, Chunk]], label=None) -> list[str]:
    """summary_line for every section and surface; label(dataset) names the surface (default its id)."""
    names = {d.id: (label(d) if label else d.id) for d in session.datasets}
    return [summary_line(r.section.code, names[ds_id], f)
            for r in analyse(session, windows) for ds_id, f in r.fits.items()]


def analyse(session: Session, windows: dict[str, dict[str, Chunk]]) -> list[SectionResult]:
    s = session.settings
    classes = set(s.cloud_classes) if s.cloud_classes is not None else None
    out = []
    for sec in find_sections(session.spots, s.section_codes):
        profiles = []
        for ds in session.datasets:
            if ds.kind is DatasetKind.DEM:
                profiles.append(dem_profile(sec, ds.id, ds.path))
            elif ds.kind is DatasetKind.CLOUD:
                profiles.append(cloud_profile(sec, ds.id, windows.get(ds.id, {}), s.section_width, classes))
        out.append(SectionResult(sec, profiles, {p.dataset_id: fit(sec, p) for p in profiles},
                                 {p.dataset_id: node_dz(sec, p) for p in profiles}))
    return out
