"""Point GDAL and PROJ at the data files bundled with rasterio.

Mining software (for example GEOVIA Surpac) sets PROJ_LIB and GDAL_DATA
system-wide to its own, older copies. rasterio then loads an incompatible
proj.db and every EPSG lookup fails. This module must run before rasterio
is imported.
"""

import importlib.util
import os
import sys
from pathlib import Path


def _rasterio_root() -> Path | None:
    if getattr(sys, "frozen", False):  # PyInstaller bundle
        return Path(sys._MEIPASS) / "rasterio"
    spec = importlib.util.find_spec("rasterio")
    if spec is None or spec.origin is None:
        return None
    return Path(spec.origin).parent


def use_bundled_data() -> None:
    root = _rasterio_root()
    if root is None:
        return
    proj, gdal = root / "proj_data", root / "gdal_data"
    if (proj / "proj.db").exists():
        os.environ["PROJ_DATA"] = str(proj)
        os.environ["PROJ_LIB"] = str(proj)  # older PROJ versions read PROJ_LIB
    if gdal.is_dir():
        os.environ["GDAL_DATA"] = str(gdal)


use_bundled_data()
