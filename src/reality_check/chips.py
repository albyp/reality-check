"""Render image chips centred on a control point.

Every dataset becomes a square 2D image of side 2*half metres, so the UI and
report handle ortho, DEM and point cloud the same way. North is up.
"""

from __future__ import annotations

import math

import numpy as np
import rasterio
from PIL import Image, ImageDraw, ImageFont
from rasterio.enums import Resampling

from reality_check.cloud import Chunk
from reality_check.raster import read_window

BACKGROUND = (40, 40, 40)
CROSSHAIR = (255, 40, 40)
OBSERVED = (0, 220, 255)

# Elevation colour ramp (low -> high).
_RAMP = np.array([[49, 54, 149], [69, 117, 180], [116, 173, 209], [171, 217, 233], [254, 224, 144],
                  [253, 174, 97], [244, 109, 67], [215, 48, 39]], dtype=float)


def _ramp(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0, 1) * (len(_RAMP) - 1)
    i = np.minimum(t.astype(int), len(_RAMP) - 2)
    f = (t - i)[..., None]
    return _RAMP[i] * (1 - f) + _RAMP[i + 1] * f


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def ortho_chip(ds: rasterio.DatasetReader, cx: float, cy: float, half: float, px: int = 400) -> Image.Image:
    bands = [1, 2, 3] if ds.count >= 3 else [1]
    data = read_window(ds, cx, cy, half, px, bands)
    mask = np.ma.getmaskarray(data).any(axis=0)
    if ds.count >= 4:  # alpha band
        alpha = read_window(ds, cx, cy, half, px, [4], Resampling.nearest)
        mask |= np.ma.filled(alpha[0], 0) == 0
    rgb = np.ma.filled(data, 0).astype(np.uint8)
    rgb = np.repeat(rgb, 3, axis=0) if rgb.shape[0] == 1 else rgb
    rgb = np.moveaxis(rgb, 0, -1).copy()
    rgb[mask] = BACKGROUND
    return Image.fromarray(rgb)


def hillshade(z: np.ndarray, cell: float, azimuth: float = 315, altitude: float = 45) -> np.ndarray:
    gy, gx = np.gradient(z, cell)
    slope = np.arctan(np.hypot(gx, gy))
    aspect = np.arctan2(-gx, gy)
    az, alt = math.radians(360 - azimuth + 90), math.radians(altitude)
    hs = np.sin(alt) * np.cos(slope) + np.cos(alt) * np.sin(slope) * np.cos(az - aspect)
    return np.clip(hs, 0, 1)


def dem_chip(ds: rasterio.DatasetReader, cx: float, cy: float, half: float, px: int = 400) -> Image.Image:
    data = read_window(ds, cx, cy, half, px, [1])[0]
    mask = np.ma.getmaskarray(data)
    z = np.ma.filled(data.astype(float), np.nan)
    if mask.all():
        return Image.new("RGB", (px, px), BACKGROUND)
    zf = np.where(mask, np.nanmedian(z), z)
    lo, hi = np.nanpercentile(z, 2), np.nanpercentile(z, 98)
    colour = _ramp((zf - lo) / max(hi - lo, 1e-6))
    shade = hillshade(zf, 2 * half / px)[..., None]
    rgb = (colour * (0.35 + 0.65 * shade)).astype(np.uint8)
    rgb[mask] = BACKGROUND
    return Image.fromarray(rgb)


def _cloud_cell(n_points: int, half: float, px: int) -> float:
    """Raster cell size: about the point spacing, so the chip has few gaps."""
    density = max(n_points, 1) / (2 * half) ** 2
    spacing = 1 / math.sqrt(density)
    return max(2 * half / px, spacing * 1.4)


def _fill_gaps(img: np.ndarray, empty: np.ndarray, passes: int = 2) -> np.ndarray:
    """Fill empty cells from any filled 4-neighbour. Only closes small holes."""
    for _ in range(passes):
        if not empty.any():
            break
        n_r, n_c = empty.shape
        for dr, dc in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            # dst[r, c] takes src[r - dr, c - dc]; no wrap at the edges.
            dst = (slice(max(dr, 0), n_r + min(dr, 0)), slice(max(dc, 0), n_c + min(dc, 0)))
            src = (slice(max(-dr, 0), n_r + min(-dr, 0)), slice(max(-dc, 0), n_c + min(-dc, 0)))
            take = empty[dst] & ~empty[src]
            img[dst][take] = img[src][take]
            empty[dst][take] = False
    return img


def cloud_topdown(chunk: Chunk, cx: float, cy: float, half: float, px: int = 400, mode: str = "rgb") -> Image.Image:
    """Top-down render. Each cell shows its highest point.

    mode: "rgb", "intensity" or "elevation".
    """
    if len(chunk) == 0:
        return Image.new("RGB", (px, px), BACKGROUND)
    if mode == "rgb" and chunk.rgb is None:
        mode = "elevation"
    if mode == "intensity" and chunk.intensity is None:
        mode = "elevation"

    inside = (np.abs(chunk.x - cx) < half) & (np.abs(chunk.y - cy) < half)
    cell = _cloud_cell(int(inside.sum()), half, px)
    n = max(1, math.ceil(2 * half / cell))
    col = ((chunk.x - (cx - half)) / (2 * half) * n).astype(int)
    row = (((cy + half) - chunk.y) / (2 * half) * n).astype(int)
    keep = inside & (col >= 0) & (col < n) & (row >= 0) & (row < n)
    col, row = col[keep], row[keep]
    z = chunk.z[keep]

    if mode == "rgb":
        colours = chunk.rgb[keep].astype(float)
    elif mode == "intensity":
        i = chunk.intensity[keep].astype(float)
        lo, hi = np.percentile(i, 2), np.percentile(i, 98)
        g = np.clip((i - lo) / max(hi - lo, 1e-6), 0, 1) * 255
        colours = np.stack([g, g, g], axis=1)
    else:
        lo, hi = np.percentile(z, 2), np.percentile(z, 98)
        colours = _ramp((z - lo) / max(hi - lo, 1e-6))

    img = np.empty((n, n, 3), dtype=float)
    img[:] = BACKGROUND
    empty = np.ones((n, n), dtype=bool)
    order = np.argsort(z)  # highest point written last wins
    img[row[order], col[order]] = colours[order]
    empty[row, col] = False
    img = _fill_gaps(img, empty)
    return Image.fromarray(img.astype(np.uint8)).resize((px, px), Image.NEAREST)


def cloud_profile(
    chunk: Chunk,
    cx: float,
    cy: float,
    half: float,
    z_ref: float,
    px: int = 400,
    band: float = 0.25,
    axis: str = "ew",
) -> Image.Image:
    """Side view through the control point.

    axis "ew" cuts west to east (points within +/- band of the northing),
    "ns" cuts south to north. The red line marks the surveyed Z. The
    vertical scale is exaggerated to fit the local Z range.
    """
    h = px // 2
    img = Image.new("RGB", (px, h), BACKGROUND)
    draw = ImageDraw.Draw(img)
    if axis == "ew":
        sel = (np.abs(chunk.y - cy) <= band) & (np.abs(chunk.x - cx) <= half)
        along = chunk.x[sel] - cx
    else:
        sel = (np.abs(chunk.x - cx) <= band) & (np.abs(chunk.y - cy) <= half)
        along = chunk.y[sel] - cy
    z = chunk.z[sel]

    span = 0.5
    if len(z):
        p2, p98 = np.percentile(z, 2), np.percentile(z, 98)
        span = max(span, 1.2 * max(abs(p98 - z_ref), abs(z_ref - p2)) * 2)
    z_lo = z_ref - span / 2

    def to_px(a, zz):
        return (a + half) / (2 * half) * (px - 1), (h - 1) - (zz - z_lo) / span * (h - 1)

    if len(z):
        u, v = to_px(along, z)
        colours = chunk.rgb[sel] if chunk.rgb is not None else np.full((len(z), 3), 220)
        for uu, vv, c in zip(u.astype(int), v.astype(int), colours):
            draw.rectangle([uu - 1, vv - 1, uu + 1, vv + 1], fill=tuple(int(k) for k in c))
    _, yref = to_px(0, z_ref)
    draw.line([(0, yref), (px, yref)], fill=CROSSHAIR, width=1)
    draw.line([(px / 2, 0), (px / 2, h)], fill=CROSSHAIR, width=1)
    font = _font(12)
    draw.text((4, 4), f"{'W-E' if axis == 'ew' else 'S-N'} profile, vertical span {span:.2f} m", fill=(230, 230, 230), font=font)
    return img


def annotate(
    img: Image.Image,
    half: float,
    title: str = "",
    observed: tuple[float, float] | None = None,
) -> Image.Image:
    """Draw the surveyed position (red crosshair), an optional observed position
    (offset dx, dy in metres from the surveyed point), a scale bar and a title."""
    img = img.copy()
    draw = ImageDraw.Draw(img)
    w, h = img.size
    c = w / 2
    gap, arm = w * 0.02, w * 0.12
    for a, b in (((c - arm, c), (c - gap, c)), ((c + gap, c), (c + arm, c)), ((c, c - arm), (c, c - gap)), ((c, c + gap), (c, c + arm))):
        draw.line([a, b], fill=CROSSHAIR, width=2)
    if observed is not None:
        ox = c + observed[0] / (2 * half) * w
        oy = c - observed[1] / (2 * half) * h
        r = w * 0.015
        draw.ellipse([ox - r, oy - r, ox + r, oy + r], outline=OBSERVED, width=2)

    bar_m = _nice_length(half * 2 * 0.25)
    bar_px = bar_m / (2 * half) * w
    y = h - 14
    draw.rectangle([10, y - 4, 10 + bar_px, y], fill=(255, 255, 255))
    font = _font(12)
    draw.text((10, y - 20), _fmt_len(bar_m), fill=(255, 255, 255), font=font)
    if title:
        draw.text((6, 4), title, fill=(255, 255, 255), font=font, stroke_width=2, stroke_fill=(0, 0, 0))
    return img


def _nice_length(target: float) -> float:
    exp = math.floor(math.log10(target))
    for m in (5, 2, 1):
        if m * 10**exp <= target:
            return m * 10**exp
    return 10**exp


def _fmt_len(m: float) -> str:
    return f"{m * 100:.0f} cm" if m < 1 else f"{m:g} m"
