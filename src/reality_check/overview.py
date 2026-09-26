"""Site overview: the orthomosaic (or DEM) with every control point plotted.

Styled after Metashape's GCP error plot: each point's horizontal error is an
ellipse with semi-axes |dX| and |dY|, exaggerated by a stated factor, and its
fill colour shows dZ. A short line points in the direction of the XY error.
Legends give the ground scale, the ellipse exaggeration and the dZ colours.
"""

from __future__ import annotations

import math

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

from reality_check.chips import BACKGROUND, _font, _nice_length, hillshade
from reality_check.models import CheckKind, DatasetKind, ObsStatus
from reality_check.review import point_results
from reality_check.session import Session

MARGIN = 0.04  # fraction of the extent added around the control points
TARGET_ELLIPSE = 0.035  # largest ellipse semi-axis, as a fraction of image width
DZ_NEG = np.array([33, 102, 172], float)  # surface below surveyed
DZ_MID = np.array([247, 247, 247], float)
DZ_POS = np.array([178, 24, 43], float)  # surface above surveyed


def dz_colour(dz: float, limit: float) -> tuple[int, int, int]:
    t = max(-1.0, min(1.0, dz / limit)) if limit > 0 else 0.0
    c = DZ_MID + (DZ_POS - DZ_MID) * t if t >= 0 else DZ_MID + (DZ_NEG - DZ_MID) * -t
    return tuple(int(v) for v in c)


def _nice_down(x: float) -> float:
    """Largest 1/2/5 x 10^n not above x."""
    if x <= 0:
        return 1.0
    exp = math.floor(math.log10(x))
    for m in (5, 2, 1):
        if m * 10**exp <= x:
            return m * 10**exp
    return 10**exp


def _background(session: Session, bounds, width: int, height: int) -> tuple[Image.Image, str]:
    """Ortho if present, else DEM hillshade, else blank. Returns (image, source label)."""
    left, bottom, right, top = bounds
    for kind in (DatasetKind.ORTHO, DatasetKind.DEM):
        ds = next((d for d in session.datasets if d.kind is kind), None)
        if ds is None:
            continue
        with rasterio.open(ds.path) as r:
            win = from_bounds(left, bottom, right, top, r.transform)
            if kind is DatasetKind.ORTHO:
                bands = [1, 2, 3] if r.count >= 3 else [1]
                data = r.read(bands, window=win, out_shape=(len(bands), height, width), boundless=True, masked=True,
                              resampling=Resampling.average)
                mask = np.ma.getmaskarray(data).any(axis=0)
                if r.count >= 4:
                    alpha = r.read(4, window=win, out_shape=(height, width), boundless=True, fill_value=0)
                    mask |= alpha == 0
                rgb = np.ma.filled(data, 0).astype(np.uint8)
                rgb = np.repeat(rgb, 3, axis=0) if rgb.shape[0] == 1 else rgb
                rgb = np.moveaxis(rgb, 0, -1).copy()
                rgb[mask] = BACKGROUND
                # Soften the ortho so the symbols stand out.
                rgb = (rgb.astype(float) * 0.8 + 255 * 0.2).astype(np.uint8)
                rgb[mask] = BACKGROUND
                return Image.fromarray(rgb), "Orthomosaic"
            z = r.read(1, window=win, out_shape=(height, width), boundless=True, masked=True).astype(float)
            mask = np.ma.getmaskarray(z)
            zf = np.ma.filled(z, np.nan)
            if mask.all():
                continue
            zf = np.where(mask, np.nanmedian(zf), zf)
            shade = hillshade(zf, (right - left) / width)
            g = (70 + 170 * shade).astype(np.uint8)
            g[mask] = BACKGROUND[0]
            return Image.fromarray(np.stack([g, g, g], axis=-1)), "DEM hillshade"
    return Image.new("RGB", (width, height), (235, 235, 235)), "No imagery"


def render_overview(session: Session, width: int = 1600) -> Image.Image:
    results = point_results(session)
    pts = session.points
    if not pts:
        return Image.new("RGB", (width, width // 2), (235, 235, 235))

    xs, ys = np.array([p.x for p in pts]), np.array([p.y for p in pts])
    span = max(xs.max() - xs.min(), ys.max() - ys.min(), 10.0)
    pad = span * MARGIN + 10
    left, right = xs.min() - pad, xs.max() + pad
    bottom, top = ys.min() - pad, ys.max() + pad
    height = max(200, int(round(width * (top - bottom) / (right - left))))
    mpp = (right - left) / width  # metres per pixel

    img, source = _background(session, (left, bottom, right, top), width, height)
    img = img.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def to_px(x, y):
        return (x - left) / mpp, (top - y) / mpp

    xy_ds = next((d for d in session.datasets if d.kind is DatasetKind.ORTHO), None) or next(
        (d for d in session.datasets if d.kind is DatasetKind.CLOUD), None)
    z_ds = next((d for d in session.datasets if d.kind is DatasetKind.DEM), None) or next(
        (d for d in session.datasets if d.kind is DatasetKind.CLOUD), None)

    dxys = [pr.dxy[xy_ds.id] for pr in results if xy_ds and pr.point.enabled and xy_ds.id in pr.dxy]
    dzs = [pr.dz[z_ds.id] for pr in results if z_ds and pr.point.enabled and z_ds.id in pr.dz]
    dz_limit = max([abs(v) for v in dzs] + [session.settings.tol_z or 0, 0.01])
    factor = None
    if dxys and max(dxys) > 0:
        factor = _nice_down(TARGET_ELLIPSE * width * mpp / max(dxys))

    font = _font(max(12, width // 64))
    r_marker = max(5, width // 170)
    for pr in results:
        cp = pr.point
        px, py = to_px(cp.x, cp.y)
        dz = pr.dz.get(z_ds.id) if z_ds else None
        fill = dz_colour(dz, dz_limit) + (220,) if dz is not None and cp.enabled else (160, 160, 160, 200)
        outline = (20, 20, 20, 255) if cp.enabled else (110, 110, 110, 255)
        dx = pr.dx.get(xy_ds.id) if xy_ds else None
        dy = pr.dy.get(xy_ds.id) if xy_ds else None
        xy_obs = pr.status.get((xy_ds.id, CheckKind.XY)) if xy_ds else None

        if factor and dx is not None and cp.enabled:
            ax = max(abs(dx) * factor / mpp, r_marker)
            ay = max(abs(dy) * factor / mpp, r_marker)
            draw.ellipse([px - ax, py - ay, px + ax, py + ay], fill=fill, outline=outline, width=2)
            ex, ey = px + dx * factor / mpp, py - dy * factor / mpp
            draw.line([(px, py), (ex, ey)], fill=outline, width=2)
        else:
            draw.ellipse([px - r_marker, py - r_marker, px + r_marker, py + r_marker], fill=fill, outline=outline,
                         width=2)
        if xy_obs is not None and xy_obs.status is ObsStatus.NOT_FOUND:
            k = r_marker + 3
            draw.line([(px - k, py - k), (px + k, py + k)], fill=(200, 0, 0, 255), width=3)
            draw.line([(px - k, py + k), (px + k, py - k)], fill=(200, 0, 0, 255), width=3)
        label = cp.id + ("" if cp.enabled else " (off)")
        draw.text((px + r_marker + 4, py - r_marker - 2), label, fill=(255, 255, 255, 255), font=font,
                  stroke_width=3, stroke_fill=(0, 0, 0, 255))

    img = Image.alpha_composite(img, overlay).convert("RGB")
    # Legend in a strip below the map, so it never hides a control point.
    strip = _legend_height(font, bool(dxys))
    out = Image.new("RGB", (width, height + strip), (255, 255, 255))
    out.paste(img, (0, 0))
    _legend(out, height, mpp, factor, dz_limit, source, xy_ds, z_ds, bool(dxys), font)
    return out


def _line_h(font) -> int:
    return (font.size if hasattr(font, "size") else 12) + 6


def _legend_height(font, has_xy: bool) -> int:
    pad = 12
    return pad * 2 + _line_h(font) * (4 + (1 if has_xy else 0)) + 18


def _legend(img, map_h, mpp, factor, dz_limit, source, xy_ds, z_ds, has_xy, font) -> None:
    draw = ImageDraw.Draw(img, "RGBA")
    w = img.size[0]
    pad = 12
    line_h = _line_h(font)
    box_w = max(320, w // 3)
    draw.line([(0, map_h), (w, map_h)], fill=(60, 60, 60, 255), width=1)
    x, y = pad * 2, map_h + pad
    ink = (20, 20, 20, 255)

    # Ground scale bar
    bar_m = _nice_length((box_w - 2 * pad) * 0.5 * mpp)
    bar_px = bar_m / mpp
    draw.rectangle([x, y + 4, x + bar_px, y + 10], fill=ink)
    draw.text((x + bar_px + 8, y), f"{bar_m:g} m", fill=ink, font=font)
    y += line_h

    # Ellipse exaggeration
    if has_xy and factor:
        err_m = _nice_down((box_w - 2 * pad) * 0.4 * mpp / factor)
        err_px = err_m * factor / mpp
        draw.line([(x, y + 7), (x + err_px, y + 7)], fill=ink, width=3)
        draw.text((x + err_px + 8, y), f"= {err_m * 1000:g} mm XY error (ellipses x{factor:g})", fill=ink, font=font)
        y += line_h

    # dZ colour bar
    bar_w = box_w - 2 * pad
    for i in range(int(bar_w)):
        t = (i / max(bar_w - 1, 1)) * 2 - 1
        draw.line([(x + i, y), (x + i, y + 12)], fill=dz_colour(t * dz_limit, dz_limit))
    draw.rectangle([x, y, x + bar_w, y + 12], outline=(80, 80, 80, 255))
    y += 16
    lim = dz_limit * 1000
    draw.text((x, y), f"dZ -{lim:.0f} mm", fill=ink, font=font)
    draw.text((x + bar_w / 2 - 6, y), "0", fill=ink, font=font)
    txt = f"+{lim:.0f} mm"
    draw.text((x + bar_w - draw.textlength(txt, font=font), y), txt, fill=ink, font=font)
    y += line_h

    src = [f"Background: {source}"]
    if z_ds:
        src.append(f"dZ: {_kind(z_ds)}")
    if xy_ds and has_xy:
        src.append(f"XY: {_kind(xy_ds)}")
    draw.text((x, y), " · ".join(src), fill=(70, 70, 70, 255), font=font)
    y += line_h
    draw.text((x, y), "Grey = disabled · red X = target not found", fill=(70, 70, 70, 255), font=font)


def _kind(ds) -> str:
    return {DatasetKind.ORTHO: "orthomosaic", DatasetKind.DEM: "DEM", DatasetKind.CLOUD: "point cloud"}[ds.kind]
