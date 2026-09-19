# Turns satellite grid data (NDVI vegetation, LST heat) into colored PNG map overlays.
# Also has helpers for the image bounds and a link to a NASA true-color tile layer.

import cv2
import numpy as np

# colour stops as RGB (the palette: sand to forest green for vegetation, teal to terracotta for heat); positions are 0..1
NDVI_STOPS = [(0.0, (201, 164, 106)), (0.35, (217, 205, 140)), (0.6, (120, 170, 95)), (1.0, (35, 72, 49))]
HEAT_STOPS = [(0.0, (42, 123, 138)), (0.35, (247, 204, 134)), (0.65, (235, 167, 69)), (1.0, (196, 69, 38))]
NDVI_RANGE = (0.0, 0.8)
UPSCALE = 4
ALPHA = 165

# Text labels shown next to each layer's color legend on the map.
LEGENDS = {
    "ndvi": {"low": "Less green", "high": "More green"},
    "lst": {"low": "Cooler", "high": "Hotter"},
}


# Turn a value (0..1) into an RGB color by blending between the given color stops.
def _ramp(t, stops):
    t = np.clip(t, 0, 1)
    out = np.zeros(t.shape + (3,), dtype=np.float32)
    xs = [s[0] for s in stops]
    for c in range(3):
        out[..., c] = np.interp(t, xs, [s[1][c] for s in stops])
    return out


def render_png(grid, name):
    """Colour-mapped RGBA PNG of one satellite layer (NaN = transparent)."""
    if name == "ndvi":
        arr, lo, hi, stops = grid.ndvi, NDVI_RANGE[0], NDVI_RANGE[1], NDVI_STOPS
    elif name == "lst":
        vals = grid.lst[np.isfinite(grid.lst)]
        lo, hi = float(np.percentile(vals, 3)), float(np.percentile(vals, 97))
        arr, stops = grid.lst, HEAT_STOPS
    else:
        raise KeyError(name)
    valid = np.isfinite(arr)
    scaled = np.where(valid, (arr - lo) / max(hi - lo, 1e-6), 0)
    rgb = _ramp(scaled, stops).astype("uint8")
    alpha = np.where(valid, ALPHA, 0).astype("uint8")
    rgba = np.dstack([rgb, alpha])
    rgba = cv2.resize(rgba, (rgba.shape[1] * UPSCALE, rgba.shape[0] * UPSCALE), interpolation=cv2.INTER_CUBIC)
    bgra = cv2.cvtColor(rgba, cv2.COLOR_RGBA2BGRA)
    ok, buf = cv2.imencode(".png", bgra)
    if not ok:
        raise RuntimeError("png encode failed")
    return buf.tobytes()


def bounds_of(grid):
    """MapLibre image-source corners: NW, NE, SE, SW as [lon, lat]."""
    w, s, e, n = grid.meta["aoi"]
    east = grid.west + grid.cols * grid.res
    south = grid.north - grid.rows * grid.res
    return [[grid.west, grid.north], [east, grid.north], [east, south], [grid.west, south]]
