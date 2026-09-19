import json
import math
from pathlib import Path

import numpy as np


class SatelliteGrid:
    """Vegetation (NDVI) and surface-heat snapshot on a small WGS84 grid, with percentile ranks inside the area."""

    def __init__(self, meta, ndvi, lst):
        self.meta = meta
        self.ndvi, self.lst = ndvi, lst
        w, s, e, n = meta["aoi"]
        self.west, self.north, self.res = w, n, meta["res_deg"]
        self.rows, self.cols = ndvi.shape
        self._ndvi_sorted = np.sort(ndvi[np.isfinite(ndvi)])
        self._lst_sorted = np.sort(lst[np.isfinite(lst)])

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists():
            return None
        raw = json.loads(path.read_text())
        to_arr = lambda rows: np.array([[np.nan if v is None else v for v in r] for r in rows], dtype="float32")
        return cls(raw["meta"], to_arr(raw["ndvi"]), to_arr(raw["lst_c"]))

    def _cell(self, lat, lon):
        col = int((lon - self.west) / self.res)
        row = int((self.north - lat) / self.res)
        if not (0 <= row < self.rows and 0 <= col < self.cols):
            return None
        return row, col

    def _mean3(self, arr, row, col):
        patch = arr[max(row - 1, 0): row + 2, max(col - 1, 0): col + 2]
        vals = patch[np.isfinite(patch)]
        return float(vals.mean()) if vals.size else None

    @staticmethod
    def _pct(sorted_vals, v):
        return float(np.searchsorted(sorted_vals, v) / len(sorted_vals)) if len(sorted_vals) else None

    def sample(self, lat, lon):
        cell = self._cell(lat, lon)
        if cell is None:
            return None
        ndvi, lst = self._mean3(self.ndvi, *cell), self._mean3(self.lst, *cell)
        if ndvi is None and lst is None:
            return None
        return {
            "ndvi": None if ndvi is None else round(ndvi, 2),
            "lst_c": None if lst is None else round(lst, 1),
            # 0 = greenest / coolest in the area, 1 = least green / hottest
            "veg_low_pct": None if ndvi is None else round(1 - self._pct(self._ndvi_sorted, ndvi), 2),
            "heat_pct": None if lst is None else round(self._pct(self._lst_sorted, lst), 2),
            "ndvi_dates": [s["date"] for s in self.meta["ndvi"]["scenes"]],
            "lst_dates": [s["date"] for s in self.meta["lst"]["scenes"]],
        }

    def need_score(self, lat, lon):
        """0..1: how much a spot needs shade/greenery (low vegetation and high surface heat)."""
        s = self.sample(lat, lon)
        if not s or s["veg_low_pct"] is None or s["heat_pct"] is None:
            return None
        return round(0.5 * s["veg_low_pct"] + 0.5 * s["heat_pct"], 3)
