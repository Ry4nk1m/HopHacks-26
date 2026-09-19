"""Build the bundled satellite snapshot (vegetation and surface heat) for the app's area.

    .venv/bin/python scripts/build_satellite.py

Sentinel-2 L2A -> NDVI (10 m) and Landsat 8/9 Collection 2 L2 -> land surface temperature, both read straight from
Microsoft's Planetary Computer (free, no account needed for reads). Recent, low-cloud scenes are median-composited,
resampled to a ~30 m WGS84 grid and written to app/data/satellite.json. Refresh it whenever you want newer imagery.
"""
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import rasterio
import requests
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject, transform_bounds
from rasterio.windows import from_bounds

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import ROOT, load_settings  # noqa: E402

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
SAS = "https://planetarycomputer.microsoft.com/api/sas/v1/token/{}"
RES_DEG = 0.0003
S2_LOOKBACK_DAYS = 80
LANDSAT_LOOKBACK_DAYS = 75


# Query the Planetary Computer STAC API for recent, low-cloud scenes covering the area.
def search(collection, aoi, days, max_cloud, limit=30):
    end = datetime.now(timezone.utc)
    body = {"collections": [collection], "bbox": list(aoi),
            "datetime": f"{(end - timedelta(days=days)).date()}/{end.date()}",
            "query": {"eo:cloud_cover": {"lt": max_cloud}}, "limit": limit,
            "sortby": [{"field": "properties.datetime", "direction": "desc"}]}
    r = requests.post(STAC, json=body, timeout=90)
    r.raise_for_status()
    return r.json()["features"]


# Get a short-lived token that signs asset URLs so they can be read from blob storage.
def signer(collection):
    r = requests.get(SAS.format(collection), timeout=60)
    r.raise_for_status()
    token = r.json()["token"]
    return lambda href: f"{href}?{token}"


# Read one band from a remote raster, cropped to the area of interest, in the raster's own projection.
def read_window(href, aoi, out_shape=None, resampling=Resampling.bilinear):
    with rasterio.open(href) as ds:
        left, bottom, right, top = transform_bounds("EPSG:4326", ds.crs, *aoi)
        win = from_bounds(left, bottom, right, top, ds.transform).round_offsets().round_lengths()
        arr = ds.read(1, window=win, out_shape=out_shape, resampling=resampling, boundless=True, fill_value=0)
        return arr, ds.window_transform(win), ds.crs


# Reproject and resample an array onto a fixed-resolution lat/lon grid covering the area.
def to_grid(arr, transform, crs, aoi):
    w, s, e, n = aoi
    cols, rows = math.ceil((e - w) / RES_DEG), math.ceil((n - s) / RES_DEG)
    dst = np.full((rows, cols), np.nan, dtype="float32")
    reproject(source=arr.astype("float32"), destination=dst, src_transform=transform, src_crs=crs,
              dst_transform=from_origin(w, n, RES_DEG, RES_DEG), dst_crs="EPSG:4326",
              resampling=Resampling.average, src_nodata=np.nan, dst_nodata=np.nan)
    return dst


# Compute the vegetation index (NDVI) for one Sentinel-2 scene, masking clouds and no-data pixels.
def ndvi_scene(item, sign, aoi):
    red, tr, crs = read_window(sign(item["assets"]["B04"]["href"]), aoi)
    nir, _, _ = read_window(sign(item["assets"]["B08"]["href"]), aoi, out_shape=red.shape)
    scl, _, _ = read_window(sign(item["assets"]["SCL"]["href"]), aoi, out_shape=red.shape, resampling=Resampling.nearest)
    r = np.clip((red.astype("float32") - 1000) / 10000, 0, None)
    n = np.clip((nir.astype("float32") - 1000) / 10000, 0, None)
    ndvi = (n - r) / (n + r + 1e-6)
    ok = np.isin(scl, (4, 5, 7)) & ((n + r) > 0.01)
    ndvi[~ok] = np.nan
    return to_grid(ndvi, tr, crs, aoi)


# Convert the Landsat thermal band to surface temperature in Celsius, masking cloud-affected pixels.
def lst_scene(item, sign, aoi):
    st, tr, crs = read_window(sign(item["assets"]["lwir11"]["href"]), aoi)
    qa, _, _ = read_window(sign(item["assets"]["qa_pixel"]["href"]), aoi, out_shape=st.shape, resampling=Resampling.nearest)
    kelvin = st.astype("float32") * 0.00341802 + 149.0
    celsius = kelvin - 273.15
    bad = (st == 0) | ((qa & 0b11010) != 0)  # dilated cloud, cloud, cloud shadow
    celsius[bad] = np.nan
    return to_grid(celsius, tr, crs, aoi)


# Build a median composite from multiple scenes, skipping ones that fail to load or are mostly cloud.
def composite(items, fn, sign, aoi, want):
    grids, used = [], []
    for it in items:
        if len(grids) >= want:
            break
        try:
            g = fn(it, sign, aoi)
        except Exception as exc:
            print("  skip", it["id"], str(exc)[:100])
            continue
        if np.isfinite(g).mean() < 0.4:
            print("  skip", it["id"], "mostly cloud/no data")
            continue
        grids.append(g)
        used.append({"id": it["id"], "date": it["properties"]["datetime"][:10],
                     "cloud": round(it["properties"].get("eo:cloud_cover", 0), 1)})
        print("  used", it["id"], used[-1]["date"])
    if not grids:
        raise SystemExit("no usable scenes found")
    with np.errstate(all="ignore"):
        return np.nanmedian(np.stack(grids), axis=0), used


# Convert a numpy grid into plain nested lists for JSON, rounding values and turning NaN into None.
def encode(grid, digits):
    return [[None if not np.isfinite(v) else round(float(v), digits) for v in row] for row in grid]


# Fetch Sentinel-2 and Landsat scenes, build vegetation and heat composites, and write them to satellite.json.
def main():
    aoi = load_settings().aoi
    print("Sentinel-2 (vegetation)...")
    s2 = search("sentinel-2-l2a", aoi, S2_LOOKBACK_DAYS, 25)
    ndvi, ndvi_used = composite(s2, ndvi_scene, signer("sentinel-2-l2a"), aoi, 3)
    print("Landsat (surface heat)...")
    ls = search("landsat-c2-l2", aoi, LANDSAT_LOOKBACK_DAYS, 40)
    ls = [i for i in ls if i["properties"].get("platform") in ("landsat-8", "landsat-9")]
    lst, lst_used = composite(ls, lst_scene, signer("landsat-c2-l2"), aoi, 2)
    snapshot = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "aoi": list(aoi),
            "res_deg": RES_DEG, "rows": ndvi.shape[0], "cols": ndvi.shape[1],
            "ndvi": {"source": "Sentinel-2 L2A, 10 m, median of clear scenes, aggregated to ~30 m", "scenes": ndvi_used},
            "lst": {"source": "Landsat 8/9 Collection 2 Level-2 surface temperature (~10:30 am overpass), ~100 m resampled to 30 m",
                    "scenes": lst_used},
        },
        "ndvi": encode(ndvi, 3), "lst_c": encode(lst, 1),
    }
    out = ROOT / "app" / "data" / "satellite.json"
    out.write_text(json.dumps(snapshot, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size // 1024} KB) grid {ndvi.shape}")
    print(f"NDVI  valid {np.isfinite(ndvi).mean():.0%}  mean {np.nanmean(ndvi):.2f}  p10 {np.nanpercentile(ndvi, 10):.2f}  p90 {np.nanpercentile(ndvi, 90):.2f}")
    print(f"LST C valid {np.isfinite(lst).mean():.0%}  mean {np.nanmean(lst):.1f}  p10 {np.nanpercentile(lst, 10):.1f}  p90 {np.nanpercentile(lst, 90):.1f}")


if __name__ == "__main__":
    main()
