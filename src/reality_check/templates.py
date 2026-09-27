"""Project templates: where a survey's files live inside a project folder.

A template gives, for each input (control points, orthomosaic, DEM, point
cloud), a folder relative to the project root ("" = the root itself) and the
file patterns to look for there. Resolving a template against a project
folder finds one file per input, or reports it missing: an orthomosaic or DEM
may not exist, and a user may check against the point cloud only.
"""

from __future__ import annotations

import fnmatch
from dataclasses import asdict, dataclass, field
from pathlib import Path

from reality_check.detect import _LABEL, _rank, is_own_output

ROLES = ("control", "ortho", "dem", "cloud")


@dataclass
class RoleRule:
    folder: str = ""  # relative to the project root; "" = the root
    patterns: list[str] = field(default_factory=list)  # e.g. ["*.tif", "*.tiff"]; may contain sub-folders
    exclude: list[str] = field(default_factory=list)  # file-name patterns to skip, e.g. ["*dem*"]
    recursive: bool = False  # also search sub-folders of `folder`


@dataclass
class Template:
    name: str
    description: str = ""
    control: RoleRule = field(default_factory=RoleRule)
    ortho: RoleRule = field(default_factory=RoleRule)
    dem: RoleRule = field(default_factory=RoleRule)
    cloud: RoleRule = field(default_factory=RoleRule)

    def rule(self, role: str) -> RoleRule:
        return getattr(self, role)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Template:
        def rule(r: dict | None) -> RoleRule:
            r = r or {}
            return RoleRule(folder=r.get("folder", ""), patterns=list(r.get("patterns", [])),
                            exclude=list(r.get("exclude", [])), recursive=bool(r.get("recursive", False)))

        return cls(name=d["name"], description=d.get("description", ""),
                   **{role: rule(d.get(role)) for role in ROLES})


_CONTROL = ["*.csv", "*.txt"]
_TIF = ["*.tif", "*.tiff"]
_DEM_NAMES = ["*dem*", "*dsm*", "*dtm*"]
_CLOUD = ["*.laz", "*.las", "*.xyz", "*.pts"]


def builtin_templates() -> list[Template]:
    return [
        Template(
            name="Project root",
            description="Everything directly inside the project folder.",
            control=RoleRule("", _CONTROL),
            ortho=RoleRule("", _TIF, exclude=_DEM_NAMES),
            dem=RoleRule("", ["*dem*.tif", "*dsm*.tif", "*dtm*.tif", "*dem*.tiff", "*dsm*.tiff", "*dtm*.tiff"]),
            cloud=RoleRule("", _CLOUD),
        ),
        Template(
            name="Exports folder",
            description="Control points in the project folder; orthomosaic, DEM (*_dem.tif) and cloud in exports/.",
            control=RoleRule("", _CONTROL),
            ortho=RoleRule("exports", _TIF, exclude=_DEM_NAMES),
            dem=RoleRule("exports", ["*_dem.tif", "*_dem.tiff", "*dsm*.tif", "*dtm*.tif"]),
            cloud=RoleRule("exports", _CLOUD),
        ),
        Template(
            name="Pix4D (draft - check paths)",
            description="Pix4Dmapper output folders, from memory; confirm before use. Control points in the "
                        "project folder.",
            control=RoleRule("", _CONTROL),
            ortho=RoleRule("3_dsm_ortho/2_mosaic", ["*_transparent_mosaic_group1.tif", "*mosaic*.tif"]),
            dem=RoleRule("3_dsm_ortho/1_dsm", ["*_dsm.tif", "*dsm*.tif"]),
            cloud=RoleRule("2_densification/point_cloud", ["*densified_point_cloud.laz",
                                                           "*densified_point_cloud.las", "*.laz", "*.las"]),
        ),
    ]


@dataclass
class Resolution:
    project: Path
    found: dict[str, str] = field(default_factory=dict)  # role -> file path
    missing: list[str] = field(default_factory=list)  # roles with no file
    notes: list[str] = field(default_factory=list)  # several candidates, missing folders

    @property
    def runnable(self) -> bool:
        """Control points plus at least one dataset."""
        return "control" in self.found and any(r in self.found for r in ("ortho", "dem", "cloud"))

    def problem(self) -> str:
        if "control" not in self.found:
            return "No control point file."
        if not self.runnable:
            return "No orthomosaic, DEM or point cloud."
        return ""


def _candidates(root: Path, rule: RoleRule) -> tuple[list[Path], str | None]:
    folder = root / rule.folder if rule.folder else root
    if not folder.is_dir():
        return [], f"folder '{rule.folder}' not found"
    seen: dict[Path, None] = {}
    for pattern in rule.patterns:
        matches = folder.rglob(pattern) if rule.recursive else folder.glob(pattern)
        for p in matches:
            name = p.name.lower()
            if (p.is_file() and not is_own_output(p)
                    and not any(fnmatch.fnmatch(name, x.lower()) for x in rule.exclude)):
                seen.setdefault(p, None)
    return list(seen), None


def resolve(template: Template, project: str | Path) -> Resolution:
    root = Path(project)
    res = Resolution(root)
    if not root.is_dir():
        res.missing = list(ROLES)
        res.notes.append(f"{root} is not a folder.")
        return res
    for role in ("control", "dem", "ortho", "cloud"):  # DEM before ortho, so the ortho never reuses the DEM file
        rule = template.rule(role)
        if not rule.patterns:
            res.missing.append(role)
            continue
        found, why = _candidates(root, rule)
        if role == "ortho":
            dem_path = res.found.get("dem")
            found = [p for p in found if str(p) != dem_path]
        if not found:
            res.missing.append(role)
            if why:
                res.notes.append(f"{_LABEL[role].capitalize()}: {why}.")
            continue
        found.sort(key=lambda f: _rank(role, f))
        res.found[role] = str(found[0])
        if len(found) > 1:
            others = ", ".join(f.name for f in found[1:4]) + (" …" if len(found) > 4 else "")
            res.notes.append(f"{len(found)} possible {_LABEL[role]} files; using {found[0].name} (also: {others}).")
    return res
