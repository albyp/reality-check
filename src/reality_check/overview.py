"""Site overview: the orthomosaic (or DEM) with every control point plotted.

Styled after Metashape's GCP error plot: each point's horizontal error is an
ellipse with semi-axes |dX| and |dY|, exaggerated by a stated factor, and its
fill colour shows dZ. A short line points in the direction of the XY error.
Legends give the ground scale, the ellipse exaggeration and the dZ colours.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import rasterio
from PIL import Image, ImageDraw
from rasterio.enums import Resampling
from rasterio.windows import from_bounds

from reality_check.chips import _font, _nice_length, hillshade
from reality_check.models import CheckKind, DatasetKind, ObsStatus
from reality_check.review import point_results
from reality_check.session import Session

MARGIN = 0.04  # fraction of the extent added around the control points
# The overview is drawn at its printed size, so text and symbols are the same
# size on paper however large or oddly shaped the site is.
SHEET_W_MM = 186.0  # A4 width inside 12 mm margins
SHEET_H_MM = 235.0  # A4 height left for the figure after its heading and caption
PX_PER_MM = 8  # about 200 dpi
TEXT_MM = 2.6  # labels and legend, about 7.5 pt
MARKER_MM = 1.1  # point symbol radius
TARGET_ELLIPSE_MM = 6.0  # largest ellipse semi-axis
GAP_MM = 4.0  # between strips
OVERLAP = 0.03  # each strip runs this fraction of its length into its neighbours
MAX_STRIPS = 4  # per sheet
SPLIT_GAIN = 1.5  # cut a site into strips only if that makes the map at least this much larger
MAX_SHEETS = 6
MIN_SPACING_MM = 6.0  # typical gap between neighbouring points on paper before more sheets are used
KEY_MIN_PX = 40  # narrowest key map worth drawing
PAPER = (255, 255, 255)  # outside the imagery: white, so a printed report uses no ink there
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
                rgb[mask] = PAPER
                # Soften the ortho so the symbols stand out.
                rgb = (rgb.astype(float) * 0.8 + 255 * 0.2).astype(np.uint8)
                rgb[mask] = PAPER
                return Image.fromarray(rgb), "Orthomosaic"
            z = r.read(1, window=win, out_shape=(height, width), boundless=True, masked=True).astype(float)
            mask = np.ma.getmaskarray(z)
            zf = np.ma.filled(z, np.nan)
            if mask.all():
                continue
            zf = np.where(mask, np.nanmedian(zf), zf)
            shade = hillshade(zf, (right - left) / width)
            g = (70 + 170 * shade).astype(np.uint8)
            g[mask] = PAPER[0]
            return Image.fromarray(np.stack([g, g, g], axis=-1)), "DEM hillshade"
    return Image.new("RGB", (width, height), PAPER), "No imagery"


@dataclass
class Layout:
    """How the site is split into strips and the strips onto sheets (printed pages).

    A site that suits the page is one strip on one sheet. A long, narrow site
    is cut along its length into strips at one scale: north-south sites into
    strips laid side by side (north first, left to right), east-west sites
    into strips stacked down the sheet (west first, top to bottom). More
    sheets are used only when one sheet would crowd the points.
    """
    tall: bool  # strips run north-south and sit side by side; else east-west, stacked
    sheets: int
    per_sheet: int
    scale: float  # paper mm per ground metre
    extent: tuple[float, float, float, float]  # whole site with margin: left, bottom, right, top
    tiles: list[tuple[float, float, float, float]]  # ground bounds of each strip, in reading order

    @property
    def strips(self) -> int:
        return len(self.tiles)


@dataclass
class Overview:
    sheets: list[Image.Image]
    layout: Layout


def _median_spacing(xs: np.ndarray, ys: np.ndarray) -> float:
    """Median distance from each point to its nearest neighbour (m)."""
    if len(xs) < 2:
        return math.inf
    step = max(1, len(xs) // 1500)
    x, y = xs[::step], ys[::step]
    d = np.hypot(x[:, None] - x[None, :], y[:, None] - y[None, :])
    np.fill_diagonal(d, np.inf)
    return float(np.median(d.min(axis=1)))


def plan_layout(xs: np.ndarray, ys: np.ndarray, legend_mm: float) -> Layout:
    dx, dy = float(np.ptp(xs)), float(np.ptp(ys))
    pad_x, pad_y = dx * MARGIN + 10, dy * MARGIN + 10
    w, h = dx + 2 * pad_x, dy + 2 * pad_y
    cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
    map_h = SHEET_H_MM - legend_mm
    tall = h / w >= map_h / SHEET_W_MM
    long_, short = (h, w) if tall else (w, h)
    # Room on the sheet across the strips (where they sit next to each other) and along one strip.
    across, along = (SHEET_W_MM, map_h) if tall else (map_h, SHEET_W_MM)

    def tile_len(k: int) -> float:
        return long_ / k * (1 + 2 * OVERLAP) if k > 1 else long_

    def scale(n: int, c: int) -> float:
        return min((across - (c - 1) * GAP_MM) / (c * short), along / tile_len(n * c))

    spacing = _median_spacing(xs, ys)
    n, c, s = 1, 1, scale(1, 1)
    for sheets in range(1, MAX_SHEETS + 1):
        per = max(range(1, MAX_STRIPS + 1), key=lambda c_: (round(scale(sheets, c_), 9), -c_))
        if scale(sheets, per) < SPLIT_GAIN * scale(sheets, 1):  # not worth cutting the site up
            per = 1
        sc = scale(sheets, per)
        if sheets > 1 and sc < s * 1.15:  # another sheet would hardly enlarge the map
            break
        n, c, s = sheets, per, sc
        if spacing * s >= MIN_SPACING_MM:
            break

    k = n * c
    seg = long_ / k
    # Across the strips, show as much ground as the slot on the sheet holds; this
    # centres a narrow site in its strip with imagery either side.
    slot = (across - (c - 1) * GAP_MM) / c
    half_across = max(short, slot / s if (tall or k > 1) else short) / 2
    tiles = []
    for i in range(k):
        a0 = i * seg - (OVERLAP * seg if k > 1 else 0)
        a1 = (i + 1) * seg + (OVERLAP * seg if k > 1 else 0)
        if tall:  # north first
            top = cy + h / 2 - a0
            tiles.append((cx - half_across, top - (a1 - a0), cx + half_across, top))
        else:  # west first
            left = cx - w / 2 + a0
            tiles.append((left, cy - half_across, left + (a1 - a0), cy + half_across))
    extent = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    return Layout(tall, n, c, s, extent, tiles)


def render_overview(session: Session) -> Overview:
    """The overview as one image per printed sheet, drawn at print size."""
    results = point_results(session)
    pts = session.points
    font = _font(round(TEXT_MM * PX_PER_MM))
    sheet_w = round(SHEET_W_MM * PX_PER_MM)
    if not pts:
        layout = Layout(True, 1, 1, 1.0, (0, 0, 1, 1), [])
        return Overview([Image.new("RGB", (sheet_w, sheet_w // 2), PAPER)], layout)

    xy_ds = next((d for d in session.datasets if d.kind is DatasetKind.ORTHO), None) or next(
        (d for d in session.datasets if d.kind is DatasetKind.CLOUD), None)
    z_ds = next((d for d in session.datasets if d.kind is DatasetKind.DEM), None) or next(
        (d for d in session.datasets if d.kind is DatasetKind.CLOUD), None)
    dxys = [pr.dxy[xy_ds.id] for pr in results if xy_ds and pr.point.enabled and xy_ds.id in pr.dxy]
    dzs = [pr.dz[z_ds.id] for pr in results if z_ds and pr.point.enabled and z_ds.id in pr.dz]
    dz_limit = max([abs(v) for v in dzs] + [session.settings.tol_z or 0, 0.01])

    xs, ys = np.array([p.x for p in pts]), np.array([p.y for p in pts])
    # Plan with room for the strip line in the legend; most sites do not need it.
    layout = plan_layout(xs, ys, _legend_height(font, bool(dxys), True) / PX_PER_MM)
    mpp = 1 / (layout.scale * PX_PER_MM)  # ground metres per pixel
    factor = None
    if dxys and max(dxys) > 0:
        factor = _nice_down(TARGET_ELLIPSE_MM / layout.scale / max(dxys))

    images, source = [], "No imagery"
    for b in layout.tiles:
        tw, th = int((b[2] - b[0]) / mpp), int((b[3] - b[1]) / mpp)  # rounded down, so the strips fit the sheet
        img, source = _tile(session, results, b, tw, th, mpp, xy_ds, z_ds, dz_limit, factor, font)
        images.append(img)

    gap = round(GAP_MM * PX_PER_MM)
    k, per = layout.strips, layout.per_sheet
    sheets = []
    for n in range(layout.sheets):
        part = images[n * per:(n + 1) * per]
        if layout.tall:
            map_w = sum(i.width for i in part) + gap * (len(part) - 1)
            map_h = max(i.height for i in part)
        else:
            map_w = max(i.width for i in part)
            map_h = sum(i.height for i in part) + gap * (len(part) - 1)
        strip = _legend_height(font, bool(dxys), k > 1)
        out = Image.new("RGB", (sheet_w, map_h + strip), PAPER)
        x, y = (sheet_w - map_w) // 2, 0
        for j, img in enumerate(part):
            out.paste(img, (x, y))
            if k > 1:
                _strip_label(out, x, y, img.size, n * per + j + 1, font)
            x, y = (x + img.width + gap, y) if layout.tall else (x, y + img.height + gap)
        first, last = n * per + 1, n * per + len(part)
        _legend(out, map_h, mpp, factor, dz_limit, source, xy_ds, z_ds, bool(dxys), font,
                layout if k > 1 else None, (first, last), xs, ys)
        sheets.append(out)
    return Overview(sheets, layout)


def _strip_label(img: Image.Image, x: int, y: int, size: tuple[int, int], number: int, font) -> None:
    draw = ImageDraw.Draw(img)
    draw.rectangle([x, y, x + size[0] - 1, y + size[1] - 1], outline=(40, 40, 40), width=2)
    pad = 6
    tw = draw.textlength(str(number), font=font)
    draw.rectangle([x, y, x + tw + 2 * pad, y + _line_h(font) + 2], fill=(40, 40, 40))
    draw.text((x + pad, y + 3), str(number), fill=(255, 255, 255), font=font)


def _tile(session, results, bounds, width, height, mpp, xy_ds, z_ds, dz_limit, factor, font):
    """One strip of the map: background with every point inside it. Returns (image, source label)."""
    left, bottom, right, top = bounds
    img, source = _background(session, bounds, width, height)
    img = img.convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    def to_px(x, y):
        return (x - left) / mpp, (top - y) / mpp

    r_marker = round(MARKER_MM * PX_PER_MM)
    for pr in results:
        cp = pr.point
        px, py = to_px(cp.x, cp.y)
        if not (0 <= px < width and 0 <= py < height):  # the next strip shows it; strips overlap
            continue
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

    return Image.alpha_composite(img, overlay).convert("RGB"), source


def _line_h(font) -> int:
    return (font.size if hasattr(font, "size") else 12) + 6


def _legend_height(font, has_xy: bool, strips: bool = False) -> int:
    pad = 12
    return pad * 2 + _line_h(font) * (4 + (1 if has_xy else 0) + (1 if strips else 0)) + 18


def _legend(img, map_h, mpp, factor, dz_limit, source, xy_ds, z_ds, has_xy, font,
            layout: Layout | None = None, shown: tuple[int, int] = (1, 1), xs=None, ys=None) -> None:
    """Legend in a strip below the map, so it never hides a control point."""
    draw = ImageDraw.Draw(img, "RGBA")
    w = img.size[0]
    pad = 12
    line_h = _line_h(font)
    box_w = max(320, w // 3)
    draw.line([(0, map_h), (w, map_h)], fill=(60, 60, 60, 255), width=1)
    if layout is not None:
        _key_map(draw, layout, shown, xs, ys, w - pad * 2, map_h + pad, img.size[1] - map_h - 2 * pad, font)
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
    if layout is not None:
        y += line_h
        order = "north to south, left to right" if layout.tall else "west to east, top to bottom"
        first, last = shown
        part = f"strip {first}" if first == last else f"strips {first} to {last}"  # the font has no en dash
        draw.text((x, y), f"{part[0].upper()}{part[1:]} of {layout.strips}, {order}; one scale, "
                          "slight overlap at joins", fill=(70, 70, 70, 255), font=font)


def _key_map(draw, layout: Layout, shown, xs, ys, right: float, top: float, height: float, font) -> None:
    """Small map of the whole site at the right of the legend: the strips, numbered, with this sheet's shaded."""
    left_g, bottom_g, right_g, top_g = layout.extent
    for b in layout.tiles:
        left_g, bottom_g = min(left_g, b[0]), min(bottom_g, b[1])
        right_g, top_g = max(right_g, b[2]), max(top_g, b[3])
    k = height / max(top_g - bottom_g, (right_g - left_g) * height / (SHEET_W_MM * PX_PER_MM * 0.25))
    width = (right_g - left_g) * k
    if min(width, (top_g - bottom_g) * k) < KEY_MIN_PX:  # a sliver says nothing the strip numbers don't
        return
    x0 = right - width

    def to_px(x, y):
        return x0 + (x - left_g) * k, top + (top_g - y) * k

    first, last = shown
    for i, b in enumerate(layout.tiles, start=1):
        (ax, ay), (bx, by) = to_px(b[0], b[3]), to_px(b[2], b[1])
        fill = (31, 122, 140, 70) if first <= i <= last else None
        draw.rectangle([ax, ay, bx, by], fill=fill, outline=(60, 60, 60, 255), width=1)
        label = str(i)
        tw = draw.textlength(label, font=font)
        if bx - ax > tw + 4 and by - ay > _line_h(font):
            draw.text(((ax + bx - tw) / 2, ay + 2), label, fill=(20, 20, 20, 255), font=font)
    for x, y in zip(xs, ys):
        px, py = to_px(x, y)
        draw.ellipse([px - 2, py - 2, px + 2, py + 2], fill=(20, 20, 20, 255))
    tw = draw.textlength("Key", font=font)
    draw.text((x0 - tw - 8, top), "Key", fill=(70, 70, 70, 255), font=font)


def _kind(ds) -> str:
    return {DatasetKind.ORTHO: "orthomosaic", DatasetKind.DEM: "DEM", DatasetKind.CLOUD: "point cloud"}[ds.kind]
