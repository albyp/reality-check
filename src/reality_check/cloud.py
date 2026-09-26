"""Point cloud access (LAS, LAZ, ASCII XYZ).

Point clouds are streamed in chunks. One pass collects the points inside a
square window around every control point, plus class counts for the whole
cloud (used for the class on/off list).
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import laspy
import numpy as np
from pyproj import CRS

LAS_SUFFIXES = {".las", ".laz"}
XYZ_SUFFIXES = {".xyz", ".txt", ".csv", ".pts"}

# ASPRS standard class names (LAS 1.4, R15).
CLASS_NAMES = {
    0: "Created, never classified",
    1: "Unclassified",
    2: "Ground",
    3: "Low vegetation",
    4: "Medium vegetation",
    5: "High vegetation",
    6: "Building",
    7: "Low point (noise)",
    8: "Model key point",
    9: "Water",
    10: "Rail",
    11: "Road surface",
    12: "Overlap",
    13: "Wire guard",
    14: "Wire conductor",
    15: "Transmission tower",
    16: "Wire connector",
    17: "Bridge deck",
    18: "High noise",
}


@dataclass
class Chunk:
    x: np.ndarray
    y: np.ndarray
    z: np.ndarray
    rgb: np.ndarray | None = None  # (N, 3) uint8
    intensity: np.ndarray | None = None
    classification: np.ndarray | None = None

    def __len__(self) -> int:
        return len(self.x)

    def subset(self, mask: np.ndarray) -> Chunk:
        pick = lambda a: None if a is None else a[mask]  # noqa: E731
        return Chunk(self.x[mask], self.y[mask], self.z[mask], pick(self.rgb), pick(self.intensity), pick(self.classification))

    @staticmethod
    def concat(chunks: list[Chunk]) -> Chunk:
        if not chunks:
            e = np.empty(0)
            return Chunk(e, e, e)
        cat = lambda name: None if getattr(chunks[0], name) is None else np.concatenate([getattr(c, name) for c in chunks])  # noqa: E731
        return Chunk(cat("x"), cat("y"), cat("z"), cat("rgb"), cat("intensity"), cat("classification"))


@dataclass
class CloudInfo:
    path: str
    point_count: int | None
    bounds: tuple[float, float, float, float] | None  # minx, miny, maxx, maxy
    crs: CRS | None
    has_rgb: bool
    has_intensity: bool


@dataclass
class XYZColumns:
    """Column indices for ASCII point clouds. r, g, b are optional."""

    x: int = 0
    y: int = 1
    z: int = 2
    r: int | None = 3
    g: int | None = 4
    b: int | None = 5


def _is_las(path: str | Path) -> bool:
    return Path(path).suffix.lower() in LAS_SUFFIXES


def cloud_info(path: str) -> CloudInfo:
    if _is_las(path):
        with laspy.open(path) as r:
            h = r.header
            try:
                crs = h.parse_crs()
            except Exception:
                crs = None
            dims = set(h.point_format.dimension_names)
            return CloudInfo(
                path,
                h.point_count,
                (h.mins[0], h.mins[1], h.maxs[0], h.maxs[1]),
                crs,
                "red" in dims,
                "intensity" in dims,
            )
    return CloudInfo(path, None, None, None, has_rgb=True, has_intensity=False)


def _rgb8(red, green, blue) -> np.ndarray:
    rgb = np.stack([np.asarray(red), np.asarray(green), np.asarray(blue)], axis=1)
    # LAS stores 16-bit colour; some writers put 8-bit values in 16-bit fields.
    if rgb.size and rgb.max() > 255:
        rgb = rgb >> 8
    return rgb.astype(np.uint8)


def iter_chunks(path: str, chunk_size: int = 5_000_000, xyz_columns: XYZColumns | None = None) -> Iterator[Chunk]:
    if _is_las(path):
        yield from _iter_las(path, chunk_size)
    else:
        yield from _iter_xyz(path, chunk_size, xyz_columns or XYZColumns())


def _iter_las(path: str, chunk_size: int) -> Iterator[Chunk]:
    with laspy.open(path) as r:
        dims = set(r.header.point_format.dimension_names)
        has_rgb = "red" in dims
        for pts in r.chunk_iterator(chunk_size):
            yield Chunk(
                np.asarray(pts.x),
                np.asarray(pts.y),
                np.asarray(pts.z),
                _rgb8(pts.red, pts.green, pts.blue) if has_rgb else None,
                np.asarray(pts.intensity),
                np.asarray(pts.classification),
            )


def _iter_xyz(path: str, chunk_size: int, cols: XYZColumns) -> Iterator[Chunk]:
    delimiter = None
    with open(path, encoding="utf-8", errors="replace") as f:
        buf: list[str] = []
        for line in f:
            s = line.strip()
            if not s or not (s[0].isdigit() or s[0] in "-+."):
                continue  # header or comment
            if delimiter is None:
                delimiter = "," if "," in s else None
            buf.append(s)
            if len(buf) >= chunk_size:
                yield _parse_xyz(buf, delimiter, cols)
                buf = []
        if buf:
            yield _parse_xyz(buf, delimiter, cols)


def _parse_xyz(lines: list[str], delimiter: str | None, cols: XYZColumns) -> Chunk:
    a = np.loadtxt(io.StringIO("\n".join(lines)), delimiter=delimiter, ndmin=2)
    rgb = None
    if cols.r is not None and a.shape[1] > max(cols.r, cols.g, cols.b):
        rgb = _rgb8(*(a[:, c].astype(np.uint16) for c in (cols.r, cols.g, cols.b)))
    return Chunk(a[:, cols.x].copy(), a[:, cols.y].copy(), a[:, cols.z].copy(), rgb)


@dataclass
class SampleResult:
    windows: dict[str, Chunk]  # point id -> points inside its window
    class_counts: dict[int, int] = field(default_factory=dict)  # whole cloud
    total_points: int = 0


def sample_windows(
    path: str,
    centres: dict[str, tuple[float, float]],
    half: float,
    chunk_size: int = 5_000_000,
    xyz_columns: XYZColumns | None = None,
) -> SampleResult:
    """Stream the cloud once. Keep points within +/- half of each centre."""
    ids = list(centres)
    cx = np.array([centres[i][0] for i in ids])
    cy = np.array([centres[i][1] for i in ids])
    if ids:
        gx0, gx1, gy0, gy1 = cx.min() - half, cx.max() + half, cy.min() - half, cy.max() + half
    else:  # class counts only
        gx0 = gy0 = np.inf
        gx1 = gy1 = -np.inf

    parts: dict[str, list[Chunk]] = {i: [] for i in ids}
    class_counts: dict[int, int] = {}
    total = 0
    for ch in iter_chunks(path, chunk_size, xyz_columns):
        total += len(ch)
        if ch.classification is not None:
            u, n = np.unique(ch.classification, return_counts=True)
            for k, v in zip(u.tolist(), n.tolist()):
                class_counts[k] = class_counts.get(k, 0) + v
        near = (ch.x >= gx0) & (ch.x <= gx1) & (ch.y >= gy0) & (ch.y <= gy1)
        if not near.any():
            continue
        sub = ch.subset(near)
        for i, pid in enumerate(ids):
            m = (np.abs(sub.x - cx[i]) <= half) & (np.abs(sub.y - cy[i]) <= half)
            if m.any():
                parts[pid].append(sub.subset(m))
    return SampleResult({pid: Chunk.concat(p) for pid, p in parts.items()}, class_counts, total)


def is_classified(class_counts: dict[int, int]) -> bool:
    """True if the cloud has classes beyond 0 (never classified) and 1 (unclassified)."""
    return any(k not in (0, 1) for k in class_counts)


def filter_classes(chunk: Chunk, classes: set[int] | None) -> Chunk:
    if classes is None or chunk.classification is None:
        return chunk
    return chunk.subset(np.isin(chunk.classification, list(classes)))
