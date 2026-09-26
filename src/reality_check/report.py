"""Single-file QA report (HTML, with PDF export via Microsoft Edge)."""

from __future__ import annotations

import html
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path

from reality_check import __version__
from reality_check.cloud import CLASS_NAMES, cloud_info
from reality_check.crs import describe
from reality_check.models import CheckKind, DatasetKind, ObsSource, ObsStatus, Role
from reality_check.raster import raster_info
from reality_check.render import ChipRenderer, to_data_url
from reality_check.review import point_results
from reality_check.overview import render_overview
from reality_check.session import Session, project_title
from reality_check.stats import summarise

KIND_LABEL = {DatasetKind.ORTHO: "Orthomosaic", DatasetKind.DEM: "DEM", DatasetKind.CLOUD: "Point cloud"}
COMPONENT_LABEL = {"dz": "dZ", "dx": "dX", "dy": "dY", "dxy": "dXY"}
STATUS_LABEL = {
    ObsStatus.OK: "OK",
    ObsStatus.NOT_FOUND: "Target not found",
    ObsStatus.NO_DATA: "No data",
    ObsStatus.OUT_OF_EXTENT: "Outside dataset",
}

CSS = """
:root { --ink:#1d2327; --muted:#5f6b73; --line:#d8dee2; --soft:#f3f6f8; --accent:#0b6e79;
        --ok:#1a7f37; --bad:#c62828; --warn:#b26a00; }
* { box-sizing:border-box; }
body { font:13px/1.45 "Segoe UI", system-ui, sans-serif; color:var(--ink); margin:0; background:#fff; }
main { max-width:1000px; margin:0 auto; padding:28px 24px 60px; }
h1 { font-size:24px; margin:0; letter-spacing:-.01em; }
h1 small { display:block; font-size:13px; font-weight:400; color:var(--muted); margin-top:2px; }
h2 { font-size:16px; margin:30px 0 10px; padding-bottom:4px; border-bottom:2px solid var(--accent); }
h3 { font-size:14px; margin:0; }
.meta { display:grid; grid-template-columns:max-content 1fr; gap:3px 16px; margin-top:16px; color:var(--muted); }
.meta b { color:var(--ink); font-weight:600; }
table { border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }
th, td { padding:4px 8px; border-bottom:1px solid var(--line); text-align:left; }
th { background:var(--soft); font-weight:600; font-size:12px; }
td.n, th.n { text-align:right; }
.cards { display:grid; grid-template-columns:repeat(auto-fit, minmax(210px, 1fr)); gap:10px; }
.card { border:1px solid var(--line); border-radius:6px; padding:10px 12px; }
.card .k { color:var(--muted); font-size:12px; }
.card .v { font-size:22px; font-weight:600; }
.card .v small { font-size:12px; font-weight:400; color:var(--muted); }
.pass { color:var(--ok); font-weight:600; } .fail { color:var(--bad); font-weight:600; }
.bad { color:var(--bad); font-weight:600; } .muted { color:var(--muted); }
.tag { display:inline-block; font-size:11px; padding:0 6px; border-radius:9px; margin-right:3px;
       background:var(--soft); border:1px solid var(--line); white-space:nowrap; }
.tag.red { color:var(--bad); border-color:#f0b4b4; background:#fdf0f0; }
.tag.amber { color:var(--warn); border-color:#f0d49a; background:#fff8e8; }
.tag.blue { color:var(--accent); border-color:#a9d3d8; background:#eef8f9; }
ul.issues { margin:0; padding-left:18px; }
.point { border:1px solid var(--line); border-radius:6px; padding:12px; margin:0 0 14px; break-inside:avoid; }
.point.disabled { opacity:.6; }
.point header { display:flex; flex-wrap:wrap; align-items:baseline; gap:8px; margin-bottom:8px; }
.point .coords { color:var(--muted); font-size:12px; margin-left:auto; }
.chips { display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:8px; margin-top:10px; }
.chips figure { margin:0; }
.chips img { width:100%; display:block; border-radius:4px; }
.chips figcaption { font-size:11px; color:var(--muted); margin-top:2px; }
figure.overview { margin:0; break-inside:avoid; }
figure.overview img { width:100%; display:block; border:1px solid var(--line); border-radius:4px; }
figure.overview figcaption { font-size:11px; color:var(--muted); margin-top:4px; }
.warn { background:#fff8e8; border:1px solid #f0d49a; border-radius:6px; padding:8px 12px; margin-top:12px; }
.note { color:var(--muted); font-size:12px; }
.table-wrap { overflow-x:auto; }
@media print {
  @page { size:A4; margin:12mm; }
  main { padding:0; max-width:none; }
  h2 { break-after:avoid; }
  .page-break { break-before:page; }
  figure.overview img { width:auto; max-width:100%; max-height:235mm; margin:0 auto; }
}
"""


def _e(s) -> str:
    return html.escape(str(s))


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
    out: list[str] = []
    w = out.append

    w(f"<title>RealityCheck report – {_e(title)}</title><style>{CSS}</style><main>")
    w(f"<h1>RealityCheck QA/QC report<small>{_e(title)}</small></h1>")
    w("<div class='meta'>")
    w(f"<span>Generated</span><b>{datetime.now():%d %b %Y %H:%M}</b>")
    w(f"<span>Control points</span><b>{_e(Path(session.control_path).name)} ({len(session.points)} points, "
      f"{sum(p.enabled for p in session.points)} enabled)</b>")
    for d in session.datasets:
        w(f"<span>{KIND_LABEL[d.kind]}</span><b>{_e(Path(d.path).name)} <span class='muted'>· {_e(_crs_text(d))}</span></b>")
    classes = s.cloud_classes
    if any(d.kind is DatasetKind.CLOUD for d in session.datasets):
        cls_txt = "all" if classes is None else ", ".join(f"{c} {CLASS_NAMES.get(c, '')}".strip() for c in classes)
        w(f"<span>Cloud Z method</span><b>median of points within {s.cloud_radius:g} m; classes: {_e(cls_txt)}</b>")
    tol = []
    if s.tol_z is not None:
        tol.append(f"Z ±{s.tol_z * 1000:.0f} mm")
    if s.tol_xy is not None:
        tol.append(f"XY {s.tol_xy * 1000:.0f} mm")
    w(f"<span>Tolerances</span><b>{', '.join(tol) or 'none set'}</b>")
    w("</div>")
    for m in warnings or []:
        w(f"<div class='warn'>{_e(m)}</div>")

    # Headline cards: checkpoints if roles are known, else all points.
    w("<h2>Summary</h2><div class='cards'>")
    for d in session.datasets:
        for comp, label, tolv in (("dz", "Z", s.tol_z), ("dxy", "XY", s.tol_xy)):
            rows = [r for r in summary if r.dataset_id == d.id and r.component == comp]
            if not rows:
                continue
            row = next((r for r in rows if r.group == "checkpoint"), None) or next(r for r in rows if r.group == "all")
            st = row.stats
            verdict = ""
            if tolv is not None:
                verdict = f"<span class='{'pass' if st.rmse <= tolv else 'fail'}'>{'PASS' if st.rmse <= tolv else 'FAIL'}</span>"
            group = "checkpoints" if row.group == "checkpoint" else "all points"
            bias = f"bias {_mm(st.mean)} mm · " if comp == "dz" else ""
            w(f"<div class='card'><div class='k'>{KIND_LABEL[d.kind]} {label} RMSE · {group} (n={st.n})</div>"
              f"<div class='v'>{_mm(st.rmse, False)} <small>mm</small> {verdict}</div>"
              f"<div class='k'>{bias}SD {_mm(st.sd, False)} mm</div></div>")
    w("</div>")

    w("<h2>Site overview</h2>")
    overview = render_overview(session)
    w(f"<figure class='overview'><img src='{to_data_url(overview, 'JPEG')}' alt='Site overview with control points'>"
      "<figcaption>Ellipses show horizontal error (semi-axes dX and dY, exaggerated by the stated factor); "
      "the line points in the error direction. Colour shows dZ.</figcaption></figure>")

    w("<h2>Statistics</h2><div class='table-wrap'><table><tr><th>Dataset</th><th>Component</th><th>Points</th>"
      "<th class='n'>n</th><th class='n'>Bias (mean)</th><th class='n'>SD</th><th class='n'>RMSE</th>"
      "<th class='n'>Max |residual|</th></tr>")
    for r in summary:
        st = r.stats
        w(f"<tr><td>{_e(Path(ds_by_id[r.dataset_id].path).name)}</td><td>{COMPONENT_LABEL[r.component]}</td>"
          f"<td>{_e(r.group)}</td><td class='n'>{st.n}</td><td class='n'>{_mm(st.mean) if r.component != 'dxy' else '–'}</td>"
          f"<td class='n'>{_mm(st.sd, False)}</td><td class='n'>{_mm(st.rmse, False)}</td>"
          f"<td class='n'>{_mm(max(abs(st.min), abs(st.max)), False)}</td></tr>")
    w("</table></div><p class='note'>All values in mm. Residual = dataset minus surveyed point. "
      "Bias is the mean residual (a systematic offset). RMSE combines bias and scatter (RMSE² ≈ bias² + SD²). "
      "Disabled points are excluded. GCPs were used in processing, so only checkpoints give an independent accuracy figure.</p>")

    issues = _issues(session, results, ds_by_id)
    w("<h2>Items to review</h2>")
    w("<ul class='issues'>" + "".join(f"<li>{i}</li>" for i in issues) + "</ul>" if issues else "<p class='muted'>None.</p>")

    # Point table
    w("<h2>Control points</h2><div class='table-wrap'><table><tr><th>Point</th><th>Role</th>")
    for d in z_ds:
        w(f"<th class='n'>dZ {KIND_LABEL[d.kind].lower()}</th>")
    for d in xy_ds:
        w(f"<th class='n'>dXY {KIND_LABEL[d.kind].lower()}</th>")
    w("<th>Flags</th></tr>")
    for pr in results:
        cp = pr.point
        w(f"<tr><td><a href='#pt-{_e(cp.id)}'>{_e(cp.id)}</a></td><td>{_role_text(cp.role)}</td>")
        for d in z_ds:
            v = pr.dz.get(d.id)
            txt = _mm(v) if v is not None else _status_short(pr, d.id, CheckKind.Z)
            w(f"<td class='n {_tol_class(v, s.tol_z)}'>{txt}</td>")
        for d in xy_ds:
            v = pr.dxy.get(d.id)
            txt = _mm(v, False) if v is not None else _status_short(pr, d.id, CheckKind.XY)
            w(f"<td class='n {_tol_class(v, s.tol_xy, False)}'>{txt}</td>")
        w(f"<td>{_flags(pr)}</td></tr>")
    w("</table></div>")

    # Point pages
    w("<h2 class='page-break'>Point details</h2>")
    size = s.report_chip_size
    for pr in results:
        cp = pr.point
        w(f"<section class='point{'' if cp.enabled else ' disabled'}' id='pt-{_e(cp.id)}'><header>"
          f"<h3>{_e(cp.id)}</h3>{_role_tag(cp.role)}{_flags(pr)}"
          f"<span class='coords'>E {cp.x:.3f} · N {cp.y:.3f} · Z {cp.z:.3f}</span></header>")
        w("<table><tr><th>Dataset</th><th>Check</th><th class='n'>dX</th><th class='n'>dY</th><th class='n'>dXY</th>"
          "<th class='n'>dZ</th><th>Result</th></tr>")
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
        w("</table><div class='chips'>")
        for d in session.datasets:
            img = renderer.chip(cp.id, d.id, size, px=360)
            w(f"<figure><img src='{to_data_url(img, 'JPEG')}' alt='{_e(cp.id)} {KIND_LABEL[d.kind]}'>"
              f"<figcaption>{KIND_LABEL[d.kind]} · {size:g} m · red = surveyed, cyan = measured</figcaption></figure>")
        w("</div>")
        if cp.note:
            w(f"<p class='note'>{_e(cp.note)}</p>")
        w("</section>")

    w(f"<p class='note'>RealityCheck {__version__} · QA/QC for photogrammetry and LiDAR</p></main>")
    return "".join(out)


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
        tags.append("<span class='tag amber'>needs touch-up</span>")
    if pr.manual:
        tags.append("<span class='tag blue'>manual</span>")
    if any(o.status is ObsStatus.NOT_FOUND for o in pr.status.values()):
        tags.append("<span class='tag red'>not found</span>")
    if cp.enabled and pr.exceeds:
        tags.append("<span class='tag red'>exceeds tolerance</span>")
    return "".join(tags)


def _issues(session: Session, results, ds_by_id) -> list[str]:
    out = []
    for pr in results:
        cp = pr.point
        pid = f"<a href='#pt-{_e(cp.id)}'>{_e(cp.id)}</a>"
        if not cp.enabled:
            out.append(f"{pid} is disabled and excluded from statistics." + (f" Note: {_e(cp.note)}" if cp.note else ""))
            continue
        for e in pr.exceeds:
            out.append(f"{pid}: {_e(e)} exceeds tolerance.")
        for (ds_id, check), o in pr.status.items():
            name = KIND_LABEL[ds_by_id[ds_id].kind].lower()
            if o.status is ObsStatus.NOT_FOUND:
                out.append(f"{pid}: target not found in the {name}.")
            elif o.status is ObsStatus.OUT_OF_EXTENT:
                out.append(f"{pid}: outside the {name}.")
            elif o.status is ObsStatus.NO_DATA:
                out.append(f"{pid}: no data in the {name} at this point.")
        if cp.needs_touch_up:
            out.append(f"{pid}: target needs a touch-up in the field.")
    if all(p.role is Role.UNKNOWN for p in session.points):
        out.append("GCP / checkpoint roles are not set. Statistics mix GCPs (used in processing) and independent checkpoints.")
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
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "report.html"
        write_html(session, renderer, src, warnings)
        profile = Path(tmp) / "profile"  # isolated profile so a running Edge window is not reused
        cmd = [browser, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={profile}",
               f"--print-to-pdf={path}", src.as_uri()]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not path.exists():
            raise RuntimeError(f"PDF export failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return path
