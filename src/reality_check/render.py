"""Render chips on demand for the GUI and the report."""

from __future__ import annotations

import base64
import io

import rasterio
from PIL import Image

from reality_check import chips
from reality_check.cloud import Chunk, filter_classes
from reality_check.models import CheckKind, DatasetKind, ObsStatus
from reality_check.review import find_obs
from reality_check.session import Session


class ChipRenderer:
    def __init__(self, session: Session, windows: dict[str, dict[str, Chunk]]):
        self.session = session
        self.windows = windows
        self._readers: dict[str, rasterio.DatasetReader] = {}

    def close(self) -> None:
        for r in self._readers.values():
            r.close()
        self._readers.clear()

    def _reader(self, ds_id: str, path: str) -> rasterio.DatasetReader:
        if ds_id not in self._readers:
            self._readers[ds_id] = rasterio.open(path)
        return self._readers[ds_id]

    def chip(self, pid: str, ds_id: str, size: float, px: int = 400, cloud_mode: str = "rgb") -> Image.Image:
        """Chip of width size (m) centred on the surveyed point, with the XY result marked."""
        cp = self.session.point(pid)
        ds = next(d for d in self.session.datasets if d.id == ds_id)
        half = size / 2
        if ds.kind is DatasetKind.ORTHO:
            img = chips.ortho_chip(self._reader(ds.id, ds.path), cp.x, cp.y, half, px)
        elif ds.kind is DatasetKind.DEM:
            img = chips.dem_chip(self._reader(ds.id, ds.path), cp.x, cp.y, half, px)
        else:
            classes = self.session.settings.cloud_classes
            w = filter_classes(self.windows.get(ds.id, {}).get(pid, _EMPTY), set(classes) if classes else None)
            img = chips.cloud_topdown(w, cp.x, cp.y, half, px, cloud_mode)

        observed = None
        o = find_obs(self.session, pid, ds_id, CheckKind.XY)
        if o is not None and o.status is ObsStatus.OK:
            observed = (o.x - cp.x, o.y - cp.y)
        return chips.annotate(img, half, observed=observed)


def to_data_url(img: Image.Image, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(buf, "JPEG", quality=85)
    else:
        img.save(buf, "PNG", optimize=False)
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode()


def _empty() -> Chunk:
    import numpy as np

    e = np.empty(0)
    return Chunk(e, e, e)


_EMPTY = _empty()
