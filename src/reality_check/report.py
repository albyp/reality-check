"""Single-file QA report (HTML, with PDF export via Microsoft Edge)."""

from __future__ import annotations

import html
import itertools
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

from reality_check import __version__
from reality_check.cloud import CLASS_NAMES, cloud_info
from reality_check.crs import describe
from reality_check.document import DocumentInfo, logo_data_url
from reality_check.models import CheckKind, DatasetKind, ObsSource, ObsStatus, Role
from reality_check.raster import raster_info
from reality_check.render import ChipRenderer, to_data_url
from reality_check.review import point_results
from reality_check.overview import PX_PER_MM, render_overview
from reality_check.sectionplot import section_svg
from reality_check.sections import BIN, SectionResult, analyse, plain_spots
from reality_check.session import Session, project_title
from reality_check.stats import LEVEL, Stats, summarise, trim_note, z_offsets

log = logging.getLogger("reality_check")
KIND_LABEL = {DatasetKind.ORTHO: "Orthomosaic", DatasetKind.DEM: "DEM", DatasetKind.CLOUD: "Point cloud"}
OFFSET_LABEL = {DatasetKind.DEM: "DEM", DatasetKind.CLOUD: "point cloud"}  # in a sentence
COMPONENT_LABEL = {"dz": "dZ", "dx": "dX", "dy": "dY", "dxy": "dXY"}
STATUS_LABEL = {
    ObsStatus.OK: "OK",
    ObsStatus.NOT_FOUND: "Target not found",
    ObsStatus.NO_DATA: "No data",
    ObsStatus.OUT_OF_EXTENT: "Outside dataset",
}

# Plumb house style: "Drawing Sheet" (chosen 2026-11-02), as in CounterCheck's report. Engineering drawing
# conventions (AS 1100.101 / ISO 7200) without the sheet frame: a title block with ruled cells, boxed section
# numbers, ruled schedules, DIN lettering (Bahnschrift), ink only. Red is kept for nonconformance (FAIL, values
# over tolerance, targets not found) and nothing else, so the report reads on a black-and-white copier. Every
# sheet carries a title strip (see _page_css), so a loose page can be traced.
CSS = """
/* Bahnschrift has no ±, …, ✓, ✗, ², ≈, ▲ or ▼ in print; take those from Segoe UI, keep DIN lettering for the rest. */
@font-face { font-family:"Plumb Symbols"; src:local("Segoe UI Symbol"), local("Segoe UI"), local("Arial");
             unicode-range:U+00B1, U+00B2, U+00B3, U+00D7, U+2026, U+2212, U+2248, U+2713, U+2717, U+25B2, U+25BC, U+25C7, U+25EF; }
:root { --ink:#111; --mid:#555; --hair:#bdbdbd; --red:#c8102e;
        --w1:0.18mm; --w2:0.25mm; --w3:0.35mm; --w5:0.5mm; --w7:0.7mm;
        --sans:"Plumb Symbols", "Bahnschrift", "DIN Alternate", "Arial Narrow", Arial, sans-serif;
        --cond:"Plumb Symbols", "Bahnschrift SemiCondensed", "Bahnschrift", "Arial Narrow", sans-serif; }
* { box-sizing:border-box; }
html { background:#e4e4e1; }
body { margin:0; font:8.5pt/1.4 var(--sans); color:var(--ink); font-variant-numeric:tabular-nums; }
main { width:210mm; margin:24px auto; background:#fff; padding:16mm 14mm 24mm; }
a { color:inherit; }
.k { font:6pt var(--sans); letter-spacing:.08em; color:var(--mid); white-space:nowrap; }
.muted { color:var(--mid); }

/* Title block: logos either side of the title, then a row of ruled cells, then the inputs schedule. */
.tb { display:grid; grid-template-columns:1fr; border:var(--w7) solid var(--ink); }
.tb.logo-l { grid-template-columns:36mm 1fr; } .tb.logo-r { grid-template-columns:1fr 36mm; }
.tb.logo-l.logo-r { grid-template-columns:36mm 1fr 36mm; }
.tb > * { border-right:var(--w3) solid var(--ink); min-width:0; }
.tb > :last-child { border-right:none; }
.tb .logo { display:flex; align-items:center; justify-content:center; padding:2mm; }
.tb .logo img { max-width:32mm; max-height:16mm; object-fit:contain; }
.tb .ttl { padding:2.4mm 3.5mm; }
.tb h1 { font:600 13pt/1.15 var(--sans); margin:.6mm 0 .8mm; letter-spacing:.02em; }
.tb .sub { font-size:8pt; }
.cells { display:grid; grid-auto-flow:column; grid-auto-columns:minmax(0, auto); border:var(--w7) solid var(--ink);
         border-top:none; margin-bottom:2mm; }
.cells > div { border-right:var(--w2) solid var(--ink); padding:1.1mm 1.8mm 1.3mm; min-width:0; }
.cells > div:last-child { border-right:none; }
.cells b { display:block; font-weight:600; font-size:7.6pt; }
.warn { border:var(--w5) solid var(--ink); padding:1.2mm 2mm; margin-top:2mm; }
.warn .k { margin-right:2mm; }

/* Section titles: boxed number, underlined title (the drawing view-title convention). */
h2 { display:flex; align-items:baseline; gap:2.5mm; margin:5mm 0 2mm; font:600 9.5pt var(--sans); letter-spacing:.08em; }
h2 .num { display:inline-block; min-width:9mm; text-align:center; border:var(--w5) solid var(--ink);
          padding:.2mm 1.2mm; letter-spacing:.02em; }
h2 .t { border-bottom:var(--w5) solid var(--ink); padding-bottom:.4mm; }
h2 .sc, h2 .cont { margin-left:auto; font:400 7pt var(--sans); letter-spacing:.06em; color:var(--mid); }
h2 .sc ~ .cont { margin-left:0; }
h2.page-break, .page-break > h2:first-child { margin-top:0; }
h3 { font:600 8pt var(--sans); letter-spacing:.08em; margin:0; }

/* Schedules (tables). */
table { border-collapse:collapse; width:100%; border:var(--w5) solid var(--ink); }
th, td { border:var(--w2) solid var(--ink); padding:.7mm 2mm; text-align:left; vertical-align:top; }
thead th { font:600 6.5pt var(--cond); letter-spacing:.08em; border-bottom:var(--w5) solid var(--ink); }
tbody th { font-weight:600; font-size:7.5pt; width:30%; }
td.n, th.n { text-align:right; }
td.bad { color:var(--red); font-weight:600; } td.bad::after { content:"\\00A0\\2717"; }
tr.foot td { border-top:var(--w5) solid var(--ink); }
tr.sub td { color:var(--mid); }
.compact table { width:auto; min-width:50%; }
.compact td, .compact th { padding:.3mm 2mm; font-size:7pt; }
.table-wrap { overflow-x:auto; }

/* Summary: a ruled schedule of results, the PASS / FAIL stamp first. Ruled by the gaps, so it can wrap. */
.sched { display:grid; gap:var(--w2); background:var(--ink); border:var(--w5) solid var(--ink); }
.sched > div { background:#fff; padding:1.4mm 2mm 1.6mm; min-width:0; }
.sched .k { white-space:normal; }
.sched .v { font:500 14pt/1.2 var(--sans); white-space:nowrap; margin-top:.8mm; }
.sched .v small { font-size:8pt; font-weight:400; }
.sched .res { display:flex; flex-direction:column; justify-content:center; text-align:center; }
.stamp { display:inline-block; font:700 12pt var(--sans); letter-spacing:.14em; border:var(--w7) solid var(--ink);
         padding:.4mm 2.4mm; margin-top:.6mm; }
.stamp.fail { color:var(--red); border-color:var(--red); }
.sm { font-size:6.5pt; color:var(--mid); margin-top:.6mm; }
.ok::before { content:"\\2713  "; }
.nc { color:var(--red); font-weight:600; } .nc::before { content:"\\2717  "; }

/* General notes: offsets and items to review. */
ol.gn, ul.offsets { margin:2mm 0 0; padding-left:5mm; } ol.gn { margin:0; } ol.gn li, ul.offsets li { margin-bottom:1.2mm; }
ul.disregarded { margin:.6mm 0 0; padding-left:5mm; }
.tag { display:inline-block; font:600 6.5pt var(--sans); letter-spacing:.08em; text-transform:uppercase;
       border:var(--w3) solid var(--ink); padding:0 1mm; margin-left:1.5mm; white-space:nowrap; }
.tag.nc::before { content:none; } .tag.nc { border-color:var(--red); }

/* Point details: one ruled box per point, its id boxed like a detail callout. */
.point { border:var(--w5) solid var(--ink); margin:0 0 4mm; break-inside:avoid; }
.point.disabled { opacity:.6; }
.point header { display:flex; flex-wrap:wrap; align-items:baseline; gap:1mm; padding:1.2mm 2mm;
                border-bottom:var(--w3) solid var(--ink); }
.point h3 { border:var(--w5) solid var(--ink); padding:0 1.5mm; margin-right:1mm; }
.point .coords { color:var(--mid); font-size:7.5pt; margin-left:auto; }
.point .body { padding:2mm; }
.point .note { margin-top:2mm; }
.chips { display:grid; grid-template-columns:repeat(auto-fit, minmax(40mm, 1fr)); gap:2mm; margin-top:2mm; }
.chips figure { margin:0; border:var(--w2) solid var(--ink); }
.chips img { width:100%; display:block; }
.chips figcaption { font-size:6.5pt; color:var(--mid); padding:.6mm 1.4mm .8mm; border-top:var(--w2) solid var(--ink); }

/* Figures: ruled box, caption underneath. */
figure.overview { margin:0; break-inside:avoid; border:var(--w5) solid var(--ink); }
figure.overview img { width:100%; display:block; }
figure.overview figcaption { font-size:7pt; color:var(--mid); padding:1mm 2.2mm 1.2mm; border-top:var(--w2) solid var(--ink); }

/* Cross sections: one ruled box per section, the chart full width, then its results. */
.xs { border:var(--w5) solid var(--ink); margin:0 0 4mm; break-inside:avoid; }
.xs header { display:flex; flex-wrap:wrap; align-items:baseline; gap:1mm; padding:1.2mm 2mm;
             border-bottom:var(--w3) solid var(--ink); }
.xs h3 { border:var(--w5) solid var(--ink); padding:0 1.5mm; margin-right:1mm; }
.xs header .sm { margin-left:auto; margin-top:0; }
.xs svg.cs { display:block; width:100%; height:auto; }
.xs .body { padding:0 2mm 2mm; }
.xs .legend { font-size:6.5pt; color:var(--mid); padding:.6mm 2mm 1.4mm; border-bottom:var(--w2) solid var(--ink); }
.xs table { margin-top:2mm; }
.nojoin .cs-join { display:none; }
.note { font-size:7pt; color:var(--mid); margin:1.4mm 0 0; }

/* Sign-off: a ruled schedule, so names, signatures and dates line up whatever is filled in. */
table.sign td { height:10mm; vertical-align:middle; }
table.sign td.role { width:24mm; font:600 6.5pt var(--sans); letter-spacing:.08em; text-transform:uppercase; }
table.sign td.sig { width:46mm; } table.sign td.dt { width:26mm; }
.gen { font-size:6.5pt; color:var(--mid); margin-top:4mm; letter-spacing:.04em; text-transform:uppercase; }
.print-only { display:none; }
@media print {
  html { background:none; }
  /* The 186 mm page area is 702.99 px; Chromium clips it to 702 px, which would halve the right-hand rules. */
  main { width:auto; margin:0; padding:0 .4mm 0 0; }
  /* A heading and its content print together; a block longer than a page is
     split into page-sized parts (see _table_block). */
  .block { break-inside:avoid; }
  h2 { break-after:avoid; }
  thead { display:table-header-group; }
  tr { break-inside:avoid; }
  .table-wrap { overflow:visible; }
  .screen-only { display:none; }
  .print-only { display:block; }
  .page-break { break-before:page; }
  figure.overview img { width:var(--w, auto); max-width:100%; margin:0 auto; }
}
"""

# Every sheet: running title and sheet number at the top; a three-cell title strip at the bottom.
# Margin boxes are content-box: 2 x (60 + 4) + 54 + 4 = 186 mm, the page-area width.
_PAGE_CSS = """
@page { size:A4; margin:14mm 12mm 26mm 12mm;
  @top-left { content:%(running)s; font:6.5pt "Plumb Symbols", "Bahnschrift", Arial, sans-serif; letter-spacing:.06em;
              vertical-align:bottom; margin-bottom:2.5mm; }
  @top-right { content:%(top_right)s; font:6.5pt "Plumb Symbols", "Bahnschrift", Arial, sans-serif; letter-spacing:.06em;
               vertical-align:bottom; margin-bottom:2.5mm; }
  @bottom-left { width:60mm; content:%(strip_left)s; white-space:pre; font:6.5pt/1.6 "Plumb Symbols", "Bahnschrift", Arial, sans-serif;
                 letter-spacing:.04em; vertical-align:top; text-align:left; border-top:0.5mm solid #111;
                 padding:1.6mm 2mm; margin-top:5mm; }
  @bottom-center { width:54mm; content:%(strip_centre)s; white-space:pre; font:6.5pt/1.6 "Plumb Symbols", "Bahnschrift", Arial, sans-serif;
                   letter-spacing:.04em; vertical-align:top; text-align:left; border-top:0.5mm solid #111;
                   padding:1.6mm 2mm; margin-top:5mm; }
  @bottom-right { width:60mm; content:%(strip_right)s; white-space:pre; font:6.5pt/1.6 "Plumb Symbols", "Bahnschrift", Arial, sans-serif;
                  letter-spacing:.04em; vertical-align:top; text-align:left; border-top:0.5mm solid #111;
                  padding:1.6mm 2mm; margin-top:5mm; }
}
"""


def _e(s) -> str:
    return html.escape(str(s))


def _css_esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").replace("<", "\\3C ")


def _css_lines(*lines: str) -> str:
    """Several lines in one margin box (needs white-space:pre). Blank lines are dropped."""
    return '"' + "\\A ".join(_css_esc(_caps(x)) for x in lines if x) + '"'


def _caps(s: str) -> str:
    """Drawing-style capitals for titles, keeping the unit symbols readable ("dZ", "mm", "m²")."""
    s = re.sub(r"\b(\d+(?:\.\d+)?) ?MM\b", r"\1 mm", s.upper())
    return re.sub(r"\bD(X|Y|Z|XY)\b", r"d\1", s)


def _heading(n: str, title: str, note: str = "", cls: str = "") -> str:
    """Section title: boxed number, the title in capitals, small print at the right."""
    sc = f"<span class='sc'>{_e(note)}</span>" if note else ""
    return (f"<h2{f' class={cls!r}' if cls else ''}><span class='num'>{_e(n)}</span><span class='t'>{_e(_caps(title))}"
            f"</span>{sc}</h2>")


def _page_css(doc: DocumentInfo, title: str, running: str, control: str, tolerances: str) -> str:
    """Running title and sheet number at the top of every sheet; the three-cell title strip at the bottom.

    The strip follows CounterCheck: company and title; client, site and job; document number, revision, issue
    date and sheet. Without client, site or job, the centre cell names the control file and tolerances.
    """
    sheet = (f'"SHEET {_css_esc(doc.appendix.strip())}-" counter(page)' if doc.is_appendix
             else '"SHEET " counter(page) " OF " counter(pages)')
    ref = "  ·  ".join(x for x in (doc.document_number, f"REV {doc.revision}" if doc.revision else "") if x)
    centre = [f"{k}  {v}" for k, v in (("Client", doc.client), ("Site", doc.site), ("Job", doc.job_number)) if v]
    right = [f"{k}  {v}" for k, v in (("Document No.", doc.document_number), ("Rev", doc.revision),
                                       ("Issued", doc.issued)) if v]
    return _PAGE_CSS % {
        "running": '"' + _css_esc(_caps(running)) + '"',
        "top_right": (f'"{_css_esc(_caps(ref))}  ·  " ' if ref else "") + sheet,
        "strip_left": _css_lines(doc.company or "Plumb RealityCheck", title, doc.confidentiality or doc.footer
                                 or "QA/QC report"),
        "strip_centre": _css_lines(*(centre or [f"Control  {control}", f"Tolerances  {tolerances}"])),
        "strip_right": _css_lines(*right) + r' "\A " ' + sheet,
    }


def _logo_cell(path: str, alt: str) -> str:
    """A logo cell for the title block, or "" when there is no logo (or it cannot be read)."""
    if not path:
        return ""
    try:
        return f"<div class='logo'><img src='{logo_data_url(path)}' alt='{_e(alt)}'></div>"
    except (OSError, ValueError) as e:
        log.warning("Logo left out of the report: %s", e)
        return ""


def _title_block(doc: DocumentInfo, title: str) -> str:
    left, right = _logo_cell(doc.logo, doc.company or "Company logo"), _logo_cell(doc.client_logo,
                                                                                 doc.client or "Client logo")
    cls = "tb" + (" logo-l" if left else "") + (" logo-r" if right else "")
    sub = doc.subtitle or "RealityCheck QA/QC report · photogrammetry and LiDAR against survey control"
    cells = [("Project", doc.project), ("Site", doc.site), ("Client", doc.client), ("Job No.", doc.job_number),
             ("Document No.", doc.document_number), ("Rev", doc.revision), ("Issued", doc.issued),
             ("Author", doc.author)]
    return (f"<section class='{cls}'>{left}<div class='ttl'><div class='k'>TITLE</div><h1>{_e(_caps(title))}</h1>"
            f"<div class='sub'>{_e(sub)}</div></div>{right}</section>"
            "<div class='cells'>" + "".join(f"<div><span class='k'>{_e(_caps(k))}</span><b>{_e(v)}</b></div>"
                                            for k, v in cells if v) + "</div>")


def _signoff(doc: DocumentInfo) -> str:
    """A ruled schedule, one row per signatory: names, signatures and dates line up whatever is filled in."""
    rows = "".join(f"<tr><td class='role'>{_e(p.role)}</td><td>{_e(p.name)}</td><td>{_e(p.position)}</td>"
                   "<td class='sig'></td><td class='dt'></td></tr>" for p in doc.named_signatories())
    return ("<table class='sign'><thead><tr><th>ROLE</th><th>NAME</th><th>POSITION</th><th>SIGNATURE</th>"
            f"<th>DATE</th></tr></thead><tbody>{rows}</tbody></table>")


# Rows of a report table that fit on one printed A4 page with the heading and
# header row (8.5 pt text, 257 mm page area). Longer tables are split at this size.
PRINT_ROWS_PER_PAGE = 30
COMPACT_ROWS_PER_PAGE = 50  # .compact tables: 11 px text, tight rows


def _continued(heading: str) -> str:
    return heading.replace("</h2>", " <span class='cont'>(continued…)</span></h2>")


def _table_block(heading: str, head: str, rows: list[str], after: str = "", rows_per_page: int = PRINT_ROWS_PER_PAGE,
                 css_class: str = "") -> str:
    """A heading (an <h2>, see _heading) with its table, kept together in print.

    head is the header row's cells; rows are complete <tr> elements. A table
    that fits on a page prints as one block. A longer one prints as
    page-sized parts, each with the heading ("(continued…)" after the first)
    and the header row. On screen it is always one table.
    """
    thead = f"<thead><tr>{head}</tr></thead>"

    def block(h: str, body: list[str], cls: str, tail: str) -> str:
        return (f"<section class='block {cls} {css_class}'>{h}<div class='table-wrap'><table>{thead}"
                f"<tbody>{''.join(body)}</tbody></table></div>{tail}</section>")

    if len(rows) <= rows_per_page:
        return block(heading, rows, "", after)
    parts = [rows[i:i + rows_per_page] for i in range(0, len(rows), rows_per_page)]
    out = [block(heading, rows, "screen-only", after)]
    for i, part in enumerate(parts):
        cls = "print-only" + (" page-break" if i else "")
        out.append(block(heading if i == 0 else _continued(heading), part, cls, after if i == len(parts) - 1 else ""))
    return "".join(out)


def _mm(v: float | None, signed: bool = True) -> str:
    if v is None:
        return "–"
    return f"{v * 1000:+.0f}" if signed else f"{v * 1000:.0f}"


def _tol_class(v: float | None, tol: float | None, absolute: bool = True) -> str:
    if v is None or tol is None:
        return ""
    return "bad" if (abs(v) if absolute else v) > tol else ""


def build_html(session: Session, renderer: ChipRenderer, warnings: list[str] | None = None, title: str = "") -> str:
    s = session.settings
    ds_by_id = {d.id: d for d in session.datasets}
    results = point_results(session)
    summary = summarise(session.points, session.observations)
    z_ds = [d for d in session.datasets if d.kind in (DatasetKind.DEM, DatasetKind.CLOUD)]
    xy_ds = [d for d in session.datasets if any(o.dataset_id == d.id and o.check is CheckKind.XY
                                                  for o in session.observations)]
    title = title or project_title(session)
    sections = analyse(session, renderer.windows) if session.spots else []
    spots = plain_spots(session)
    generated = f"{datetime.now():%d %b %Y %H:%M}"
    control = Path(session.control_path).name
    tol = []
    if s.tol_z is not None:
        tol.append(f"Z ±{s.tol_z * 1000:.0f} mm")
    if s.tol_xy is not None:
        tol.append(f"XY {s.tol_xy * 1000:.0f} mm")
    tol_text = ", ".join(tol) or "none set"
    doc = s.document
    numbers = itertools.count(1)

    def h2(text: str, note: str = "", cls: str = "") -> str:
        return _heading(doc.heading_number(next(numbers)), text, note, cls)

    out: list[str] = []
    w = out.append

    # In appendix mode the report is "Appendix B — <title>" and its sheets are B-1, B-2 …
    heading_title = f"Appendix {doc.appendix.strip()} — {doc.appendix_title or title}" if doc.is_appendix else title
    css = CSS + _page_css(doc, title, f"{heading_title} · RealityCheck QA/QC report", control, tol_text)
    w(f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
      f"content='width=device-width, initial-scale=1'><title>RealityCheck report – {_e(heading_title)}</title>"
      f"<style>{css}</style></head><body><main>")

    # Title block, the ruled cells under it, then the inputs as a schedule.
    w(_title_block(doc, heading_title))
    inputs = [("Control points", f"{_e(control)} ({len(session.points)} points, "
                                 f"{sum(p.enabled for p in session.points)} enabled)")]
    if session.spots_path:
        gone = len(session.spots_disregarded)
        inputs.append(("Spot heights", f"{_e(Path(session.spots_path).name)} ({len(spots)} points"
                       f"{f', {gone} disregarded (more than {s.spot_max_outside:g} m outside the data)' if gone else ''}; "
                       "Z only)"))
        if sections:
            names = ", ".join(f"{_e(r.section.code)} ({len(r.section.nodes)} points, {r.section.length:.1f} m)"
                              for r in sections)
            inputs.append(("Cross sections", f"{names}; codes {_e(s.section_codes)}; cloud corridor "
                                             f"{s.section_width:g} m wide"))
    for d in session.datasets:
        inputs.append((KIND_LABEL[d.kind], f"{_e(Path(d.path).name)} <span class='muted'>· {_e(_crs_text(d))}</span>"))
    classes = s.cloud_classes
    if any(d.kind is DatasetKind.CLOUD for d in session.datasets):
        cls_txt = "all" if classes is None else ", ".join(f"{c} {CLASS_NAMES.get(c, '')}".strip() for c in classes)
        inputs.append(("Cloud Z method", f"median of points within {s.cloud_radius:g} m; classes: {_e(cls_txt)}"))
    inputs.append(("Tolerances", _e(tol_text)))
    inputs.append(("Generated", f"{generated} · RealityCheck {__version__}"))
    w("<table class='inputs'><tbody>" + "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in inputs)
      + "</tbody></table>")
    for m in warnings or []:
        w(f"<div class='warn'><span class='k'>NOTE</span>{_e(m)}</div>")

    # Headline results: checkpoints if roles are known, else all points. A PASS / FAIL stamp when tolerances are set.
    cells, verdicts = [], []
    for d in session.datasets:
        for comp, label, tolv in (("dz", "Z", s.tol_z), ("dxy", "XY", s.tol_xy)):
            rows = [r for r in summary if r.dataset_id == d.id and r.component == comp]
            if not rows:
                continue
            row = next((r for r in rows if r.group == "checkpoint"), None) or next(r for r in rows if r.group == "all")
            st = row.stats
            verdict = ""
            if tolv is not None:
                ok = st.rmse <= tolv
                verdicts.append(ok)
                verdict = (f"<span class='{'ok' if ok else 'nc'}'>{'Pass' if ok else 'Fail'}</span>"
                           f" (tolerance {tolv * 1000:.0f} mm) · ")
            group = "checkpoints" if row.group == "checkpoint" else "all points"
            bias = f"bias {_mm(st.mean)} mm · " if comp == "dz" else ""
            cells.append(f"<div><span class='k'>{_e(_caps(f'{KIND_LABEL[d.kind]} {label} RMSE · {group}'))} · n = {st.n}"
                         f"</span><div class='v'>{_mm(st.rmse, False)} <small>mm</small></div>"
                         f"<div class='sm'>{verdict}{bias}SD {_mm(st.sd, False)} mm</div></div>")
    w(f"<section class='block'>{h2('Summary', 'RESIDUAL = DATASET − SURVEYED POINT')}")
    if cells:
        cols = min(len(cells), 4)
        rows_n = -(-len(cells) // cols)
        spare = cols * rows_n - len(cells)
        if spare:  # the last cell fills the row, so no ink shows through the ruling
            cells[-1] = cells[-1].replace("<div>", f"<div style='grid-column:span {spare + 1}'>", 1)
        template = f"repeat({cols}, minmax(0, 1fr))"
        if verdicts:
            passed = all(verdicts)
            cells.insert(0, f"<div class='res' style='grid-row:span {rows_n}'><span class='k'>RESULT</span><span>"
                            f"<span class='stamp{'' if passed else ' fail'}'>{'PASS' if passed else 'FAIL'}</span></span>"
                            f"<span class='sm'>{sum(verdicts)} of {len(verdicts)} tolerances met</span></div>")
            template = "30mm " + template
        w(f"<div class='sched' style='grid-template-columns:{template}'>{''.join(cells)}</div>")
    offsets = z_offsets(session.points, session.observations, s.trim)
    spot_offsets = z_offsets(spots, session.spot_observations, s.trim, spots=True)
    if offsets or spot_offsets:
        w("<ul class='offsets'>" + "".join(
            f"<li>{_e(o.sentence(OFFSET_LABEL[ds_by_id[o.dataset_id].kind]))}</li>"
            for o in offsets + spot_offsets) + "</ul>")
        note = trim_note(offsets + spot_offsets)
        if note:
            w(f"<p class='note'>{_e(note)}</p>")
    w("</section>")

    overview = render_overview(session)
    caption = ("Ellipses show horizontal error (semi-axes dX and dY, exaggerated by the stated factor); "
               "the line points in the error direction. Colour shows dZ.")
    if overview.layout.strips > 1:
        caption += (" The site is long and narrow, so it is cut into numbered strips at one scale, "
                    "in the order stated under the map.")
    overview_heading = h2("Site overview", "dZ COLOURS: DARK = SURFACE LOW, LIGHT = HIGH")
    if overview.left_out:
        caption += (f" Not on the map, as no dataset has data there: {_e(', '.join(overview.left_out))}.")
    for i, sheet in enumerate(overview.sheets):
        heading = _continued(overview_heading) if i else overview_heading
        # Printed at the size it was drawn, so text keeps its size on paper.
        w(f"<section class='block{' page-break' if i else ''}'>{heading}<figure class='overview'>"
          f"<img style='--w:{sheet.width / PX_PER_MM:.1f}mm' src='{to_data_url(sheet, 'JPEG')}' "
          f"alt='Site overview with control points'>"
          f"{f'<figcaption>{caption}</figcaption>' if i == len(overview.sheets) - 1 else ''}</figure></section>")

    stat_rows = []
    for r in summary:
        st = r.stats
        stat_rows.append(
            f"<tr><td>{_e(Path(ds_by_id[r.dataset_id].path).name)}</td><td>{COMPONENT_LABEL[r.component]}</td>"
            f"<td>{_e(r.group)}</td><td class='n'>{st.n}</td><td class='n'>{_mm(st.mean) if r.component != 'dxy' else '–'}</td>"
            f"<td class='n'>{_mm(st.sd, False)}</td><td class='n'>{_mm(st.rmse, False)}</td>"
            f"<td class='n'>{_mm(max(abs(st.min), abs(st.max)), False)}</td></tr>")
    w(_table_block(
        h2("Statistics", "ALL VALUES IN mm"),
        "<th>DATASET</th><th>COMPONENT</th><th>POINTS</th><th class='n'>n</th><th class='n'>BIAS (MEAN)</th>"
        "<th class='n'>SD</th><th class='n'>RMSE</th><th class='n'>MAX |RESIDUAL|</th>",
        stat_rows,
        after="<p class='note'>All values in mm. Residual = dataset minus surveyed point. "
              "Bias is the mean residual (a systematic offset). RMSE combines bias and scatter (RMSE² ≈ bias² + SD²). "
              "Disabled points are excluded. GCPs were used in processing, so only checkpoints give an independent "
              "accuracy figure.</p>"))

    issues = _issues(session, results, ds_by_id)
    body = ("<ol class='gn'>" + "".join(f"<li>{i}</li>" for i in issues) + "</ol>") if issues \
        else "<p class='note'>None.</p>"
    w(f"<section class='block'>{h2('Items to review', 'NC = NONCONFORMANCE · CHECK = LOOK AT IT')}{body}</section>")

    # Point table
    head = "<th>POINT</th><th>ROLE</th>"
    head += "".join(f"<th class='n'>dZ {_caps(KIND_LABEL[d.kind])}</th>" for d in z_ds)
    head += "".join(f"<th class='n'>dXY {_caps(KIND_LABEL[d.kind])}</th>" for d in xy_ds)
    head += "<th>FLAGS</th>"
    point_rows = []
    for pr in results:
        cp = pr.point
        cells = [f"<td><a href='#pt-{_e(cp.id)}'>{_e(cp.id)}</a></td><td>{_role_text(cp.role)}</td>"]
        for d in z_ds:
            v = pr.dz.get(d.id)
            txt = _mm(v) if v is not None else _status_short(pr, d.id, CheckKind.Z)
            cells.append(f"<td class='n {_tol_class(v, s.tol_z)}'>{txt}</td>")
        for d in xy_ds:
            v = pr.dxy.get(d.id)
            txt = _mm(v, False) if v is not None else _status_short(pr, d.id, CheckKind.XY)
            cells.append(f"<td class='n {_tol_class(v, s.tol_xy, False)}'>{txt}</td>")
        cells.append(f"<td>{_flags(pr)}</td>")
        point_rows.append("<tr>" + "".join(cells) + "</tr>")
    w(_table_block(h2("Control points", "RESIDUALS IN mm"), head, point_rows))

    if z_ds:
        w(_table_block(h2("Z comparison", "dZ = SURFACE − CONTROL"), *_z_table(session, results, z_ds, offsets)))
    if z_ds and spots:
        w(_table_block(h2("Spot heights", "dZ = SURFACE − SPOT HEIGHT"),
                       *_spot_table(session, spots, z_ds, spot_offsets),
                       rows_per_page=COMPACT_ROWS_PER_PAGE, css_class="compact"))
    if z_ds and sections:
        w(_sections_block(session, sections, ds_by_id,
                          h2("Cross sections", "dZ = SURFACE − SURVEYED POINT · mm", "page-break")))

    if doc.named_signatories():
        w(f"<section class='block'>{h2('Sign-off')}{_signoff(doc)}</section>")

    # Point pages
    size = s.report_chip_size
    w(h2("Point details", f"CHIPS {size:g} m WIDE", "page-break"))
    for pr in results:
        cp = pr.point
        w(f"<section class='point{'' if cp.enabled else ' disabled'}' id='pt-{_e(cp.id)}'><header>"
          f"<h3>{_e(cp.id)}</h3>{_role_tag(cp.role)}{_flags(pr)}"
          f"<span class='coords'>E {cp.x:.3f} · N {cp.y:.3f} · Z {cp.z:.3f}</span></header><div class='body'>")
        w("<table><thead><tr><th>DATASET</th><th>CHECK</th><th class='n'>dX</th><th class='n'>dY</th>"
          "<th class='n'>dXY</th><th class='n'>dZ</th><th>RESULT</th></tr></thead><tbody>")
        order = {d.id: i for i, d in enumerate(session.datasets)}
        for (ds_id, check), o in sorted(pr.status.items(), key=lambda kv: (order[kv[0][0]], kv[0][1].value)):
            kind = KIND_LABEL[ds_by_id[ds_id].kind]
            if check is CheckKind.Z:
                cells = f"<td class='n'>–</td><td class='n'>–</td><td class='n'>–</td><td class='n {_tol_class(pr.dz.get(ds_id), s.tol_z)}'>{_mm(pr.dz.get(ds_id))}</td>"
            else:
                cells = (f"<td class='n'>{_mm(pr.dx.get(ds_id))}</td><td class='n'>{_mm(pr.dy.get(ds_id))}</td>"
                         f"<td class='n {_tol_class(pr.dxy.get(ds_id), s.tol_xy, False)}'>{_mm(pr.dxy.get(ds_id), False)}</td><td class='n'>–</td>")
            src = " (manual)" if o.source is ObsSource.MANUAL else ""
            extra = f", {o.n_points} pts, spread {_mm(o.spread, False)} mm" if o.n_points and o.spread is not None else ""
            w(f"<tr><td>{kind}</td><td>{check.value.upper()}</td>{cells}<td>{STATUS_LABEL[o.status]}{src}{extra}</td></tr>")
        w("</tbody></table><div class='chips'>")
        for d in session.datasets:
            img = renderer.chip(cp.id, d.id, size, px=360)
            w(f"<figure><img src='{to_data_url(img, 'JPEG')}' alt='{_e(cp.id)} {KIND_LABEL[d.kind]}'>"
              f"<figcaption>{KIND_LABEL[d.kind]} · {size:g} m · red = surveyed, cyan = measured</figcaption></figure>")
        w("</div>")
        if cp.note:
            w(f"<p class='note'>{_e(cp.note)}</p>")
        w("</div></section>")

    w(f"<p class='gen'>Generated by RealityCheck {__version__} (Plumb) · QA/QC for photogrammetry and LiDAR</p>"
      "</main></body></html>")
    return "".join(out)


def _z_table(session: Session, results, z_ds, offsets) -> tuple[str, list[str], str]:
    """Control Z against each surface's Z, point by point, with the mean offset at the foot."""
    tol = session.settings.tol_z
    head = "<th>POINT</th><th>ROLE</th><th class='n'>CONTROL Z</th>"
    head += "".join(f"<th class='n'>{_caps(KIND_LABEL[d.kind])} Z</th><th class='n'>dZ</th><th></th>" for d in z_ds)
    rows = []
    for pr in results:
        cp = pr.point
        cells = [f"<td>{_e(cp.id)}</td><td>{_role_text(cp.role)}</td><td class='n'>{cp.z:.3f}</td>"]
        for d in z_ds:
            o = pr.status.get((d.id, CheckKind.Z))
            dz = pr.dz.get(d.id)
            if dz is None:
                cells.append(f"<td class='n muted'>{_status_short(pr, d.id, CheckKind.Z)}</td><td></td><td></td>")
                continue
            cells.append(f"<td class='n'>{o.z:.3f}</td><td class='n {_tol_class(dz, tol)}'>{_mm(dz)}</td>"
                         f"<td class='muted'>{_above_below(dz)}</td>")
        rows.append(f"<tr{' class=muted' if not cp.enabled else ''}>" + "".join(cells) + "</tr>")
    by_ds = {o.dataset_id: o for o in offsets}
    foot = [f"<td colspan=3><b>Mean offset{'*' if session.settings.trim > 0 else ''}</b></td>"]
    for d in z_ds:
        o = by_ds.get(d.id)
        foot.append("<td></td><td></td><td></td>" if o is None else
                    f"<td></td><td class='n'><b>{_mm(o.mean)}</b></td><td class='muted'>{_above_below(o.mean)}</td>")
    rows.append("<tr class='foot'>" + "".join(foot) + "</tr>")
    after = ("<p class='note'>Z values in metres, dZ in mm. dZ = surface minus control: positive means the surface "
             "is higher than the surveyed point. The mean offset uses checkpoints if any are set, otherwise all "
             "enabled points; disabled points are greyed and excluded."
             + (" * Trimmed mean, see the Summary." if session.settings.trim > 0 else "") + "</p>")
    return head, rows, after


def _spot_table(session: Session, spots, z_ds, offsets) -> tuple[str, list[str], str]:
    """Spot heights (those not in a cross section): compact, dZ only. Supporting evidence for a shift or a
    reprocess."""
    star = "*" if session.settings.trim > 0 else ""
    obs = {(o.point_id, o.dataset_id): o for o in session.spot_observations if o.check is CheckKind.Z}
    head = "<th>SPOT</th><th class='n'>Z</th>" + "".join(
        f"<th class='n'>dZ {_caps(KIND_LABEL[d.kind])}</th>" for d in z_ds)
    rows = []
    for p in spots:
        if not p.enabled:  # disregarded: listed after the table
            continue
        cells = [f"<td>{_e(p.id)}</td><td class='n'>{p.z:.3f}</td>"]
        for d in z_ds:
            o = obs.get((p.id, d.id))
            if o is None or o.status is not ObsStatus.OK:
                cells.append(f"<td class='n muted'>{STATUS_LABEL[o.status] if o else '–'}</td>")
            else:
                dz = o.z - p.z
                cells.append(f"<td class='n'>{_mm(dz)} <span class='muted'>{_above_below(dz).split()[0]}</span></td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    by_ds = {o.dataset_id: o for o in offsets}
    foot_rows = [(f"Mean offset{star}", lambda o, st: f"<b>{_mm(o.mean)}</b> {_above_below(o.mean).split()[0]}"),
                 ("Median", lambda o, st: _mm(o.median)),
                 ("SD", lambda o, st: _mm(st.sd, False)),
                 ("RMSE", lambda o, st: _mm(st.rmse, False)),
                 ("n (above / below)", lambda o, st: f"{o.n} ({o.above} / {o.below})")]
    stats = {d.id: Stats.of(o.z - p.z for p in spots
                            if p.enabled and (o := obs.get((p.id, d.id))) is not None and o.status is ObsStatus.OK)
             for d in z_ds}
    for i, (label, cell) in enumerate(foot_rows):
        tds = [f"<td colspan=2>{label}</td>"]
        for d in z_ds:
            o, st = by_ds.get(d.id), stats[d.id]
            tds.append("<td></td>" if o is None or st is None else f"<td class='n'>{cell(o, st)}</td>")
        rows.append(f"<tr class='{'foot' if i == 0 else 'sub'}'>" + "".join(tds) + "</tr>")
    after = ("<p class='note'>Spot heights are surveyed ground levels with no physical mark, on hardstand and soft "
             "ground, so they carry surface roughness and are kept apart from the control point statistics. Use them "
             "as supporting evidence when a vertical shift or a reprocess is considered. dZ in mm = surface minus "
             f"spot height; ▲ surface higher, ▼ lower.{' * Trimmed mean, see the Summary.' if star else ''}</p>")
    if session.spots_disregarded:
        by_id = {p.id: p for p in session.spots}
        items = "".join(
            f"<li>{_e(pid)} <span class='muted'>E {by_id[pid].x:.3f} · N {by_id[pid].y:.3f} · Z {by_id[pid].z:.3f}"
            f" · {'no data nearby' if d is None else f'nearest data {d:.0f} m'}</span></li>"
            for pid, d in session.spots_disregarded.items() if pid in by_id)
        after += (f"<p class='note'><b>Disregarded:</b> {len(session.spots_disregarded)} spot heights are more than "
                  f"{session.settings.spot_max_outside:g} m outside the DEM and point cloud, so they are left out of "
                  f"the table and the averages:</p><ul class='disregarded note'>{items}</ul>")
    return head, rows, after


def _sections_block(session: Session, sections: list[SectionResult], ds_by_id, heading: str) -> str:
    """Each cross section: the profile chart, the fit per surface, then the surveyed points."""
    s = session.settings
    tol = s.tol_z
    out = [f"<div class='sections{'' if s.section_join else ' nojoin'}'>{heading}"]
    for r in sections:
        sec = r.section
        svg, ve = section_svg(r)
        last = sec.nodes[-1].id
        out.append(f"<section class='xs'><header><h3>{_e(sec.code)}</h3>"
                   f"<span class='sm'>{len(sec.nodes)} surveyed points · {sec.length:.2f} m · "
                   f"{_e(sec.nodes[0].id)} to {_e(last)}, bearing {sec.bearing():.0f}°</span></header>{svg}")
        legend = ["◇ surveyed points<span class='cs-join'>, joined in survey order</span>"]
        kinds = {p.kind for p in r.profiles}
        if DatasetKind.DEM in kinds:
            legend.append("— DEM")
        if DatasetKind.CLOUD in kinds:
            legend.append(f"· point cloud within {s.section_width / 2:g} m of the section, - - its median")
        out.append(f"<div class='legend'>{' · '.join(legend)} · vertical exaggeration ×{ve:.0f}</div><div class='body'>")

        # The fit per surface.
        rows = []
        for p in r.profiles:
            f = r.fits.get(p.dataset_id)
            name = _e(Path(ds_by_id[p.dataset_id].path).name)
            if f is None:
                rows.append(f"<tr><td>{name}</td><td class='n'>0</td><td colspan=5 class='muted'>No surface data "
                            "along the section</td></tr>")
                continue
            if f.shift is None:
                along, vert, note = "–", "–", f"No along-section fit: {_e(f.reason)}."
            elif f.significant:
                towards = f"towards {_e(last)}" if f.shift > 0 else f"towards {_e(sec.nodes[0].id)}"
                along, vert = _mm(f.shift), _mm(f.vertical)
                note = (f"The surface's shape matches best {abs(f.shift) * 1000:.0f} mm {towards} along the section "
                        f"(spread of dZ {f.sd_before * 1000:.0f} → {f.sd_after * 1000:.0f} mm).")
            else:
                along, vert = "0", _mm(f.mean)
                note = (f"No significant shift along the section: the best fit ({_mm(f.shift)} mm) only changes the "
                        f"spread of dZ from {f.sd_before * 1000:.0f} to {f.sd_after * 1000:.0f} mm.")
            rows.append(f"<tr><td>{name}</td><td class='n'>{f.n}</td><td class='n {_tol_class(f.mean, tol)}'>"
                        f"{_mm(f.mean)}</td><td class='n'>{_mm(f.rmse, False)}</td><td class='n'>"
                        f"{_mm(f.max_abs, False)}</td><td class='n'>{along}</td><td class='n'>{vert}</td>"
                        f"<td>{note}</td></tr>")
        out.append("<table><thead><tr><th>SURFACE</th><th class='n'>n</th><th class='n'>MEAN dZ</th>"
                   "<th class='n'>RMSE</th><th class='n'>MAX |dZ|</th><th class='n'>ALONG</th>"
                   "<th class='n'>VERTICAL</th><th>BEST FIT</th></tr></thead><tbody>" + "".join(rows)
                   + "</tbody></table>")

        # The surveyed points, compact.
        head = "<th>POINT</th><th class='n'>CHAINAGE</th><th class='n'>Z</th>" + "".join(
            f"<th class='n'>dZ {_caps(KIND_LABEL[p.kind])}</th>" for p in r.profiles)
        body = []
        for i, node in enumerate(sec.nodes):
            cells = [f"<td>{_e(node.id)}{'' if node.enabled else ' <span class=muted>(off)</span>'}</td>"
                     f"<td class='n'>{sec.chainage[i]:.2f}</td><td class='n'>{node.z:.3f}</td>"]
            for p in r.profiles:
                dz = r.dz[p.dataset_id][i]
                cells.append("<td class='n muted'>No data</td>" if not math.isfinite(dz) else
                             f"<td class='n {_tol_class(dz, tol)}'>{_mm(dz)}</td>")
            body.append("<tr>" + "".join(cells) + "</tr>")
        out.append(f"<div class='compact'><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}"
                   "</tbody></table></div></div></section>")
    out.append(
        "<p class='note'>A cross section is a string of spot heights with one code, joined in the order they were "
        f"surveyed (codes {_e(s.section_codes)}). dZ in mm = surface minus surveyed point; for a point cloud it is the "
        f"median of the corridor points within {BIN / 2:g} m of the point's chainage. ALONG is how far along the "
        "section the surface has to move for its shape to match the surveyed string best (positive = towards the "
        "last point), and VERTICAL the offset left after that move: a horizontal check without a target. It needs "
        "ground with shape (kerbs, drains, windrows); a flat section, or a best fit that barely improves the match, "
        "gives no shift. Two sections at right angles give both horizontal directions.</p></div>")
    return "".join(out)


def _above_below(dz: float) -> str:
    return "level" if abs(dz) < LEVEL else ("▲ above" if dz > 0 else "▼ below")


def _crs_text(d) -> str:
    try:
        crs = cloud_info(d.path).crs if d.kind is DatasetKind.CLOUD else raster_info(d.path).crs
    except Exception:
        return "CRS unreadable"
    return describe(crs)


def _role_text(role: Role) -> str:
    return {Role.GCP: "GCP", Role.CHECKPOINT: "Checkpoint"}.get(role, "<span class='muted'>–</span>")


def _role_tag(role: Role) -> str:
    return "" if role is Role.UNKNOWN else f"<span class='tag'>{_role_text(role)}</span>"


def _status_short(pr, ds_id, check) -> str:
    o = pr.status.get((ds_id, check))
    if o is None:
        return "<span class='muted'>–</span>"
    return {ObsStatus.NOT_FOUND: "not found", ObsStatus.NO_DATA: "no data", ObsStatus.OUT_OF_EXTENT: "outside"}.get(
        o.status, "–")


def _flags(pr) -> str:
    cp = pr.point
    tags = []
    if not cp.enabled:
        tags.append("<span class='tag'>disabled</span>")
    if cp.needs_touch_up:
        tags.append("<span class='tag'>needs touch-up</span>")
    if pr.manual:
        tags.append("<span class='tag'>manual</span>")
    if any(o.status is ObsStatus.NOT_FOUND for o in pr.status.values()):  # red: nonconformance
        tags.append("<span class='tag nc'>not found</span>")
    if cp.enabled and pr.exceeds:
        tags.append("<span class='tag nc'>exceeds tolerance</span>")
    return "".join(tags)


NC_TAG = "<span class='tag nc'>NC</span>"  # nonconformance: the only red in the report
CHECK_TAG = "<span class='tag'>CHECK</span>"


def _issues(session: Session, results, ds_by_id) -> list[str]:
    out = []
    for pr in results:
        cp = pr.point
        pid = f"<a href='#pt-{_e(cp.id)}'>{_e(cp.id)}</a>"
        if not cp.enabled:
            out.append(f"{pid} is disabled and excluded from statistics." + (f" Note: {_e(cp.note)}" if cp.note else ""))
            continue
        for e in pr.exceeds:
            out.append(f"{pid}: {_e(e)} exceeds tolerance.{NC_TAG}")
        for (ds_id, check), o in pr.status.items():
            name = KIND_LABEL[ds_by_id[ds_id].kind].lower()
            if o.status is ObsStatus.NOT_FOUND:
                out.append(f"{pid}: target not found in the {name}.{NC_TAG}")
            elif o.status is ObsStatus.OUT_OF_EXTENT:
                out.append(f"{pid}: outside the {name}.")
            elif o.status is ObsStatus.NO_DATA:
                out.append(f"{pid}: no data in the {name} at this point.{CHECK_TAG}")
        if cp.needs_touch_up:
            out.append(f"{pid}: target needs a touch-up in the field.{CHECK_TAG}")
    if all(p.role is Role.UNKNOWN for p in session.points):
        out.append("GCP / checkpoint roles are not set. Statistics mix GCPs (used in processing) and independent "
                   f"checkpoints.{CHECK_TAG}")
    return out


def write_html(session: Session, renderer: ChipRenderer, path: Path, warnings: list[str] | None = None) -> Path:
    path = Path(path)
    path.write_text(build_html(session, renderer, warnings), encoding="utf-8")
    return path


def find_browser() -> str | None:
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    found = next((c for c in candidates if os.path.isfile(c)), None)
    return found or shutil.which("msedge") or shutil.which("chrome")


def write_pdf(session: Session, renderer: ChipRenderer, path: Path, warnings: list[str] | None = None) -> Path:
    """Render the HTML report to PDF with headless Edge (or Chrome)."""
    browser = find_browser()
    if browser is None:
        raise RuntimeError("PDF export needs Microsoft Edge or Google Chrome. Export HTML and print it instead.")
    path = Path(path).resolve()
    path.unlink(missing_ok=True)
    # Edge's helper processes can keep the temporary profile locked for a moment
    # after printing; a failed clean-up must not turn a written PDF into an error.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        src = Path(tmp) / "report.html"
        write_html(session, renderer, src, warnings)
        profile = Path(tmp) / "profile"  # isolated profile so a running Edge window is not reused
        printed = Path(tmp) / "report.pdf"
        _print_to_pdf(browser, src, printed, profile)
        shutil.move(printed, path)
    return path


def _print_to_pdf(browser: str, src: Path, out: Path, profile: Path, timeout: float = 180) -> None:
    """Print src to out with headless Edge/Chrome, and wait for a complete PDF.

    The browser's exit is not a reliable signal. From a GUI process, the
    launcher can exit (code 0) before a helper process has written the PDF,
    or helper processes can keep running long after the PDF is done. So the
    browser runs detached (no pipes for helpers to hold open), completion is
    judged from the file itself, and the print job's processes are then
    closed.
    """
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={profile}",
           f"--print-to-pdf={out}", src.as_uri()]
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=no_window)
    deadline = time.monotonic() + timeout
    exited_at = None
    last_size, stable_since = -1, None
    try:
        while time.monotonic() < deadline:
            if out.exists():
                size = out.stat().st_size
                if size > 0 and size == last_size and _pdf_complete(out):
                    stable_since = stable_since or time.monotonic()
                    if time.monotonic() - stable_since >= 0.5:
                        return
                else:
                    stable_since = None
                last_size = size
            if proc.poll() is not None:
                exited_at = exited_at or time.monotonic()
                if time.monotonic() - exited_at > 20 and not out.exists():
                    raise RuntimeError(f"PDF export failed: the browser closed (exit code {proc.returncode}) "
                                       "without writing a PDF")
            time.sleep(0.25)
        raise RuntimeError(f"PDF export failed: no complete PDF after {timeout:.0f} s")
    finally:
        _close_print_job(proc, profile)


def _pdf_complete(pdf: Path) -> bool:
    try:
        with open(pdf, "rb") as f:
            f.seek(max(0, pdf.stat().st_size - 2048))
            return b"%%EOF" in f.read()
    except OSError:
        return False


def _close_print_job(proc: subprocess.Popen, profile: Path) -> None:
    """End the browser processes of this print job, found by their private profile folder."""
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if proc.poll() is None:
        proc.kill()
    marker = str(profile).replace("'", "''")
    ps = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe' OR Name='chrome.exe'\" | "
          f"Where-Object {{ $_.CommandLine -like '*{marker}*' }} | "
          "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, creationflags=no_window)
    except (OSError, subprocess.SubprocessError):
        pass  # leftover helpers exit on their own; the PDF is already complete
