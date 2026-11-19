"""Cross-section chart: an SVG profile for the report, drawn in millimetres.

Chainage along the string runs left to right, height up, with the vertical
exaggeration stated. Cloud points within the corridor are small grey dots
(as in a GIS path profile), the cloud's median line is dashed, a DEM is a
solid line, and the surveyed points are diamonds. The line joining the
surveyed points has the class "cs-join", so the report can hide it
(settings.section_join) without redrawing the chart.

Ink only, as in the rest of the report: red is kept for nonconformance.
"""

from __future__ import annotations

import html
import math

import numpy as np

from reality_check.models import DatasetKind
from reality_check.sections import MAX_DRAWN_POINTS, SectionResult

WIDTH = 186.0  # mm, the page width inside the margins
HEIGHT = 68.0  # mm
LEFT, RIGHT, TOP, BOTTOM = 16.0, 3.0, 3.0, 8.0  # plot margins, mm
DOT = 0.32  # mm, cloud point radius
NODE = 1.1  # mm, surveyed point half-size
FONT = 2.3  # mm (about 6.5 pt)
CLOUD_FILL = ("#8a8a85", "#c2b8a3")  # first and second point cloud


def _nice(span: float, target: int) -> float:
    """A 1/2/5 x 10^n step giving about target intervals over span."""
    raw = span / max(target, 1)
    exp = math.floor(math.log10(raw)) if raw > 0 else 0
    for m in (1, 2, 5, 10):
        if m * 10**exp >= raw:
            return m * 10**exp
    return 10 ** (exp + 1)


def _fmt(v: float) -> str:
    return f"{v:.2f}".rstrip("0").rstrip(".")


def section_svg(result: SectionResult) -> tuple[str, float]:
    """The chart as an inline SVG string, and its vertical exaggeration."""
    sec = result.section
    pw, ph = WIDTH - LEFT - RIGHT, HEIGHT - TOP - BOTTOM
    node_z = np.array([p.z for p in sec.nodes])

    # Extent: the surveyed string plus a little; height from the surveyed points and the surfaces.
    pad = max(0.02 * sec.length, 0.2)
    x0, x1 = -pad, sec.length + pad
    zs = [node_z]
    for p in result.profiles:
        inside = (p.ch >= x0) & (p.ch <= x1) & np.isfinite(p.z)
        zs.append(p.z[inside])
        if len(p.points_z):
            pin = (p.points_ch >= x0) & (p.points_ch <= x1)
            if pin.any():
                zs.append(np.percentile(p.points_z[pin], [0.5, 99.5]))
    allz = np.concatenate([np.atleast_1d(z) for z in zs if len(np.atleast_1d(z))])
    z0, z1 = float(allz.min()), float(allz.max())
    zpad = max((z1 - z0) * 0.12, 0.02)
    z0, z1 = z0 - zpad, z1 + zpad

    sx = pw / (x1 - x0)
    sz = ph / (z1 - z0)
    ve = sz / sx

    def X(c):
        return LEFT + (np.asarray(c) - x0) * sx

    def Z(z):
        return TOP + (z1 - np.asarray(z)) * sz

    out = [f"<svg class='cs' viewBox='0 0 {WIDTH:g} {HEIGHT:g}' width='{WIDTH:g}mm' height='{HEIGHT:g}mm' "
           f"xmlns='http://www.w3.org/2000/svg' font-family='Bahnschrift, Arial Narrow, Arial, sans-serif' "
           f"font-size='{FONT}'>"]
    out.append(f"<defs><clipPath id='clip-{html.escape(sec.code)}'><rect x='{LEFT}' y='{TOP}' width='{pw}' "
               f"height='{ph}'/></clipPath></defs>")

    # Grid and axes.
    zstep = _nice(z1 - z0, 5)
    cstep = _nice(x1 - x0, 10)
    for z in np.arange(math.ceil(z0 / zstep) * zstep, z1, zstep):
        y = Z(z)
        out.append(f"<line x1='{LEFT}' y1='{y:.2f}' x2='{LEFT + pw}' y2='{y:.2f}' stroke='#bdbdbd' "
                   f"stroke-width='0.12' stroke-dasharray='1.2 0.6 0.2 0.6'/>")
        out.append(f"<text x='{LEFT - 1:.2f}' y='{y + FONT * 0.35:.2f}' text-anchor='end'>{z:.2f}</text>")
    for c in np.arange(math.ceil(x0 / cstep) * cstep, x1, cstep):
        x = X(c)
        out.append(f"<line x1='{x:.2f}' y1='{TOP + ph}' x2='{x:.2f}' y2='{TOP + ph + 1}' stroke='#111' stroke-width='0.18'/>")
        out.append(f"<text x='{x:.2f}' y='{TOP + ph + 1 + FONT}' text-anchor='middle'>{_fmt(c)}</text>")
    out.append(f"<rect x='{LEFT}' y='{TOP}' width='{pw}' height='{ph}' fill='none' stroke='#111' stroke-width='0.35'/>")
    out.append(f"<text x='{LEFT + pw}' y='{HEIGHT - 0.6:.2f}' text-anchor='end' fill='#555'>CHAINAGE m</text>")
    out.append(f"<text x='1' y='{HEIGHT - 0.6:.2f}' fill='#555'>Z m</text>")

    out.append(f"<g clip-path='url(#clip-{html.escape(sec.code)})'>")
    # Cloud points first, so the lines and surveyed points sit on top.
    rng = np.random.default_rng(0)
    n_cloud = 0
    for p in result.profiles:
        if p.kind is not DatasetKind.CLOUD or not len(p.points_ch):
            continue
        fill = CLOUD_FILL[min(n_cloud, len(CLOUD_FILL) - 1)]
        n_cloud += 1
        idx = np.arange(len(p.points_ch))
        if len(idx) > MAX_DRAWN_POINTS:
            idx = rng.choice(idx, MAX_DRAWN_POINTS, replace=False)
        dots = "".join(f"<circle cx='{a:.2f}' cy='{b:.2f}' r='{DOT}'/>"
                       for a, b in zip(X(p.points_ch[idx]), Z(p.points_z[idx])))
        out.append(f"<g fill='{fill}'>{dots}</g>")
    for p in result.profiles:
        ok = np.isfinite(p.z)
        if ok.sum() < 2:
            continue
        dash = " stroke-dasharray='1 0.6'" if p.kind is DatasetKind.CLOUD else ""
        width = 0.25 if p.kind is DatasetKind.CLOUD else 0.35
        colour = "#444" if p.kind is DatasetKind.CLOUD else "#111"
        # Break the line where there is no data.
        runs, cur = [], []
        for c, z, good in zip(p.ch, p.z, ok):
            if good:
                cur.append(f"{X(c):.2f},{Z(z):.2f}")
            elif cur:
                runs.append(cur)
                cur = []
        if cur:
            runs.append(cur)
        for r in runs:
            if len(r) > 1:
                out.append(f"<polyline points='{' '.join(r)}' fill='none' stroke='{colour}' stroke-width='{width}'"
                           f"{dash}/>")
    # The surveyed string: joining line (optional in the report), then the points.
    nx, nz = X(sec.chainage), Z(node_z)
    out.append(f"<polyline class='cs-join' points='{' '.join(f'{a:.2f},{b:.2f}' for a, b in zip(nx, nz))}' "
               "fill='none' stroke='#111' stroke-width='0.3'/>")
    for p, a, b in zip(sec.nodes, nx, nz):
        fill = "#fff" if p.enabled else "#ddd"
        out.append(f"<path d='M{a:.2f} {b - NODE:.2f} L{a + NODE:.2f} {b:.2f} L{a:.2f} {b + NODE:.2f} "
                   f"L{a - NODE:.2f} {b:.2f} Z' fill='{fill}' stroke='#111' stroke-width='0.3'>"
                   f"<title>{html.escape(p.id)} · Z {p.z:.3f}</title></path>")
    out.append("</g>")
    # First and last point names, above the plot edge so they never cover the data.
    for p, a, anchor in ((sec.nodes[0], nx[0], "start"), (sec.nodes[-1], nx[-1], "end")):
        out.append(f"<text x='{a:.2f}' y='{TOP + FONT + 0.6:.2f}' text-anchor='{anchor}' fill='#555'>"
                   f"{html.escape(p.id)}</text>")
    out.append("</svg>")
    return "".join(out), ve
