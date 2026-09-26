# PyInstaller spec (one-folder). reality-check.exe with no arguments opens the GUI.
# Build from the repo root:  pyinstaller packaging/reality-check-cli.spec --noconfirm
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
for pkg in ("rasterio", "pyproj", "laspy", "lazrs", "nicegui", "webview", "clr_loader", "pythonnet"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

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
