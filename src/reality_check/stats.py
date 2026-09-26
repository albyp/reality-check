"""Accuracy statistics over residuals."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from reality_check.models import CheckKind, ControlPoint, Observation, Role, residual


@dataclass
class Stats:
    n: int
    mean: float  # bias
    sd: float  # sample standard deviation
    rmse: float
    min: float
    max: float

    @classmethod
    def of(cls, values) -> Stats | None:
        v = np.asarray([x for x in values if x is not None], dtype=float)
        if len(v) == 0:
            return None
        return cls(
            n=len(v),
            mean=float(v.mean()),
            sd=float(v.std(ddof=1)) if len(v) > 1 else 0.0,
            rmse=float(np.sqrt((v**2).mean())),
            min=float(v.min()),
            max=float(v.max()),
        )


@dataclass
class SummaryRow:
    dataset_id: str
    check: CheckKind
    component: str  # "dz" | "dx" | "dy" | "dxy"
    group: str  # "all" | role value
    stats: Stats


def summarise(points: list[ControlPoint], observations: list[Observation]) -> list[SummaryRow]:
    """Stats per dataset, check, component and role. Disabled points are excluded."""
    by_id = {cp.id: cp for cp in points}
    groups: dict[tuple[str, CheckKind], list[tuple[ControlPoint, object]]] = {}
    for obs in observations:
        cp = by_id.get(obs.point_id)
        if cp is None or not cp.enabled:
            continue
        r = residual(cp, obs)
        if r is None:
            continue
        groups.setdefault((obs.dataset_id, obs.check), []).append((cp, r))

    rows: list[SummaryRow] = []
    for (ds_id, check), items in groups.items():
        comps = ("dz",) if check is CheckKind.Z else ("dx", "dy", "dxy")
        role_sets = [("all", items)] + [
            (role.value, [it for it in items if it[0].role is role]) for role in Role if role is not Role.UNKNOWN
        ]
        for group, subset in role_sets:
            if not subset:
                continue
            for comp in comps:
                s = Stats.of(getattr(r, comp) for _, r in subset)
                if s is not None:
                    rows.append(SummaryRow(ds_id, check, comp, group, s))
    return rows
