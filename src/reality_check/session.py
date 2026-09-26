"""A QA session: inputs, settings, control points and observations.

The session is saved as JSON so a review can be reopened, adjusted and
re-reported. Manual adjustments and enable/disable flags live here.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from reality_check import __version__
from reality_check.models import (
    CheckKind,
    ControlPoint,
    Dataset,
    DatasetKind,
    Observation,
    ObsSource,
    ObsStatus,
    Role,
)

SESSION_SUFFIX = ".rcheck.json"


@dataclass
class Settings:
    cloud_radius: float = 0.5  # m, plan radius for cloud Z
    cloud_classes: list[int] | None = None  # None = all classes
    control_crs: str | None = None  # CRS library entry name, None = same as datasets
    tol_z: float | None = None  # m, per-point |dZ| tolerance
    tol_xy: float | None = None  # m, per-point dXY tolerance
    report_chip_size: float = 2.0  # m, chip width in the report


@dataclass
class Session:
    control_path: str
    points: list[ControlPoint]
    datasets: list[Dataset]
    settings: Settings = field(default_factory=Settings)
    observations: list[Observation] = field(default_factory=list)
    cloud_class_counts: dict[str, dict[int, int]] = field(default_factory=dict)  # dataset id -> counts
    created: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    def point(self, pid: str) -> ControlPoint:
        return next(p for p in self.points if p.id == pid)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self._to_dict(), indent=2, default=_enum_value), encoding="utf-8")

    def _to_dict(self) -> dict:
        d = asdict(self)
        d["cloud_class_counts"] = {k: {str(c): n for c, n in v.items()} for k, v in self.cloud_class_counts.items()}
        return {"format": "reality-check-session", "version": 1, "app_version": __version__, **d}

    @classmethod
    def load(cls, path: str | Path) -> Session:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            control_path=d["control_path"],
            points=[ControlPoint(**{**p, "role": Role(p["role"])}) for p in d["points"]],
            datasets=[Dataset(ds["id"], DatasetKind(ds["kind"]), ds["path"]) for ds in d["datasets"]],
            settings=Settings(**{k: v for k, v in d.get("settings", {}).items() if k in Settings.__dataclass_fields__}),
            observations=[
                Observation(
                    **{
                        **o,
                        "check": CheckKind(o["check"]),
                        "status": ObsStatus(o["status"]),
                        "source": ObsSource(o["source"]),
                        "auto_status": ObsStatus(o["auto_status"]) if o.get("auto_status") else None,
                    }
                )
                for o in d.get("observations", [])
            ],
            cloud_class_counts={
                k: {int(c): n for c, n in v.items()} for k, v in d.get("cloud_class_counts", {}).items()
            },
            created=d.get("created", ""),
        )


def _enum_value(o):
    if isinstance(o, Enum):
        return o.value
    raise TypeError(f"not JSON serialisable: {type(o)}")
