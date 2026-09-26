# PyInstaller spec (one-folder, windowed). RealityCheck.exe with no arguments opens the GUI.
# Build with packaging\build.ps1, which also removes build\work and makes the release zip.
import importlib.metadata as md

from packaging.requirements import Requirement
from PyInstaller.utils.hooks import collect_all, copy_metadata

datas, binaries, hiddenimports = [], [], []
for pkg in ("rasterio", "pyproj", "laspy", "lazrs", "nicegui", "webview", "clr_loader", "pythonnet"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h


def runtime_dists(name, extras=(), seen=None):
    """reality-check's runtime dependency closure on Windows."""
    seen = set() if seen is None else seen
    key = name.lower().replace("_", "-")
    if key in seen:
        return seen
    try:
        dist = md.distribution(name)
    except md.PackageNotFoundError:
        return seen
    seen.add(key)
    for r in dist.requires or []:
        req = Requirement(r)
        envs = [{"extra": e, "sys_platform": "win32", "platform_system": "Windows", "os_name": "nt"}
                for e in (extras or ("",))]
        if req.marker and not any(req.marker.evaluate(e) for e in envs):
            continue
        runtime_dists(req.name, tuple(req.extras), seen)
    return seen


# Ship every dependency's dist-info (with its license text): MIT/BSD require the
# notice to travel with binary distributions.
for dist_name in sorted(runtime_dists("reality-check") - {"reality-check"}):
    datas += copy_metadata(dist_name)

a = Analysis(
    ["entry.py"],
    pathex=["../src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["matplotlib", "IPython", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="RealityCheck", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="RealityCheck")
