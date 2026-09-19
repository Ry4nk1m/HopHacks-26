import json
import logging
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

log = logging.getLogger("parasol.satellite")


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


LIVE_NAME = "satellite.json"
MIN_VALID_FRACTION = 0.4


def generated_at(grid):
    try:
        return datetime.fromisoformat(grid.meta["generated_at"])
    except (TypeError, ValueError, KeyError):
        return None


def load_best(snapshot_dir, data_dir):
    """The newest usable snapshot: one the app built itself in the data dir, else the one bundled with the code."""
    candidates = []
    for path in (Path(data_dir) / LIVE_NAME, Path(snapshot_dir) / LIVE_NAME):
        try:
            grid = SatelliteGrid.load(path)
        except (OSError, ValueError, KeyError):
            log.warning("ignoring unreadable satellite snapshot %s", path)
            continue
        if grid is not None:
            candidates.append(grid)
    return max(candidates, key=lambda g: generated_at(g) or datetime.min.replace(tzinfo=timezone.utc), default=None)


def is_due(grid, days, now=None):
    """True when the snapshot is older than `days`. days <= 0 turns automatic refresh off."""
    if days <= 0:
        return False
    if grid is None:
        return True
    made = generated_at(grid)
    return made is None or (now or datetime.now(timezone.utc)) - made >= timedelta(days=days)


def check_new_snapshot(path, aoi, previous=None):
    """Load a freshly built snapshot and refuse it unless it covers the right area and has enough real data."""
    grid = SatelliteGrid.load(path)
    if grid is None:
        raise ValueError("snapshot file missing")
    if [round(v, 5) for v in grid.meta["aoi"]] != [round(v, 5) for v in aoi]:
        raise ValueError("snapshot covers a different area")
    for name, arr in (("vegetation", grid.ndvi), ("heat", grid.lst)):
        if np.isfinite(arr).mean() < MIN_VALID_FRACTION:
            raise ValueError(f"{name} layer is mostly empty")
    if previous is not None and (grid.rows, grid.cols) != (previous.rows, previous.cols):
        raise ValueError("snapshot grid size changed")
    return grid


def run_build(out_path, timeout=900):
    """Run the builder in its own process so its memory is returned to the system as soon as it finishes."""
    root = Path(__file__).resolve().parent.parent
    proc = subprocess.run([sys.executable, "-W", "ignore", "-m", "app.satellite_build", "--out", str(out_path)],
                          cwd=root, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "build failed").strip().splitlines()[-1][:300])


def refresh(settings, previous=None, build=run_build):
    """Build a new snapshot into the data dir. The live file is replaced only after the new one passes checks."""
    live = Path(settings.data_dir) / LIVE_NAME
    tmp = live.with_suffix(".json.new")
    try:
        build(tmp)
        grid = check_new_snapshot(tmp, settings.aoi, previous)
        os.replace(tmp, live)
        return grid
    finally:
        tmp.unlink(missing_ok=True)
