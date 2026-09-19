# Loads and reads map features (trees, drains, public spaces, cooling centres) from the database.

import json

from . import db
from .geo import in_bbox


def load_snapshot(conn, settings):
    """Upsert the bundled map features (trees, drains, public spaces, cooling centres) into the database."""
    path = settings.snapshot_dir / "features.json"
    if not path.exists():
        return 0
    snap = json.loads(path.read_text())
    rows = [(f["id"], f["kind"], f["source"], f["name"], f["lat"], f["lon"], json.dumps(f.get("tags", {}))) for f in snap["features"]]
    # Insert each feature, or update it if a feature with that id already exists.
    conn.executemany(
        "INSERT INTO features (id, kind, source, name, lat, lon, tags) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET kind=excluded.kind, source=excluded.source, name=excluded.name, "
        "lat=excluded.lat, lon=excluded.lon, tags=excluded.tags", rows)
    n = len(rows)
    return n


# Get features of the given kinds that fall inside the area of interest bounding box.
def features_in_aoi(conn, settings, kinds):
    marks = ",".join("?" for _ in kinds)
    out = []
    for r in conn.execute(f"SELECT * FROM features WHERE kind IN ({marks})", tuple(kinds)):
        d = dict(r)
        if in_bbox(d["lat"], d["lon"], settings.aoi):
            d["tags"] = json.loads(d["tags"] or "{}")
            d["info"] = json.loads(d["info"]) if d["info"] else None
            out.append(d)
    return out


# Save the last time a feature was checked, plus any extra info about it.
def mark_verified(conn, feature_id, info, when):
    conn.execute("UPDATE features SET last_verified_at=?, info=? WHERE id=?", (when, json.dumps(info), feature_id))
