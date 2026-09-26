"""Review GUI (NiceGUI, served on localhost and opened in the browser).

Workflow: Setup (inputs, run) -> Review (per point: adjust, enable, flag)
-> Report (live preview, export PDF / HTML / CSV). Batch runs many project
folders with a template; Settings holds defaults and templates
(settings.json next to the exe).

Single user, local only: the app state is module level and survives a page
reload.
"""

from __future__ import annotations

import logging
import os
import queue
from dataclasses import dataclass, field
from pathlib import Path

from nicegui import app, run, ui

from reality_check import __version__, appsettings
from reality_check.batch import BatchOptions, BatchResult, plan, run_one
from reality_check.cloud import CLASS_NAMES, Chunk, is_classified
from reality_check.detect import detect
from reality_check.export import write_residuals, write_summary
from reality_check.models import CheckKind, DatasetKind, ObsSource, ObsStatus, Role
from reality_check.pipeline import check_crs, load_windows, make_datasets
from reality_check.pipeline import run as run_pipeline
from reality_check.render import ChipRenderer
from reality_check.report import KIND_LABEL, build_html, write_pdf
from reality_check.review import (
    find_obs,
    point_results,
    recompute_cloud_z,
    reset_xy,
    set_manual_xy,
    set_not_found,
)
from reality_check.session import SESSION_SUFFIX, Session, Settings, output_name
from reality_check.templates import ROLES, Resolution, RoleRule, Template, resolve

CHIP_SIZES = [0.5, 1.0, 2.0, 5.0, 10.0]  # m
CHIP_PX = 400

FILE_TYPES = {
    "control": [("Control points", "*.csv *.txt"), ("All files", "*.*")],
    "ortho": [("GeoTIFF", "*.tif *.tiff"), ("All files", "*.*")],
    "dem": [("GeoTIFF", "*.tif *.tiff"), ("All files", "*.*")],
    "cloud": [("Point cloud", "*.las *.laz *.xyz *.txt *.pts"), ("All files", "*.*")],
    "session": [("RealityCheck session", f"*{SESSION_SUFFIX}"), ("All files", "*.*")],
}


@dataclass
class State:
    inputs: dict[str, str] = field(default_factory=lambda: {"control": "", "ortho": "", "dem": "", "cloud": ""})
    settings: Settings = field(default_factory=Settings)
    session: Session | None = None
    session_path: Path | None = None
    windows: dict[str, dict[str, Chunk]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    renderer: ChipRenderer | None = None
    selected: str | None = None
    size_index: int = 2
    cloud_mode: str = "rgb"
    dirty: bool = False
    busy: bool = False
    desktop: bool = False  # running in the pywebview window (drag and drop gives full paths)
    window: object | None = None  # the pywebview window, for native file dialogs
    app: appsettings.AppSettings | None = None  # settings.json: defaults and templates
    template_name: str = ""  # Setup: template for a project folder ("" = the default template)
    project_dir: str = ""  # Setup: project folder loaded through a template
    batch_template: str = ""
    batch_items: list[BatchResult] = field(default_factory=list)
    batch_options: BatchOptions = field(default_factory=BatchOptions)
    batch_running: bool = False
    edit_template: str = ""  # Settings: template open in the editor


S = State()
log = logging.getLogger("reality_check")
AUTO = "Auto-detect (no template)"
ROLE_LABEL = {"control": "Control points", "ortho": "Orthomosaic", "dem": "DEM", "cloud": "Point cloud"}


def app_settings() -> appsettings.AppSettings:
    """settings.json, loaded once. The first load also sets the run defaults."""
    if S.app is None:
        S.app = appsettings.load()
        d = S.app.defaults
        if S.session is None:
            S.settings = Settings(tol_z=d.tol_z, tol_xy=d.tol_xy, cloud_radius=d.cloud_radius,
                                  report_chip_size=d.report_chip_size)
        S.template_name = S.template_name or S.app.default_template
        S.batch_template = S.batch_template or S.app.default_template
    return S.app


def _template_names() -> list[str]:
    return [t.name for t in app_settings().templates]
LOG: queue.Queue[str] = queue.Queue()
INPUT_FIELDS: dict[str, ui.input] = {}  # role -> input element on the current page
TITLE_FIELD: list[ui.input] = []  # the Project title input on the current page
INPUT_LABELS = {"control": "Control points (CSV)", "ortho": "Orthomosaic (GeoTIFF)", "dem": "DEM (GeoTIFF)",
                "cloud": "Point cloud (LAS / LAZ / XYZ)"}

# Stop the browser from opening a dropped file in place of the app. In a plain
# browser tab the drop carries no file path, so tell the user.
_DROP_JS = """
<script>
window.addEventListener('dragover', e => e.preventDefault());
window.addEventListener('drop', e => { e.preventDefault(); if (!window.pywebview) emitEvent('rc_drop_browser', {}); });
</script>
"""


# ---------- native file dialogs (the server runs on the user's own PC) ----------
#
# In the desktop window, use pywebview's dialogs: they belong to the app window,
# so they always open on top of it. In browser mode, fall back to tkinter.

def _tk_dialog(kind: str, title: str, types, initial: str = "", directory: str = "") -> str:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "folder":
            return filedialog.askdirectory(parent=root, title=title, initialdir=directory or None)
        if kind == "open":
            return filedialog.askopenfilename(parent=root, title=title, filetypes=types, initialdir=directory or None)
        return filedialog.asksaveasfilename(parent=root, title=title, filetypes=types, initialfile=initial,
                                            initialdir=directory or None,
                                            defaultextension=types[0][1].split()[0].lstrip("*"))
    finally:
        root.destroy()


def _webview_types(types) -> tuple[str, ...]:
    """tkinter-style [("PDF", "*.pdf")] to pywebview-style ("PDF (*.pdf)",)."""
    return tuple(f"{label} ({';'.join(pattern.split())})" for label, pattern in types)


def _webview_dialog(kind: str, types, initial: str = "", directory: str = "") -> str:
    import webview

    dialog = {"open": webview.FileDialog.OPEN, "save": webview.FileDialog.SAVE,
              "folder": webview.FileDialog.FOLDER}[kind]
    result = S.window.create_file_dialog(dialog, directory=directory, save_filename=initial,
                                         file_types=_webview_types(types) if kind != "folder" else ())
    if not result:
        return ""
    path = result if isinstance(result, str) else result[0]
    if kind == "save":  # the Windows save dialog does not always add the extension
        ext = types[0][1].split()[0].lstrip("*")
        if ext.startswith(".") and not path.lower().endswith(ext.lower()):
            path += ext
    return path


async def _dialog(kind: str, title: str, types, initial: str = "", directory: str = "") -> str:
    if S.window is not None:
        return await run.io_bound(_webview_dialog, kind, types, initial, directory)
    return await run.io_bound(_tk_dialog, kind, title, types, initial, directory)


async def ask_open(title: str, types, directory: str = "") -> str:
    return await _dialog("open", title, types, directory=directory)


async def ask_save(title: str, types, initial: str, directory: str = "") -> str:
    return await _dialog("save", title, types, initial, directory)


async def ask_folder(title: str, directory: str = "") -> str:
    return await _dialog("folder", title, [("Folder", "*")], directory=directory)


def _project_dir() -> str:
    """Default folder for saving: the control file's folder."""
    ctrl = S.inputs.get("control") or (S.session.control_path if S.session else "")
    folder = Path(ctrl).parent if ctrl else None
    return str(folder) if folder and folder.is_dir() else ""


# ---------- helpers ----------

def _mm(v: float | None, signed: bool = True) -> str:
    if v is None:
        return "–"
    return f"{v * 1000:+.0f}" if signed else f"{v * 1000:.0f}"


def _ds_label(ds) -> str:
    return f"{KIND_LABEL[ds.kind]} · {Path(ds.path).name}"


def _new_renderer() -> None:
    if S.renderer is not None:
        S.renderer.close()
    S.renderer = ChipRenderer(S.session, S.windows) if S.session else None


def _mark_dirty() -> None:
    S.dirty = True


# ---------- page ----------

@ui.page("/")
def index() -> None:
    ui.colors(primary="#0b6e79")
    ui.add_body_html(_DROP_JS)
    ui.add_css("""
        .rc-chip img { image-rendering: auto; }
        .q-table td, .q-table th { padding: 4px 8px; }
        .rc-sel { background: #e3f2f4 !important; }
    """)

    with ui.header().classes("items-center bg-white text-black shadow-1 q-px-md"):
        with ui.column().classes("gap-0"):
            ui.label("RealityCheck").classes("text-h6 text-weight-bold")
            ui.label("QA/QC for photogrammetry and LiDAR").classes("text-caption text-grey-7")
        ui.space()
        ui.button("Open session", icon="folder_open", on_click=lambda: open_session()).props("flat")
        ui.button("Save session", icon="save", on_click=lambda: save_session()).props("flat")

    app_settings()
    with ui.tabs().classes("w-full bg-white text-primary shadow-1").props("align=left inline-label") as tabs:
        t_setup = ui.tab("Setup", icon="tune")
        t_review = ui.tab("Review", icon="fact_check")
        t_report = ui.tab("Report", icon="description")
        t_batch = ui.tab("Batch", icon="dynamic_feed")
        t_settings = ui.tab("Settings", icon="settings")

    with ui.tab_panels(tabs, value=t_setup).classes("w-full") as panels:
        with ui.tab_panel(t_setup):
            setup_panel(panels, t_review)
        with ui.tab_panel(t_review):
            review_panel()
        with ui.tab_panel(t_report):
            report_panel()
        with ui.tab_panel(t_batch):
            batch_panel()
        with ui.tab_panel(t_settings):
            settings_panel()

    if S.session is not None:
        panels.set_value(t_review)

    async def on_drop(e):
        paths = e.args.get("paths", [])
        if panels.value in (t_batch, "Batch"):
            add_batch_projects(paths)
            return
        sessions = [p for p in paths if p.lower().endswith(SESSION_SUFFIX)]
        if sessions:
            await load_session(sessions[0])
            return
        panels.set_value(t_setup)
        folders = [p for p in paths if Path(p).is_dir()]
        if len(paths) == 1 and folders and S.template_name != AUTO:
            await load_project_folder(folders[0])
        else:
            apply_drop(paths)

    ui.on("rc_drop", on_drop)
    ui.on("rc_drop_browser", lambda: ui.notify(
        "Drag and drop needs the RealityCheck desktop window. In a browser tab, use the Browse buttons.",
        type="warning"))

    async def open_session():
        path = await ask_open("Open session", FILE_TYPES["session"])
        if path:
            await load_session(path)

    async def save_session():
        if S.session is None:
            ui.notify("Nothing to save yet. Run the checks first.")
            return
        path = S.session_path
        if path is None:
            chosen = await ask_save("Save session", FILE_TYPES["session"], output_name(S.session, "session"),
                                    _project_dir())
            if not chosen:
                return
            path = Path(chosen)
        S.session.settings = S.settings
        S.session.save(path)
        S.session_path, S.dirty = path, False
        ui.notify(f"Saved {path.name}", type="positive")


async def load_session(path: str) -> None:
    try:
        session = Session.load(path)
    except Exception as e:  # noqa: BLE001
        ui.notify(f"Could not open session: {e}", type="negative")
        return
    S.session, S.session_path, S.settings = session, Path(path), session.settings
    ds = {d.kind: d.path for d in session.datasets}
    S.inputs = {"control": session.control_path, "ortho": ds.get(DatasetKind.ORTHO, ""),
                "dem": ds.get(DatasetKind.DEM, ""), "cloud": ds.get(DatasetKind.CLOUD, "")}
    S.busy = True
    n = ui.notification("Reading point clouds…", spinner=True, timeout=None)
    try:
        S.windows = await run.io_bound(load_windows, session, LOG.put)
        S.warnings = await run.io_bound(check_crs, session.datasets, LOG.put)
    finally:
        S.busy = False
        n.dismiss()
    _new_renderer()
    S.selected = session.points[0].id if session.points else None
    S.dirty = False
    ui.navigate.reload()


def apply_drop(paths: list[str]) -> None:
    """Fill the input fields from dropped files or folders."""
    det = detect(paths)
    if not det.found:
        ui.notify("No control points, orthomosaic, DEM or point cloud found in what was dropped.", type="warning")
    for role, path in det.found.items():
        S.inputs[role] = path
        if role in INPUT_FIELDS:
            INPUT_FIELDS[role].set_value(path)
    if det.found:
        short = {"control": "control points", "ortho": "orthomosaic", "dem": "DEM", "cloud": "point cloud"}
        names = ", ".join(short[r] for r in det.found)
        ui.notify(f"Filled {names}.", type="positive")
    for note in det.notes:
        LOG.put(note)
        ui.notify(note, type="info", multi_line=True)


# ---------- Setup ----------

def setup_panel(panels, t_review) -> None:
    with ui.row().classes("w-full gap-6 items-start no-wrap"):
        with ui.card().classes("w-1/2 min-w-[420px]"):
            ui.label("Inputs").classes("text-subtitle1 text-weight-medium")
            ui.label("All datasets are assumed to share the control points' coordinate system.").classes(
                "text-caption text-grey-7")
            def set_title(e):
                S.settings.project_title = (e.value or "").strip()
                if S.session:
                    S.session.settings = S.settings
                    _mark_dirty()

            title_inp = ui.input("Project title", value=S.settings.project_title,
                     placeholder="Defaults to the control file name",
                     on_change=set_title).classes("w-full").props("dense").tooltip(
                "Used in the report heading and in export names, e.g. <title>_RealityCheck.pdf")
            TITLE_FIELD[:] = [title_inp]
            with ui.row().classes("w-full items-center no-wrap gap-2"):
                ui.select([AUTO] + _template_names(), value=S.template_name if S.template_name in _template_names()
                          else AUTO, label="Template",
                          on_change=lambda e: setattr(S, "template_name", e.value)).classes("w-64").props("dense")
                folder_inp = ui.input("Project folder", value=S.project_dir).classes("grow").props("dense clearable")

                async def browse_folder():
                    path = await ask_folder("Project folder", S.project_dir or _project_dir())
                    if path:
                        folder_inp.set_value(path)
                        await load_project_folder(path)

                async def load_folder():
                    path = (folder_inp.value or "").strip().strip('"')
                    if path:
                        await load_project_folder(path)

                ui.button(icon="folder_open", on_click=browse_folder).props("flat dense").tooltip("Choose folder")
                ui.button("Load", on_click=load_folder).props("dense outline")
            drop_zone()
            for key, label in INPUT_LABELS.items():
                file_row(key, label)

            ui.separator()
            ui.label("Settings").classes("text-subtitle1 text-weight-medium")
            with ui.row().classes("gap-4"):
                ui.number("Cloud Z radius (m)", value=S.settings.cloud_radius, min=0.05, max=5, step=0.05,
                          format="%.2f", on_change=lambda e: setattr(S.settings, "cloud_radius", float(e.value or 0.5))
                          ).classes("w-40")
                ui.number("Z tolerance (mm)", value=S.settings.tol_z * 1000 if S.settings.tol_z else None,
                          on_change=lambda e: _set_tol("tol_z", e.value)).classes("w-40")
                ui.number("XY tolerance (mm)", value=S.settings.tol_xy * 1000 if S.settings.tol_xy else None,
                          on_change=lambda e: _set_tol("tol_xy", e.value)).classes("w-40")

            async def do_run():
                if not S.inputs["control"]:
                    ui.notify("Choose a control point file first.", type="warning")
                    return
                datasets = make_datasets(*([S.inputs[k]] if S.inputs[k] else [] for k in ("ortho", "dem", "cloud")))
                if not datasets:
                    ui.notify("Choose at least one dataset.", type="warning")
                    return
                S.busy = True
                run_btn.disable()
                spinner.set_visibility(True)
                try:
                    res = await run.io_bound(run_pipeline, S.inputs["control"], datasets, S.settings, None, LOG.put)
                except Exception as e:  # noqa: BLE001
                    LOG.put(f"ERROR: {e}")
                    ui.notify(f"Run failed: {e}", type="negative", multi_line=True)
                    return
                finally:
                    S.busy = False
                    run_btn.enable()
                    spinner.set_visibility(False)
                S.session, S.windows, S.warnings = res.session, res.windows, res.warnings
                S.session_path, S.dirty = None, True
                S.selected = S.session.points[0].id if S.session.points else None
                _new_renderer()
                LOG.put("Done. Open the Review tab.")
                classes_box.refresh()
                review_panel.refresh()
                report_panel.refresh()
                panels.set_value(t_review)

            with ui.row().classes("items-center gap-3 q-mt-sm"):
                run_btn = ui.button("Run checks", icon="play_arrow", on_click=do_run)
                spinner = ui.spinner(size="md")
                spinner.set_visibility(False)

        with ui.column().classes("w-1/2 gap-4"):
            classes_box()
            with ui.card().classes("w-full"):
                ui.label("Progress").classes("text-subtitle1 text-weight-medium")
                log = ui.log(max_lines=300).classes("w-full h-72 text-xs")

                def flush():
                    while not LOG.empty():
                        log.push(LOG.get_nowait())

                ui.timer(0.3, flush)


def _set_tol(attr: str, value) -> None:
    setattr(S.settings, attr, float(value) / 1000 if value not in (None, "") else None)
    if S.session:
        S.session.settings = S.settings
        _mark_dirty()


async def load_project_folder(folder: str) -> None:
    """Fill the inputs from a project folder with the selected template."""
    if S.template_name == AUTO:
        apply_drop([folder])
        return
    template = app_settings().template(S.template_name)
    if template is None:
        ui.notify(f"Template {S.template_name!r} not found.", type="negative")
        return
    res = resolve(template, folder)
    S.project_dir = folder
    for note in res.notes:
        LOG.put(note)
    if res.missing:
        await missing_files_dialog(res)
        return
    _apply_resolution(res.found, Path(folder).name)
    ui.notify(f"Loaded {Path(folder).name} with template {template.name!r}.", type="positive")


def _apply_resolution(found: dict[str, str], title: str) -> None:
    for role in ROLES:
        S.inputs[role] = found.get(role, "")
        if role in INPUT_FIELDS:
            INPUT_FIELDS[role].set_value(S.inputs[role])
    if not S.settings.project_title and title:
        S.settings.project_title = title
        if TITLE_FIELD:
            TITLE_FIELD[0].set_value(title)


async def missing_files_dialog(res: Resolution) -> None:
    """Some inputs were not found: let the user pick them, or go on without (not control points)."""
    chosen = dict(res.found)
    with ui.dialog().props("persistent") as dialog, ui.card().classes("min-w-[640px]"):
        ui.label(f"Files for {res.project.name}").classes("text-h6")
        ui.label("Some files were not found with this template. Choose them, or continue without them. "
                 "The orthomosaic and DEM are optional; control points and one dataset are needed.").classes(
            "text-body2 text-grey-8")

        @ui.refreshable
        def rows() -> None:
            for role in ROLES:
                with ui.row().classes("w-full items-center no-wrap gap-2"):
                    ui.label(ROLE_LABEL[role]).classes("w-32 text-weight-medium")
                    if role in chosen:
                        ui.icon("check_circle", color="positive")
                        ui.label(Path(chosen[role]).name).classes("grow ellipsis").tooltip(chosen[role])
                    else:
                        ui.icon("warning", color="warning" if role != "control" else "negative")
                        ui.label("Not found" + (" (required)" if role == "control" else " - skipped")).classes(
                            "grow text-grey-7")

                    async def pick(r=role):
                        path = await ask_open(ROLE_LABEL[r], FILE_TYPES[r], str(res.project))
                        if path:
                            chosen[r] = path
                            rows.refresh()

                    def skip(r=role):
                        chosen.pop(r, None)
                        rows.refresh()

                    ui.button("Browse", on_click=pick).props("dense flat no-caps")
                    if role in chosen:
                        ui.button("Skip", on_click=skip).props("dense flat no-caps color=grey")
            ok = "control" in chosen and any(r in chosen for r in ("ortho", "dem", "cloud"))
            with ui.row().classes("w-full justify-end gap-2 q-mt-md"):
                ui.button("Cancel", on_click=lambda: dialog.submit(None)).props("flat")
                btn = ui.button("Use these files", on_click=lambda: dialog.submit(dict(chosen)))
                if not ok:
                    btn.disable()

        rows()
        for note in res.notes:
            ui.label(note).classes("text-caption text-grey-7")
    result = await dialog
    dialog.clear()
    if result:
        _apply_resolution(result, res.project.name)
        ui.notify(f"Loaded {res.project.name}.", type="positive")


def drop_zone() -> None:
    if S.desktop:
        text = "Drag files or a whole project folder here"
        sub = ("A folder is read with the selected template. With Auto-detect: *_dem.tif → DEM, other .tif → "
               "orthomosaic, .las/.laz → point cloud, .csv → control")
    else:
        text = "Drag and drop works in the RealityCheck desktop window"
        sub = "In a browser tab, use the Browse buttons or paste a path (Shift + right-click a file → Copy as path)"
    with ui.column().classes("w-full items-center gap-0 q-pa-md rounded-borders").style(
            "border: 2px dashed #9bbfc4; background: #f4fafa"):
        ui.icon("file_download", size="md").classes("text-primary")
        ui.label(text).classes("text-body1 text-weight-medium")
        ui.label(sub).classes("text-caption text-grey-7 text-center")


def file_row(key: str, label: str) -> None:
    with ui.row().classes("w-full items-center no-wrap gap-2"):
        inp = ui.input(label, value=S.inputs[key]).classes("grow").props("dense clearable")
        INPUT_FIELDS[key] = inp
        inp.on_value_change(lambda e: S.inputs.__setitem__(key, (e.value or "").strip().strip('"')))

        async def browse():
            path = await ask_open(label, FILE_TYPES[key], _project_dir())
            if path:
                inp.set_value(path)

        ui.button(icon="more_horiz", on_click=browse).props("flat dense").tooltip("Browse")


@ui.refreshable
def classes_box() -> None:
    if S.session is None:
        return
    for ds in S.session.datasets:
        if ds.kind is not DatasetKind.CLOUD:
            continue
        counts = S.session.cloud_class_counts.get(ds.id, {})
        with ui.card().classes("w-full"):
            ui.label(f"Point cloud classes · {Path(ds.path).name}").classes("text-subtitle1 text-weight-medium")
            if not is_classified(counts):
                ui.label("The cloud is not classified. All points are used.").classes("text-grey-7")
                continue
            ui.label("Only ticked classes are used for Z checks and chips.").classes("text-caption text-grey-7")
            active = set(S.settings.cloud_classes) if S.settings.cloud_classes is not None else set(counts)

            def toggle(cls: int, on: bool) -> None:
                current = set(S.settings.cloud_classes) if S.settings.cloud_classes is not None else set(counts)
                current = current | {cls} if on else current - {cls}
                S.settings.cloud_classes = None if current >= set(counts) else sorted(current)
                S.session.settings = S.settings
                recompute_cloud_z(S.session, S.windows)
                _mark_dirty()
                review_panel.refresh()

            with ui.grid(columns=2).classes("gap-x-6 gap-y-0"):
                for cls in sorted(counts):
                    ui.checkbox(f"{cls} · {CLASS_NAMES.get(cls, 'User defined')} ({counts[cls]:,})",
                                value=cls in active, on_change=lambda e, c=cls: toggle(c, e.value))


# ---------- Review ----------

@ui.refreshable
def review_panel() -> None:
    if S.session is None:
        ui.label("Run the checks on the Setup tab first.").classes("text-grey-7")
        return
    for m in S.warnings:
        ui.label(m).classes("text-orange-9")

    with ui.row().classes("w-full no-wrap gap-4 items-start"):
        with ui.column().classes("w-[44%] min-w-[420px]"):
            point_table()
        with ui.column().classes("grow"):
            point_detail()

    ui.keyboard(on_key=_on_key, ignore=["input", "select", "button", "textarea"])


def _on_key(e) -> None:
    if not e.action.keydown:
        return
    if e.key.name in ("n", "ArrowDown", "j"):
        _step(1)
    elif e.key.name in ("p", "ArrowUp", "k"):
        _step(-1)


def _step(delta: int) -> None:
    ids = [p.id for p in S.session.points]
    if not ids:
        return
    i = ids.index(S.selected) if S.selected in ids else 0
    S.selected = ids[(i + delta) % len(ids)]
    point_table.refresh()
    point_detail.refresh()


@ui.refreshable
def point_table() -> None:
    sess = S.session
    z_ds = [d for d in sess.datasets if d.kind in (DatasetKind.DEM, DatasetKind.CLOUD)]
    xy_ds = [d for d in sess.datasets if d.kind in (DatasetKind.ORTHO, DatasetKind.CLOUD)]
    tol_z, tol_xy = S.settings.tol_z, S.settings.tol_xy
    cols = [{"name": "id", "label": "Point", "field": "id", "align": "left", "sortable": True},
            {"name": "role", "label": "Role", "field": "role", "align": "left"}]
    cols += [{"name": f"z:{d.id}", "label": f"dZ {KIND_LABEL[d.kind].split()[-1].lower()}", "field": f"z:{d.id}"}
             for d in z_ds]
    cols += [{"name": f"xy:{d.id}", "label": f"dXY {KIND_LABEL[d.kind].split()[-1].lower()}", "field": f"xy:{d.id}"}
             for d in xy_ds]
    cols.append({"name": "flags", "label": "Flags", "field": "flags", "align": "left"})

    rows = []
    for pr in point_results(sess):
        cp = pr.point
        row = {"id": cp.id, "role": cp.role.value if cp.role is not Role.UNKNOWN else "", "_exceeds": bool(pr.exceeds)}
        for d in z_ds:
            o = pr.status.get((d.id, CheckKind.Z))
            row[f"z:{d.id}"] = _mm(pr.dz[d.id]) if d.id in pr.dz else (o.status.value.replace("_", " ") if o else "–")
        for d in xy_ds:
            o = pr.status.get((d.id, CheckKind.XY))
            row[f"xy:{d.id}"] = (_mm(pr.dxy[d.id], False) if d.id in pr.dxy
                                 else ("not found" if o and o.status is ObsStatus.NOT_FOUND else "–"))
        flags = []
        if not cp.enabled:
            flags.append("off")
        if cp.needs_touch_up:
            flags.append("touch-up")
        if pr.manual:
            flags.append("manual")
        if cp.enabled and pr.exceeds:
            flags.append("exceeds")
        row["flags"] = ", ".join(flags)
        rows.append(row)

    table = ui.table(columns=cols, rows=rows, row_key="id").classes("w-full").props("dense flat bordered")
    table.props(':pagination="{rowsPerPage: 0}"')
    table.add_slot("body", r"""
        <q-tr :props="props" @click="$parent.$emit('pick', props.row.id)"
              :class="props.row.id === '""" + (S.selected or "").replace("'", "\\'") + r"""' ? 'rc-sel cursor-pointer' : 'cursor-pointer'">
            <q-td v-for="col in props.cols" :key="col.name" :props="props"
                  :class="(props.row.flags.includes('off') ? 'text-grey-5 ' : '') +
                          (props.row._exceeds && (col.name.startsWith('z:') || col.name.startsWith('xy:')) ? 'text-negative text-weight-bold' : '')">
                {{ col.value }}
            </q-td>
        </q-tr>
    """)

    def pick(e) -> None:
        S.selected = e.args
        point_table.refresh()
        point_detail.refresh()

    table.on("pick", pick)
    note = "Values in mm (dataset minus surveyed). Click a row or use N / P to move between points."
    if tol_z or tol_xy:
        note += " Bold red = outside tolerance."
    ui.label(note).classes("text-caption text-grey-7")


@ui.refreshable
def point_detail() -> None:
    sess = S.session
    if S.selected is None:
        return
    cp = sess.point(S.selected)
    size = CHIP_SIZES[S.size_index]

    with ui.row().classes("w-full items-center gap-2"):
        ui.button(icon="chevron_left", on_click=lambda: _step(-1)).props("flat dense round")
        ui.label(cp.id).classes("text-h6")
        ui.button(icon="chevron_right", on_click=lambda: _step(1)).props("flat dense round")
        ui.label(f"E {cp.x:.3f}  N {cp.y:.3f}  Z {cp.z:.3f}").classes("text-grey-7 q-ml-md")

    with ui.row().classes("w-full items-center gap-6"):
        def set_attr(attr, value):
            setattr(cp, attr, value)
            _mark_dirty()
            point_table.refresh()

        ui.switch("Enabled", value=cp.enabled, on_change=lambda e: set_attr("enabled", e.value)).tooltip(
            "Disabled points are excluded from statistics")
        ui.checkbox("Needs touch-up", value=cp.needs_touch_up,
                    on_change=lambda e: set_attr("needs_touch_up", e.value)).tooltip(
            "Flag the target to be repainted in the field")
        ui.select({r: r.value for r in Role}, value=cp.role, label="Role",
                  on_change=lambda e: set_attr("role", e.value)).classes("w-36").props("dense")
        ui.input("Note", value=cp.note, on_change=lambda e: set_attr("note", e.value)).classes("grow").props("dense")

    with ui.row().classes("w-full items-center gap-4"):
        ui.label("Chip width").classes("text-grey-8")

        def set_size(e):
            S.size_index = int(e.value)
            point_detail.refresh()

        ui.slider(min=0, max=len(CHIP_SIZES) - 1, step=1, value=S.size_index, on_change=set_size).classes("w-56")
        ui.label(f"{size:g} m").classes("text-weight-medium w-12")
        if any(d.kind is DatasetKind.CLOUD for d in sess.datasets):
            def set_mode(e):
                S.cloud_mode = e.value
                point_detail.refresh()

            ui.toggle({"rgb": "RGB", "intensity": "Intensity", "elevation": "Elevation"}, value=S.cloud_mode,
                      on_change=set_mode).props("dense no-caps")

    ui.label("Red cross = surveyed position. Click the target centre on the orthomosaic or point cloud "
             "to set the measured position (cyan).").classes("text-caption text-grey-7")

    with ui.grid(columns="repeat(auto-fill, minmax(300px, 1fr))").classes("w-full gap-3"):
        for ds in sess.datasets:
            chip_card(cp, ds, size)


def chip_card(cp, ds, size: float) -> None:
    sess = S.session
    half = size / 2
    xy_capable = ds.kind in (DatasetKind.ORTHO, DatasetKind.CLOUD)
    with ui.card().classes("p-2 gap-1 rc-chip"):
        ui.label(_ds_label(ds)).classes("text-caption text-weight-medium ellipsis w-full")
        img = S.renderer.chip(cp.id, ds.id, size, CHIP_PX, S.cloud_mode)

        def on_click(e):
            if e.type != "click":
                return
            x = cp.x - half + e.image_x / CHIP_PX * size
            y = cp.y + half - e.image_y / CHIP_PX * size
            set_manual_xy(sess, cp.id, ds.id, x, y)
            _mark_dirty()
            point_table.refresh()
            point_detail.refresh()

        ii = ui.interactive_image(img, on_mouse=on_click if xy_capable else None, events=["click"],
                                  cross="#00dcff" if xy_capable else False).classes("w-full")
        if xy_capable:
            ii.classes("cursor-crosshair")

        lines = []
        z = find_obs(sess, cp.id, ds.id, CheckKind.Z)
        if z is not None:
            if z.status is ObsStatus.OK:
                extra = f" · {z.n_points} pts, spread {_mm(z.spread, False)} mm" if z.n_points else ""
                lines.append(f"dZ {_mm(z.z - cp.z)} mm{extra}")
            else:
                lines.append(f"Z: {z.status.value.replace('_', ' ')}")
        xy = find_obs(sess, cp.id, ds.id, CheckKind.XY)
        if xy_capable:
            if xy is None:
                lines.append("XY: not measured")
            elif xy.status is ObsStatus.OK:
                src = "manual" if xy.source is ObsSource.MANUAL else "auto"
                lines.append(f"dX {_mm(xy.x - cp.x)} · dY {_mm(xy.y - cp.y)} · dXY "
                             f"{_mm(((xy.x - cp.x) ** 2 + (xy.y - cp.y) ** 2) ** 0.5, False)} mm ({src})")
            else:
                lines.append(f"XY: {xy.status.value.replace('_', ' ')}")
        for ln in lines:
            ui.label(ln).classes("text-body2")

        if xy_capable:
            with ui.row().classes("gap-1"):
                def not_found():
                    set_not_found(sess, cp.id, ds.id)
                    _mark_dirty()
                    point_table.refresh()
                    point_detail.refresh()

                def reset():
                    reset_xy(sess, cp.id, ds.id)
                    _mark_dirty()
                    point_table.refresh()
                    point_detail.refresh()

                ui.button("Not found", on_click=not_found).props("flat dense no-caps color=negative")
                ui.button("Reset", on_click=reset).props("flat dense no-caps")


# ---------- Batch ----------

def add_batch_projects(paths: list[str]) -> None:
    folders = [str(Path(p)) for p in paths if Path(p).is_dir()]
    skipped = len(paths) - len(folders)
    known = {str(i.project) for i in S.batch_items}
    new = [f for f in folders if f not in known]
    template = app_settings().template(S.batch_template)
    if template is not None:
        S.batch_items += plan(template, new)
    if skipped:
        ui.notify(f"{skipped} item(s) ignored: batch takes project folders.", type="warning")
    if new:
        ui.notify(f"Added {len(new)} project folder(s).", type="positive")
    batch_table.refresh()


def _replan() -> None:
    template = app_settings().template(S.batch_template)
    if template is not None:
        S.batch_items = plan(template, [i.project for i in S.batch_items])
    batch_table.refresh()


def batch_panel() -> None:
    with ui.row().classes("w-full items-center gap-3"):
        ui.select(_template_names(), value=S.batch_template if S.batch_template in _template_names() else None,
                  label="Template", on_change=lambda e: (setattr(S, "batch_template", e.value), _replan())
                  ).classes("w-72").props("dense")

        async def add_folder():
            path = await ask_folder("Add project folder")
            if path:
                add_batch_projects([path])

        ui.button("Add folder", icon="create_new_folder", on_click=add_folder).props("outline")
        ui.button("Clear", icon="clear_all", on_click=lambda: (S.batch_items.clear(), batch_table.refresh())
                  ).props("flat")
        ui.space()
        o = S.batch_options
        ui.checkbox("PDF", value=o.pdf, on_change=lambda e: setattr(o, "pdf", e.value))
        ui.checkbox("HTML", value=o.html, on_change=lambda e: setattr(o, "html", e.value))
        ui.checkbox("CSV", value=o.csv, on_change=lambda e: setattr(o, "csv", e.value))
    with ui.row().classes("w-full items-center gap-3"):
        out_inp = ui.input("Output folder", value=S.batch_options.out_dir,
                           placeholder="Blank = inside each project folder",
                           on_change=lambda e: setattr(S.batch_options, "out_dir", (e.value or "").strip().strip('"'))
                           ).classes("grow").props("dense clearable")

        async def pick_out():
            path = await ask_folder("Output folder")
            if path:
                out_inp.set_value(path)

        ui.button(icon="folder_open", on_click=pick_out).props("flat dense").tooltip("Choose output folder")
        run_btn = ui.button("Run batch", icon="play_arrow")

    ui.label("Drag project folders onto this tab, or use Add folder. Each project runs with the Setup tab's "
             "tolerances and cloud settings; the project folder name is the report title. Missing orthomosaic "
             "or DEM files are skipped; a project without control points is not run.").classes(
        "text-caption text-grey-7")
    batch_table()

    async def run_all():
        if S.batch_running:
            return
        runnable = [i for i in S.batch_items if i.resolution.runnable]
        if not runnable:
            ui.notify("No runnable projects. Add project folders first.", type="warning")
            return
        if not (S.batch_options.pdf or S.batch_options.html or S.batch_options.csv):
            ui.notify("Choose at least one output: PDF, HTML or CSV.", type="warning")
            return
        _replan()
        S.batch_running = True
        run_btn.props("loading")
        try:
            for n, item in enumerate(S.batch_items, start=1):
                LOG.put(f"[batch {n}/{len(S.batch_items)}] {item.project.name}")
                item.status = "running"
                batch_table.refresh()
                try:
                    await run.io_bound(run_one, item, S.settings, S.batch_options, LOG.put)
                except Exception as e:  # noqa: BLE001 - keep going with the next project
                    log.exception("Batch project %s failed", item.project)
                    item.status, item.message = "failed", f"{type(e).__name__}: {e}"
                batch_table.refresh()
        finally:
            S.batch_running = False
            run_btn.props(remove="loading")
        done = sum(i.status == "done" for i in S.batch_items)
        ui.notify(f"Batch finished: {done} of {len(S.batch_items)} projects done.", type="positive")

    run_btn.on_click(run_all)


@ui.refreshable
def batch_table() -> None:
    items = S.batch_items
    if not items:
        with ui.column().classes("w-full items-center gap-0 q-pa-xl rounded-borders").style(
                "border: 2px dashed #9bbfc4; background: #f4fafa"):
            ui.icon("create_new_folder", size="lg").classes("text-primary")
            ui.label("Drag project folders here").classes("text-body1 text-weight-medium")
        return
    with ui.grid(columns="minmax(160px,1.4fr) repeat(4, 90px) 110px minmax(200px,2fr) 150px").classes(
            "w-full items-center gap-x-3 gap-y-1"):
        for h in ("Project", "Control", "Ortho", "DEM", "Cloud", "Status", "Result", ""):
            ui.label(h).classes("text-caption text-weight-bold text-grey-8")
        for item in items:
            res = item.resolution
            ui.label(item.project.name).classes("ellipsis").tooltip(str(item.project))
            for role in ROLES:
                if role in res.found:
                    ui.icon("check_circle", color="positive").tooltip(res.found[role])
                else:
                    ui.icon("cancel" if role == "control" else "remove_circle_outline",
                            color="negative" if role == "control" else "grey").tooltip("Not found")
            colour = {"done": "positive", "failed": "negative", "skipped": "warning", "running": "primary"}.get(
                item.status, "grey")
            status = item.status if res.runnable or item.status != "pending" else "cannot run"
            ui.badge(status, color=colour if res.runnable or item.status != "pending" else "negative")
            text = "; ".join(item.headline) or item.message or res.problem() or "; ".join(res.notes)
            if item.headline and item.message:
                text += f" ({item.message})"
            ui.label(text).classes("text-caption" + (" text-negative" if "FAIL" in text else ""))
            with ui.row().classes("gap-1 no-wrap"):
                report = next((o for o in item.outputs if o.lower().endswith((".pdf", ".html"))), None)
                if report:
                    ui.button(icon="description", on_click=lambda r=report: os.startfile(r)).props(
                        "flat dense round").tooltip("Open report")
                if item.session_path:
                    ui.button(icon="fact_check", on_click=lambda s=item.session_path: load_session(s)).props(
                        "flat dense round").tooltip("Open in Review")

                def remove(it=item):
                    S.batch_items.remove(it)
                    batch_table.refresh()

                ui.button(icon="close", on_click=remove).props("flat dense round color=grey").tooltip("Remove")


# ---------- Settings ----------

def settings_panel() -> None:
    app_ = app_settings()
    ui.label(f"Settings file: {app_.path}").classes("text-caption text-grey-7")
    with ui.row().classes("w-full gap-6 items-start no-wrap"):
        with ui.card().classes("w-80"):
            ui.label("Defaults for new runs").classes("text-subtitle1 text-weight-medium")
            d = app_.defaults
            z = ui.number("Z tolerance (mm)", value=d.tol_z * 1000 if d.tol_z else None).classes("w-full")
            xy = ui.number("XY tolerance (mm)", value=d.tol_xy * 1000 if d.tol_xy else None).classes("w-full")
            rad = ui.number("Cloud Z radius (m)", value=d.cloud_radius, min=0.05, max=5, step=0.05,
                            format="%.2f").classes("w-full")
            chip = ui.select({v: f"{v:g} m" for v in CHIP_SIZES}, value=d.report_chip_size,
                             label="Report chip width").classes("w-full")
            default_tpl = ui.select(_template_names(), value=app_.default_template, label="Default template"
                                    ).classes("w-full")

            def save_defaults():
                d.tol_z = float(z.value) / 1000 if z.value not in (None, "") else None
                d.tol_xy = float(xy.value) / 1000 if xy.value not in (None, "") else None
                d.cloud_radius = float(rad.value or 0.5)
                d.report_chip_size = float(chip.value or 2.0)
                app_.default_template = default_tpl.value or app_.default_template
                _save_settings()
                ui.notify("Defaults saved. They apply to new runs.", type="positive")

            ui.button("Save defaults", icon="save", on_click=save_defaults)
        with ui.card().classes("grow"):
            template_editor()


def _save_settings() -> None:
    try:
        app_settings().save()
    except OSError as e:
        log.exception("Could not save settings")
        ui.notify(f"Could not save {app_settings().path}: {e}", type="negative", multi_line=True)


@ui.refreshable
def template_editor() -> None:
    app_ = app_settings()
    names = _template_names()
    if S.edit_template not in names:
        S.edit_template = names[0] if names else ""
    with ui.row().classes("w-full items-center gap-2"):
        ui.label("Templates").classes("text-subtitle1 text-weight-medium")
        ui.select(names, value=S.edit_template or None, label="Edit template",
                  on_change=lambda e: (setattr(S, "edit_template", e.value), template_editor.refresh())
                  ).classes("w-72").props("dense")
        ui.space()

        def new_template():
            base, n = "New template", 1
            name = base
            while app_.template(name):
                n += 1
                name = f"{base} {n}"
            app_.upsert(Template(name, "Describe where this site's files are.", control=RoleRule("", ["*.csv"])))
            S.edit_template = name
            _save_settings()
            template_editor.refresh()

        def restore():
            added = app_.restore_builtins()
            _save_settings()
            ui.notify(f"Restored: {', '.join(added)}" if added else "All built-in templates are present.")
            template_editor.refresh()

        ui.button("New", icon="add", on_click=new_template).props("flat dense")
        ui.button("Restore built-ins", icon="restore", on_click=restore).props("flat dense")

    t = app_.template(S.edit_template)
    if t is None:
        return
    name = ui.input("Name", value=t.name).classes("w-full").props("dense")
    desc = ui.input("Description", value=t.description).classes("w-full").props("dense")
    ui.label("Folder: relative to the project folder; blank = the project folder itself. Patterns and "
             "exclusions: comma-separated, e.g. *_dem.tif, *dsm*.tif").classes("text-caption text-grey-7")
    fields = {}
    with ui.grid(columns="120px 1fr 2fr 1.4fr 90px").classes("w-full items-center gap-x-2 gap-y-1"):
        for h in ("Input", "Folder", "File patterns", "Exclude", "Sub-folders"):
            ui.label(h).classes("text-caption text-weight-bold text-grey-8")
        for role in ROLES:
            r = t.rule(role)
            ui.label(ROLE_LABEL[role])
            fields[role] = (
                ui.input(value=r.folder, placeholder="(project folder)").props("dense"),
                ui.input(value=", ".join(r.patterns)).props("dense"),
                ui.input(value=", ".join(r.exclude)).props("dense"),
                ui.checkbox(value=r.recursive),
            )

    def build() -> Template:
        def split(v: str) -> list[str]:
            return [x.strip() for x in (v or "").split(",") if x.strip()]

        rules = {role: RoleRule(folder=(f[0].value or "").strip().strip("/\\"), patterns=split(f[1].value),
                                exclude=split(f[2].value), recursive=bool(f[3].value))
                 for role, f in fields.items()}
        return Template((name.value or "").strip() or t.name, (desc.value or "").strip(), **rules)

    def save():
        new = build()
        try:
            app_.upsert(new, old_name=t.name)
        except ValueError as e:
            ui.notify(str(e), type="negative")
            return
        S.edit_template = new.name
        _save_settings()
        ui.notify(f"Saved template {new.name!r}.", type="positive")
        template_editor.refresh()

    def duplicate():
        new = build()
        base = f"{new.name} copy"
        name_, n = base, 1
        while app_.template(name_):
            n += 1
            name_ = f"{base} {n}"
        new.name = name_
        app_.upsert(new)
        S.edit_template = new.name
        _save_settings()
        template_editor.refresh()

    def delete():
        if len(app_.templates) <= 1:
            ui.notify("Keep at least one template.", type="warning")
            return
        app_.remove(t.name)
        _save_settings()
        ui.notify(f"Deleted template {t.name!r}.")
        template_editor.refresh()

    async def test_on_folder():
        path = await ask_folder("Test template on a project folder")
        if not path:
            return
        res = resolve(build(), path)
        lines = [f"{ROLE_LABEL[r]}: {Path(res.found[r]).name if r in res.found else 'not found'}" for r in ROLES]
        ui.notify("\n".join([Path(path).name] + lines + res.notes), multi_line=True,
                  type="positive" if res.runnable else "warning", timeout=12000, close_button=True)

    with ui.row().classes("w-full gap-2 q-mt-sm"):
        ui.button("Save", icon="save", on_click=save)
        ui.button("Duplicate", icon="content_copy", on_click=duplicate).props("outline")
        ui.button("Test on a folder", icon="rule_folder", on_click=test_on_folder).props("outline")
        ui.space()
        ui.button("Delete", icon="delete", on_click=delete).props("flat color=negative")


# ---------- Report ----------

@ui.refreshable
def report_panel() -> None:
    if S.session is None:
        ui.label("Run the checks on the Setup tab first.").classes("text-grey-7")
        return

    with ui.row().classes("w-full items-center gap-3"):
        ui.select({s: f"{s:g} m" for s in CHIP_SIZES}, value=S.settings.report_chip_size, label="Report chip width",
                  on_change=lambda e: setattr(S.settings, "report_chip_size", e.value)).classes("w-40").props("dense")
        refresh_btn = ui.button("Refresh preview", icon="refresh")
        ui.space()
        ui.button("PDF", icon="picture_as_pdf", on_click=lambda: export("pdf"))
        ui.button("HTML", icon="code", on_click=lambda: export("html")).props("outline")
        ui.button("Residuals CSV", icon="table_view", on_click=lambda: export("residuals")).props("outline")
        ui.button("Summary CSV", icon="table_view", on_click=lambda: export("summary")).props("outline")

    frame = ui.element("iframe").classes("w-full border rounded").style("height: calc(100vh - 230px)")

    async def refresh():
        refresh_btn.props("loading")
        try:
            html = await run.io_bound(_build_report_html)
            frame._props["srcdoc"] = html
            frame.update()
        finally:
            refresh_btn.props(remove="loading")

    refresh_btn.on_click(refresh)
    ui.timer(0.1, refresh, once=True)

    async def export(kind: str):
        types = {"pdf": [("PDF", "*.pdf")], "html": [("HTML", "*.html")], "residuals": [("CSV", "*.csv")],
                 "summary": [("CSV", "*.csv")]}[kind]
        try:
            path = await ask_save(f"Export {kind}", types, output_name(S.session, kind), _project_dir())
        except Exception as e:  # noqa: BLE001
            log.exception("Save dialog failed")
            ui.notify(f"Could not open the save dialog: {type(e).__name__}: {e}", type="negative", multi_line=True)
            return
        if not path:
            return
        n = ui.notification(f"Exporting {Path(path).name}…", spinner=True, timeout=None)
        try:
            await run.io_bound(_export, kind, Path(path))
            log.info("Exported %s to %s", kind, path)
            ui.notify(f"Saved {path}", type="positive", multi_line=True)
        except PermissionError as e:
            log.exception("Export %s to %s failed", kind, path)
            ui.notify(f"Could not write {Path(path).name}: {e}. Is it open in another program (Excel, a PDF viewer)?",
                      type="negative", multi_line=True)
        except Exception as e:  # noqa: BLE001
            log.exception("Export %s to %s failed", kind, path)
            ui.notify(f"Export failed: {type(e).__name__}: {e}", type="negative", multi_line=True)
        finally:
            n.dismiss()


def _build_report_html() -> str:
    S.session.settings = S.settings
    renderer = ChipRenderer(S.session, S.windows)  # own readers: this runs in a worker thread
    try:
        return build_html(S.session, renderer, S.warnings)
    finally:
        renderer.close()


def _export(kind: str, path: Path) -> None:
    S.session.settings = S.settings
    if kind in ("pdf", "html"):
        renderer = ChipRenderer(S.session, S.windows)
        try:
            if kind == "pdf":
                write_pdf(S.session, renderer, path, S.warnings)
            else:
                path.write_text(build_html(S.session, renderer, S.warnings), encoding="utf-8")
        finally:
            renderer.close()
    elif kind == "residuals":
        write_residuals(S.session, path)
    else:
        write_summary(S.session, path)


def _port_free(port: int) -> bool:
    import socket

    with socket.socket() as sock:
        return sock.connect_ex(("127.0.0.1", port)) != 0


def _is_realitycheck(port: int) -> bool:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as r:
            return b"<title>RealityCheck" in r.read(4096)
    except Exception:  # noqa: BLE001
        return False


def start(port: int = 8765, show: bool = True) -> None:
    """Browser mode: serve on localhost and open a browser tab."""
    import webbrowser

    if not _port_free(port):
        if _is_realitycheck(port):
            print(f"RealityCheck is already running at http://127.0.0.1:{port}/")
            if show:
                webbrowser.open(f"http://127.0.0.1:{port}/")
            return
        port = next(p for p in range(port + 1, port + 100) if _port_free(p))
    app.on_shutdown(lambda: S.renderer and S.renderer.close())
    ui.run(title="RealityCheck", host="127.0.0.1", port=port, reload=False, show=show,
           reconnect_timeout=30, uvicorn_logging_level="warning")


def start_desktop() -> None:
    """Desktop window with drag and drop. Falls back to the browser if WebView2 is missing."""
    from reality_check.desktop import run_desktop

    S.desktop = True
    try:
        run_desktop()
    except Exception as e:  # noqa: BLE001
        print(f"Desktop window unavailable ({e}); opening in the browser instead.")
        S.desktop = False
        start()


if __name__ in {"__main__", "__mp_main__"}:
    start()
