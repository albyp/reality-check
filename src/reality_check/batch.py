"""Batch processing: run the checks on many project folders with one template.

Each project is resolved with the template, run automatically, and gets its
own report, session file and (optionally) CSVs. The project folder name is
the project title. A project without control points, or without any
dataset, is skipped with a reason; a missing orthomosaic or DEM is not an
error. Sessions can be opened in the GUI afterwards for manual review.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path

from reality_check.export import write_residuals, write_summary
from reality_check.models import DatasetKind
from reality_check.pipeline import make_datasets, run
from reality_check.render import ChipRenderer
from reality_check.report import write_html, write_pdf
from reality_check.review import point_results
from reality_check.session import Settings, output_name
from reality_check.stats import summarise
from reality_check.templates import Resolution, Template, resolve

log = logging.getLogger("reality_check")
Progress = Callable[[str], None]


@dataclass
class BatchOptions:
    pdf: bool = True
    html: bool = False
    csv: bool = False
    out_dir: str = ""  # "" = inside each project folder


@dataclass
class BatchResult:
    project: Path
    resolution: Resolution
    status: str = "pending"  # pending | done | skipped | failed
    message: str = ""
    outputs: list[str] = field(default_factory=list)
    session_path: str = ""
    headline: list[str] = field(default_factory=list)  # e.g. "DEM Z RMSE 20 mm (checkpoints) PASS"
    exceed_count: int = 0


def plan(template: Template, projects: list[str | Path]) -> list[BatchResult]:
    """Resolve every project before running, so the user can see what will be used."""
    return [BatchResult(Path(p), resolve(template, p)) for p in projects]


def run_one(item: BatchResult, settings: Settings, options: BatchOptions, progress: Progress = print) -> BatchResult:
    res = item.resolution
    if not res.runnable:
        item.status, item.message = "skipped", res.problem()
        return item
    f = res.found
    datasets = make_datasets([f["ortho"]] if "ortho" in f else [], [f["dem"]] if "dem" in f else [],
                             [f["cloud"]] if "cloud" in f else [])
    project_settings = replace(settings, project_title=item.project.name)
    result = run(f["control"], datasets, project_settings, progress=progress)
    s = result.session
    out = Path(options.out_dir) if options.out_dir else item.project
    out.mkdir(parents=True, exist_ok=True)

    session_path = out / output_name(s, "session")
    s.save(session_path)
    item.session_path = str(session_path)
    item.outputs.append(str(session_path))
    if options.pdf or options.html:
        renderer = ChipRenderer(s, result.windows)
        try:
            if options.html:
                item.outputs.append(str(write_html(s, renderer, out / output_name(s, "html"), result.warnings)))
            if options.pdf:
                item.outputs.append(str(write_pdf(s, renderer, out / output_name(s, "pdf"), result.warnings)))
        finally:
            renderer.close()
    if options.csv:
        for kind, writer in (("residuals", write_residuals), ("summary", write_summary)):
            path = out / output_name(s, kind)
            writer(s, path)
            item.outputs.append(str(path))

    item.headline = _headline(s)
    item.exceed_count = sum(1 for pr in point_results(s) if pr.point.enabled and pr.exceeds)
    item.status = "done"
    missing = [r for r in ("ortho", "dem", "cloud") if r not in f]
    item.message = f"Not found: {', '.join(missing)}" if missing else ""
    return item


def _headline(session) -> list[str]:
    kind = {d.id: d.kind for d in session.datasets}
    label = {DatasetKind.ORTHO: "Ortho", DatasetKind.DEM: "DEM", DatasetKind.CLOUD: "Cloud"}
    rows = summarise(session.points, session.observations)
    out = []
    for ds in session.datasets:
        for comp, name, tol in (("dz", "Z", session.settings.tol_z), ("dxy", "XY", session.settings.tol_xy)):
            mine = [r for r in rows if r.dataset_id == ds.id and r.component == comp]
            if not mine:
                continue
            r = next((r for r in mine if r.group == "checkpoint"), None) or next(r for r in mine if r.group == "all")
            verdict = "" if tol is None else (" PASS" if r.stats.rmse <= tol else " FAIL")
            group = "checkpoints" if r.group == "checkpoint" else "all points"
            out.append(f"{label[kind[ds.id]]} {name} RMSE {r.stats.rmse * 1000:.0f} mm ({group}){verdict}")
    return out


def run_batch(template: Template, projects: list[str | Path], settings: Settings, options: BatchOptions,
              progress: Progress = print, on_item: Callable[[BatchResult], None] | None = None) -> list[BatchResult]:
    items = plan(template, projects)
    for i, item in enumerate(items, start=1):
        progress(f"[{i}/{len(items)}] {item.project.name}")
        try:
            run_one(item, settings, options, progress)
        except Exception as e:  # noqa: BLE001 - one bad project must not stop the batch
            log.exception("Batch project %s failed", item.project)
            item.status, item.message = "failed", f"{type(e).__name__}: {e}"
        progress(f"  {item.status}" + (f": {item.message}" if item.message else ""))
        if on_item:
            on_item(item)
    return items
