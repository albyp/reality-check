"""Write residual and summary tables."""

from __future__ import annotations

import csv
import math
from dataclasses import replace
from pathlib import Path

import numpy as np

from reality_check.models import residual
from reality_check.sections import analyse, plain_spots, section_ids
from reality_check.session import Session
from reality_check.stats import summarise, z_offsets

RESIDUAL_FIELDS = [
    "point_id", "role", "enabled", "needs_touch_up", "dataset_id", "check", "status", "source",
    "survey_x", "survey_y", "survey_z", "measured_x", "measured_y", "measured_z",
    "dx", "dy", "dxy", "dz", "confidence", "n_points", "spread", "code",
]


def _f(v, nd=4):
    return "" if v is None else f"{v:.{nd}f}"


def write_residuals(session: Session, path: Path, windows=None) -> None:
    """Control points, then spot heights (role "spot"), then cross-section points (role "section").

    With the cloud windows, a cross-section point's dZ is the one the report shows (for a point
    cloud, the median of the corridor points at its chainage); without them, its spot height result.
    """
    in_section = section_ids(session)
    analysed = analyse(session, windows) if windows is not None and in_section else []
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(RESIDUAL_FIELDS)
        for points, observations, spot in ((session.points, session.observations, False),
                                           (session.spots, session.spot_observations, True)):
            by_id = {p.id: p for p in points}
            for o in observations:
                cp = by_id[o.point_id]
                if analysed and cp.id in in_section:
                    continue  # written from the section analysis below
                r = residual(cp, o)
                role = ("section" if cp.id in in_section else "spot") if spot else cp.role.value
                w.writerow([
                    cp.id, role, cp.enabled, cp.needs_touch_up, o.dataset_id,
                    o.check.value, o.status.value, o.source.value, _f(cp.x), _f(cp.y), _f(cp.z), _f(o.x), _f(o.y),
                    _f(o.z), _f(r and r.dx), _f(r and r.dy), _f(r and r.dxy), _f(r and r.dz),
                    _f(o.confidence, 3), "" if o.n_points is None else o.n_points, _f(o.spread), cp.code,
                ])
        for res in analysed:
            for ds_id, dzs in res.dz.items():
                for p, dz in zip(res.section.nodes, dzs):
                    ok = math.isfinite(dz)
                    w.writerow([p.id, "section", p.enabled, p.needs_touch_up, ds_id, "z",
                                "ok" if ok else "no_data", "auto", _f(p.x), _f(p.y), _f(p.z), "", "",
                                _f(p.z + dz) if ok else "", "", "", "", _f(dz) if ok else "", "", "", "", p.code])


def write_summary(session: Session, path: Path, windows=None) -> None:
    """Stats per dataset and group. trimmed_mean is filled on the dZ rows the report's offset
    lines use (checkpoints if set, else all; spot); its trim is in the column name, e.g.
    trimmed_mean_90 for a 90% trimmed mean. With the cloud windows, each cross section adds a row
    per surface (group "section <code>") with its best-fit shift along the section when it is
    significant."""
    trim = session.settings.trim
    spots = plain_spots(session)
    trimmed = {(o.dataset_id, o.group): o.mean for o in
               z_offsets(session.points, session.observations, trim)
               + z_offsets(spots, session.spot_observations, trim, spots=True)}
    rows = summarise(session.points, session.observations)
    rows += [replace(r, group="spot") for r in summarise(spots, session.spot_observations) if r.group == "all"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["dataset_id", "check", "component", "group", "n", "mean", "sd", "rmse", "min", "max",
                    f"trimmed_mean_{(1 - trim) * 100:g}", "shift_along", "vertical_after_shift"])
        for row in rows:
            s = row.stats
            t = trimmed.get((row.dataset_id, row.group)) if row.component == "dz" else None
            w.writerow([row.dataset_id, row.check.value, row.component, row.group, s.n,
                        _f(s.mean), _f(s.sd), _f(s.rmse), _f(s.min), _f(s.max), _f(t), "", ""])
        if windows is None:
            return
        for res in analyse(session, windows):
            enabled = np.array([p.enabled for p in res.section.nodes])
            for ds_id, fit in res.fits.items():
                d = res.dz[ds_id][enabled & np.isfinite(res.dz[ds_id])]
                if fit is None or not len(d):
                    continue
                w.writerow([ds_id, "z", "dz", f"section {res.section.code}", len(d), _f(d.mean()), _f(d.std(ddof=1))
                            if len(d) > 1 else "", _f(fit.rmse), _f(d.min()), _f(d.max()), "",
                            _f(fit.shift) if fit.significant else "", _f(fit.vertical) if fit.significant else ""])
