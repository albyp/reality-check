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


LEVEL = 0.0005  # m: differences under 0.5 mm count as level (shown as +0 / -0 mm)


def trim_count(n: int, trim: float) -> int:
    """How many values a trimmed mean leaves out in total. As Excel TRIMMEAN: n * trim
    rounded down to an even number, half from each end."""
    return int(n * trim // 2) * 2


def trimmed_mean(values, trim: float) -> float:
    a = np.sort(np.asarray(values, dtype=float))
    k = trim_count(len(a), trim) // 2
    return float(a[k:len(a) - k].mean())


@dataclass
class ZOffset:
    """Which way a surface sits relative to the control: higher or lower, on average."""

    dataset_id: str
    group: str  # "checkpoint" / "all" (control points), "spot" (spot heights)
    n: int
    mean: float  # trimmed mean when trim > 0 (the headline offset)
    median: float
    above: int  # points where the surface is higher than the control
    below: int
    level: int  # within LEVEL of the control
    trim: float = 0.0
    trimmed_out: int = 0  # values the trimmed mean left out

    def sentence(self, label: str) -> str:
        star = "*" if self.trim > 0 else ""
        against = "the spot heights" if self.group == "spot" else "the control"
        if abs(self.mean) < LEVEL:
            where = f"level with {against} on average{star}"
        else:
            where = f"{abs(self.mean) * 1000:.0f} mm{star} {'above' if self.mean > 0 else 'below'} {against} on average"
        group = {"checkpoint": "checkpoints", "spot": "spot heights"}.get(self.group, "points")
        parts = [f"{self.above} above", f"{self.below} below"] + ([f"{self.level} level"] if self.level else [])
        return (f"The {label} sits {where} (median {self.median * 1000:+.0f} mm; "
                f"of {self.n} {group}: {', '.join(parts)}).")


def z_offsets(points: list[ControlPoint], observations: list[Observation], trim: float = 0.0,
              spots: bool = False) -> list[ZOffset]:
    """Per Z dataset: is the surface higher or lower than the points? For control
    points, checkpoints if any are set (GCPs sit near zero by design), else all
    points; spots=True marks the points as spot heights. Disabled points are
    excluded. The offset is a trimmed mean when trim > 0."""
    by_id = {cp.id: cp for cp in points if cp.enabled}
    per_ds: dict[str, list[tuple[ControlPoint, float]]] = {}
    for obs in observations:
        cp = by_id.get(obs.point_id)
        r = residual(cp, obs) if cp is not None and obs.check is CheckKind.Z else None
        if r is not None:
            per_ds.setdefault(obs.dataset_id, []).append((cp, r.dz))
    out = []
    for ds_id, items in per_ds.items():
        checks = [dz for cp, dz in items if cp.role is Role.CHECKPOINT]
        if spots:
            group, v = "spot", [dz for _, dz in items]
        else:
            group, v = ("checkpoint", checks) if checks else ("all", [dz for _, dz in items])
        a = np.asarray(v, dtype=float)
        out.append(ZOffset(ds_id, group, len(a), trimmed_mean(a, trim), float(np.median(a)),
                           int((a >= LEVEL).sum()), int((a <= -LEVEL).sum()), int((abs(a) < LEVEL).sum()),
                           trim, trim_count(len(a), trim)))
    return out


def trim_note(offsets: list[ZOffset]) -> str:
    """The asterisk note for offsets given as trimmed means ("" if none are)."""
    trimmed = [o for o in offsets if o.trim > 0]
    if not trimmed:
        return ""
    t = trimmed[0].trim
    left_out = sorted({(o.n, o.trimmed_out) for o in trimmed})
    detail = "; ".join(f"of {n}, {k} left out" if k else f"of {n}, none left out" for n, k in left_out)
    return (f"* Trimmed mean ({(1 - t) * 100:g}%): the highest and lowest {t * 50:g}% of dZ values are left out "
            f"before averaging, as Excel TRIMMEAN (the number left out is rounded down to an even number: {detail}). "
            "The median and the above/below counts use every value. Change the trim in Settings.")


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
