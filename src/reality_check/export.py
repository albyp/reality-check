"""Write residual and summary tables."""

from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

from reality_check.models import residual
from reality_check.session import Session
from reality_check.stats import summarise, z_offsets

RESIDUAL_FIELDS = [
    "point_id", "role", "enabled", "needs_touch_up", "dataset_id", "check", "status", "source",
    "survey_x", "survey_y", "survey_z", "measured_x", "measured_y", "measured_z",
    "dx", "dy", "dxy", "dz", "confidence", "n_points", "spread",
]


def _f(v, nd=4):
    return "" if v is None else f"{v:.{nd}f}"


def write_residuals(session: Session, path: Path) -> None:
    """Control points, then spot heights (role "spot")."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(RESIDUAL_FIELDS)
        for points, observations, spot in ((session.points, session.observations, False),
                                           (session.spots, session.spot_observations, True)):
            by_id = {p.id: p for p in points}
            for o in observations:
                cp = by_id[o.point_id]
                r = residual(cp, o)
                w.writerow([
                    cp.id, "spot" if spot else cp.role.value, cp.enabled, cp.needs_touch_up, o.dataset_id,
                    o.check.value, o.status.value, o.source.value, _f(cp.x), _f(cp.y), _f(cp.z), _f(o.x), _f(o.y),
                    _f(o.z), _f(r and r.dx), _f(r and r.dy), _f(r and r.dxy), _f(r and r.dz),
                    _f(o.confidence, 3), "" if o.n_points is None else o.n_points, _f(o.spread),
                ])


def write_summary(session: Session, path: Path) -> None:
    """Stats per dataset and group. trimmed_mean is filled on the dZ rows the report's offset
    lines use (checkpoints if set, else all; spot); its trim is in the column name, e.g.
    trimmed_mean_90 for a 90% trimmed mean."""
    trim = session.settings.trim
    trimmed = {(o.dataset_id, o.group): o.mean for o in
               z_offsets(session.points, session.observations, trim)
               + z_offsets(session.spots, session.spot_observations, trim, spots=True)}
    rows = summarise(session.points, session.observations)
    rows += [replace(r, group="spot") for r in summarise(session.spots, session.spot_observations) if r.group == "all"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["dataset_id", "check", "component", "group", "n", "mean", "sd", "rmse", "min", "max",
                    f"trimmed_mean_{(1 - trim) * 100:g}"])
        for row in rows:
            s = row.stats
            t = trimmed.get((row.dataset_id, row.group)) if row.component == "dz" else None
            w.writerow([row.dataset_id, row.check.value, row.component, row.group, s.n,
                        _f(s.mean), _f(s.sd), _f(s.rmse), _f(s.min), _f(s.max), _f(t)])
