"""Snapshot the real map features the app turns into flags.

    .venv/bin/python scripts/build_features.py

Sources: OpenStreetMap (trees, storm drains, libraries, community centres) via Overpass, and Baltimore's
"Code Red Cooling Center" feature service. Writes app/data/features.json.
"""
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import ROOT, load_settings  # noqa: E402

UA = {"User-Agent": "neighborhood-missions/0.1 (hackathon build script)"}
OVERPASS = "https://overpass-api.de/api/interpreter"
COOLING = ("https://services1.arcgis.com/UWYHeuuJISiGmgXx/arcgis/rest/services/"
           "Code_Red_Cooling_Center_New/FeatureServer/0/query")


# Send an HTTP request (POST if data is given, otherwise GET) and return the parsed JSON body.
def fetch(url, data=None, timeout=120):
    req = urllib.request.Request(url, data=data, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


# Query Overpass for trees, storm drains, and community spaces inside the area, and normalize them into feature dicts.
def osm_features(aoi):
    w, s, e, n = aoi
    bbox = f"{s},{w},{n},{e}"
    q = f"""[out:json][timeout:90];
(
 node["natural"="tree"]({bbox});
 node["man_made"="storm_drain"]({bbox});
 node["manhole"="drain"]({bbox});
 node["amenity"~"^(library|community_centre)$"]({bbox});
 way["amenity"~"^(library|community_centre)$"]({bbox});
);
out center tags;"""
    data = fetch(OVERPASS, urllib.parse.urlencode({"data": q}).encode())
    out = []
    for el in data["elements"]:
        tags = el.get("tags", {})
        lat = el.get("lat") or (el.get("center") or {}).get("lat")
        lon = el.get("lon") or (el.get("center") or {}).get("lon")
        if lat is None or lon is None:
            continue
        # Classify each element by its tags into a feature kind.
        if tags.get("natural") == "tree":
            kind = "tree"
        elif tags.get("man_made") == "storm_drain" or tags.get("manhole") == "drain":
            kind = "drain"
        elif tags.get("amenity") in ("library", "community_centre"):
            kind = "community_space"
        else:
            continue
        keep = {k: tags[k] for k in ("species", "genus", "leaf_type", "name", "opening_hours", "operator",
                                     "amenity", "wheelchair", "website", "phone") if k in tags}
        out.append({
            "id": f"osm:{el['type']}:{el['id']}", "kind": kind, "source": "osm",
            "name": tags.get("name") or ("Tree" if kind == "tree" else "Storm drain" if kind == "drain" else "Public space"),
            "lat": round(lat, 6), "lon": round(lon, 6), "tags": keep,
        })
    return out


# Fetch Baltimore's Code Red cooling center locations and normalize them into feature dicts.
def cooling_centers():
    params = {"where": "1=1", "outFields": "*", "outSR": 4326, "f": "json", "resultRecordCount": 500}
    data = fetch(COOLING + "?" + urllib.parse.urlencode(params))
    out = []
    for f in data.get("features", []):
        a, g = f["attributes"], f.get("geometry") or {}
        if "x" not in g:
            continue
        out.append({
            "id": f"cc:{a.get('OBJECTID')}", "kind": "cooling_center", "source": "baltimore-code-red",
            "name": (a.get("NAME") or "Cooling center").strip(), "lat": round(g["y"], 6), "lon": round(g["x"], 6),
            "tags": {"address": a.get("ADDRESS"), "neighborhood": a.get("NGHBRHD"), "hours": a.get("Open_Hrs"),
                     "phone": a.get("CNTCT_PHN"), "url": a.get("URL")},
        })
    return out


# Fetch OSM and cooling center features, tally counts by kind, and write the combined snapshot to disk.
def main():
    aoi = load_settings().aoi
    feats = osm_features(aoi) + cooling_centers()
    counts = {}
    for f in feats:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    snapshot = {
        "meta": {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "aoi": list(aoi), "counts": counts,
                 "sources": ["OpenStreetMap contributors (ODbL) via Overpass", "Baltimore City Health Department Code Red Cooling Centers"]},
        "features": feats,
    }
    path = ROOT / "app" / "data" / "features.json"
    path.write_text(json.dumps(snapshot, separators=(",", ":")))
    print("wrote", path, counts)


if __name__ == "__main__":
    main()
