"""Application settings: settings.json next to the executable.

Holds default values for new runs and the project templates. The file is
created on first use with the built-in templates, so it can be edited in the
app (Settings tab) or by hand.

Location, in order:
1. RC_SETTINGS environment variable (tests, portable setups).
2. Next to RealityCheck.exe (frozen build), or the repo root (development).
3. %APPDATA%\\RealityCheck\\settings.json if that folder is not writable,
   for example when the exe is installed under Program Files.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from reality_check.templates import Template, builtin_templates

FILE_NAME = "settings.json"
log = logging.getLogger("reality_check")


@dataclass
class Defaults:
    tol_z: float | None = None  # m
    tol_xy: float | None = None  # m
    cloud_radius: float = 0.5  # m
    report_chip_size: float = 2.0  # m


@dataclass
class AppSettings:
    path: Path
    defaults: Defaults = field(default_factory=Defaults)
    default_template: str = "Project root"
    templates: list[Template] = field(default_factory=builtin_templates)

    def template(self, name: str) -> Template | None:
        return next((t for t in self.templates if t.name == name), None)

    def upsert(self, template: Template, old_name: str | None = None) -> None:
        """Add a new template (old_name None), or replace the template called old_name.

        Names are unique: adding, or renaming to, a name in use raises ValueError.
        """
        clash = self.template(template.name)
        if old_name is None:
            if clash:
                raise ValueError(f"A template called {template.name!r} already exists")
            self.templates.append(template)
            return
        if clash and template.name != old_name:
            raise ValueError(f"A template called {template.name!r} already exists")
        for i, t in enumerate(self.templates):
            if t.name == old_name:
                self.templates[i] = template
                if self.default_template == old_name:
                    self.default_template = template.name
                return
        raise KeyError(old_name)

    def remove(self, name: str) -> None:
        self.templates = [t for t in self.templates if t.name != name]
        if self.default_template == name:
            self.default_template = self.templates[0].name if self.templates else ""

    def restore_builtins(self) -> list[str]:
        """Add back any built-in template that is missing. Returns the names added."""
        added = []
        for t in builtin_templates():
            if not self.template(t.name):
                self.templates.append(t)
                added.append(t.name)
        return added

    def save(self) -> None:
        payload = {
            "format": "reality-check-settings",
            "version": 1,
            "defaults": asdict(self.defaults),
            "default_template": self.default_template,
            "templates": [t.to_dict() for t in self.templates],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def _app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    repo = Path(__file__).resolve().parents[2]
    if (repo / "pyproject.toml").exists():
        return repo
    return _appdata_dir()


def _appdata_dir() -> Path:
    return Path(os.environ.get("APPDATA") or Path.home() / ".config") / "RealityCheck"


def _writable(folder: Path) -> bool:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".rc_write_test"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def settings_path() -> Path:
    override = os.environ.get("RC_SETTINGS")
    if override:
        return Path(override)
    app = _app_dir()
    if _writable(app):
        return app / FILE_NAME
    log.info("%s is not writable; using the per-user settings folder", app)
    return _appdata_dir() / FILE_NAME


def load(path: Path | None = None) -> AppSettings:
    """Load settings, creating the file with the built-in templates if it does not exist."""
    path = path or settings_path()
    if not path.exists():
        s = AppSettings(path)
        s.save()
        return s
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        # Keep the unreadable file for the user to inspect; start from defaults.
        backup = path.with_suffix(".json.bad")
        log.warning("Could not read %s (%s); saved a copy as %s and started from defaults", path, e, backup.name)
        try:
            path.replace(backup)
        except OSError:
            pass
        s = AppSettings(path)
        s.save()
        return s
    known = Defaults.__dataclass_fields__
    defaults = Defaults(**{k: v for k, v in d.get("defaults", {}).items() if k in known})
    templates = [Template.from_dict(t) for t in d.get("templates", [])] or builtin_templates()
    return AppSettings(path, defaults, d.get("default_template", templates[0].name), templates)
