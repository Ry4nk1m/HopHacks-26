# Decides which flags (trees needing water, drains, cooling spaces, city reports) should
# currently be active, and syncs that list into the database.

import json
from datetime import datetime, timedelta, timezone

from . import db, rules, signals
from .features import features_in_aoi
from .geo import haversine_m

TREE_LIMIT = 150
TREE_MIN_SEP_M = 150
TREE_STRONG_NEED = 0.70
MAX_ACTIVE_FLAGS = 2000


# Format a datetime as an ISO string without microseconds.
def _iso(dt):
    return dt.replace(microsecond=0).isoformat()


def select_priority_trees(trees, satellite, exclude_ids, limit=TREE_LIMIT, min_sep_m=TREE_MIN_SEP_M):
    """Pick well-spread trees where satellite data says shade matters most (low vegetation, high surface heat)."""
    scored = []
    for t in trees:
        if t["id"] in exclude_ids:
            continue
        need = satellite.need_score(t["lat"], t["lon"]) if satellite else 0.5
        if need is not None:
            scored.append((need, t))
    scored.sort(key=lambda p: (-p[0], p[1]["id"]))
    # Group trees into a grid of cells so the "too close to an already-picked tree" check only
    # has to look at nearby cells instead of every tree picked so far.
    chosen, cells = [], {}
    reach = int(min_sep_m // 80) + 1  # grid cells are about 85 to 110 m wide, so look this many cells out to cover the whole gap
    span = range(-reach, reach + 1)
    for need, t in scored:
        ci, cj = int(t["lat"] / 0.001), int(t["lon"] / 0.001)
        if any(haversine_m(t["lat"], t["lon"], o["lat"], o["lon"]) < min_sep_m
               for di in span for dj in span for o in cells.get((ci + di, cj + dj), [])):
            continue
        cells.setdefault((ci, cj), []).append(t)
        chosen.append((t, need))
        if len(chosen) >= limit:
            break
    return chosen


# Sample satellite data for a location, or None if there is no satellite grid loaded.
def _sat(satellite, lat, lon):
    return satellite.sample(lat, lon) if satellite else None


# Higher urgency for heavier rain, plus one more if there is an active flood alert.
def _drain_urgency(cond):
    mm = cond["rain_mm"]
    u = 3 if mm >= 25 else 2 if mm >= 12 else 1
    return min(u + (1 if cond["flood_alert"] else 0), 3)


def build_specs(conn, settings, satellite, cond, now):
    """Everything that should currently be flagged, keyed by (type, source, source_ref)."""
    specs = {}

    # Register one flag spec, keyed by its type, source, and source reference.
    def add(**s):
        s.setdefault("priority", round(s["urgency"] + 0.0, 3))
        specs[(s["type"], s["source"], s["source_ref"])] = s

    # --- trees: dry spell + satellite need
    if cond["dry"]:
        cooling_days = rules.FLAG_TYPES["tree_water"]["cooldown_days"]
        cutoff = _iso(now - timedelta(days=cooling_days))
        excluded = {r["feature_id"] for r in conn.execute(
            "SELECT feature_id FROM flags WHERE type='tree_water' AND status='resolved' AND resolved_at > ?", (cutoff,))}
        trees = features_in_aoi(conn, settings, ["tree"])
        for tree, need in select_priority_trees(trees, satellite, excluded):
            urgency = min(1 + (1 if need >= TREE_STRONG_NEED else 0) + (1 if cond["heat"] else 0), 3)
            reasons = [{"code": "dry_spell", "past7_mm": cond["metrics"].get("past7_mm"), "next3_mm": cond["metrics"].get("next3_mm")}]
            if cond["heat"]:
                reasons.append({"code": "heat_forecast", "max_c": cond["metrics"].get("max_apparent_next3_c")})
            add(type="tree_water", source="trigger", source_ref=tree["id"], feature_id=tree["id"], title="Tree needs water",
                lat=tree["lat"], lon=tree["lon"], urgency=urgency, priority=round(urgency + need, 3),
                context={"reasons": reasons, "satellite": _sat(satellite, tree["lat"], tree["lon"]), "need": need,
                         "feature": {"name": tree["name"], "species": tree["tags"].get("species") or tree["tags"].get("genus")}})

    # --- storm drains from OpenStreetMap: only when heavy rain is coming
    if cond["rain"]:
        for d in features_in_aoi(conn, settings, ["drain"]):
            u = _drain_urgency(cond)
            add(type="drain_clear", source="trigger", source_ref=d["id"], feature_id=d["id"], title="Storm drain to clear",
                lat=d["lat"], lon=d["lon"], urgency=u, priority=float(u),
                context={"reasons": [{"code": "rain_forecast", "next2_mm": cond["rain_mm"]}], "satellite": None, "feature": {"name": d["name"]}})

    # --- cooling spaces: heat, or hours not verified recently
    stale_cutoff = _iso(now - timedelta(days=rules.FLAG_TYPES["cooling_check"]["cooldown_days"]))
    for f in features_in_aoi(conn, settings, ["cooling_center", "community_space"]):
        stale = not f["last_verified_at"] or f["last_verified_at"] < stale_cutoff
        if not (cond["heat"] or stale):
            continue
        official = f["kind"] == "cooling_center"
        urgency = (3 if official else 2) if cond["heat"] else 1
        reasons = [{"code": "heat_forecast", "max_c": cond["metrics"].get("max_apparent_next3_c")}] if cond["heat"] else []
        if stale:
            reasons.append({"code": "hours_unverified", "last": f["last_verified_at"]})
        t = f["tags"]
        add(type="cooling_check", source="trigger", source_ref=f["id"], feature_id=f["id"], title=f"Verify cooling space: {f['name']}",
            lat=f["lat"], lon=f["lon"], urgency=urgency, priority=round(urgency + (0.3 if official else 0), 3),
            context={"reasons": reasons, "satellite": None,
                     "feature": {"name": f["name"], "official": official, "address": t.get("address"), "hours": t.get("hours") or t.get("opening_hours"),
                                 "phone": t.get("phone"), "url": t.get("url") or t.get("website"), "verified": f["info"], "last_verified_at": f["last_verified_at"]}})

    # --- real, open 311 requests from Baltimore
    city, _ = signals.load_signal(conn, "city311")
    for r in (city or {}).get("requests", []):
        ftype = r["flag_type"]
        base = {"drain_clear": 2, "flood_report": 2}.get(ftype, 2 if r["category"] in ("fallen_tree", "broken_branch") else 1)
        urgency = min(base + (1 if cond["rain"] and ftype in ("drain_clear", "flood_report") else 0), 3)
        reasons = [{"code": "city_request", "srtype": r["srtype"], "created": r["created"], "status": r["status"]}]
        if cond["rain"] and ftype in ("drain_clear", "flood_report"):
            reasons.append({"code": "rain_forecast", "next2_mm": cond["rain_mm"]})
        add(type=ftype, source="311", source_ref=str(r["ref"]), feature_id=None, title="", lat=r["lat"], lon=r["lon"], urgency=urgency,
            priority=float(urgency), context={"reasons": reasons, "satellite": _sat(satellite, r["lat"], r["lon"]),
                                              "city": {"category": r["category"], "srtype": r["srtype"], "status": r["status"], "created": r["created"],
                                                       "address": r["address"], "neighborhood": r["neighborhood"]}})
    return specs


# Create, update, or reopen the flag matching this spec, depending on its current status.
def _apply(conn, spec, now):
    row = conn.execute("SELECT * FROM flags WHERE type=? AND source=? AND source_ref=?",
                       (spec["type"], spec["source"], spec["source_ref"])).fetchone()
    ctx = json.dumps(spec["context"])
    stamp = _iso(now)
    # No matching flag yet, so create a new one.
    if row is None:
        conn.execute("INSERT INTO flags (type, source, source_ref, feature_id, title, lat, lon, urgency, priority, status, context, created_at, updated_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?, 'open', ?,?,?)",
                     (spec["type"], spec["source"], spec["source_ref"], spec["feature_id"], spec["title"], spec["lat"], spec["lon"],
                      spec["urgency"], spec["priority"], ctx, stamp, stamp))
        return "created"
    # Already active, so just refresh its details.
    if row["status"] in ("open", "pending"):
        conn.execute("UPDATE flags SET urgency=?, priority=?, context=?, title=?, updated_at=? WHERE id=?",
                     (spec["urgency"], spec["priority"], ctx, spec["title"], stamp, row["id"]))
        return "updated"
    # Recently resolved flags of this type stay closed for a cooldown period before reopening.
    if row["status"] == "resolved":
        days = rules.FLAG_TYPES[spec["type"]].get("cooldown_days", 7)
        if row["resolved_at"] and row["resolved_at"] > _iso(now - timedelta(days=days)):
            return "cooldown"
    # Otherwise, reopen the flag with fresh details.
    conn.execute("UPDATE flags SET status='open', urgency=?, priority=?, context=?, title=?, updated_at=?, resolved_at=NULL, "
                 "claimed_by=NULL, claim_expires_at=NULL, verified_count=0 WHERE id=?",
                 (spec["urgency"], spec["priority"], ctx, spec["title"], stamp, row["id"]))
    return "reopened"


# Main entry point: rebuild the current flag list from live conditions and clean up stale flags.
def sync_flags(conn, settings, satellite, now=None):
    now = now or datetime.now(timezone.utc)
    stamp = _iso(now)
    cond = signals.conditions(conn)
    specs = build_specs(conn, settings, satellite, cond, now)
    stats = {"created": 0, "updated": 0, "reopened": 0, "cooldown": 0, "expired": 0}
    for spec in specs.values():
        stats[_apply(conn, spec, now)] += 1

    # managed flags whose condition no longer holds go away, unless someone is actively working on them
    for r in conn.execute("SELECT id, type, source, source_ref, claimed_by, claim_expires_at FROM flags WHERE status='open' AND source IN ('trigger','311')").fetchall():
        if (r["type"], r["source"], r["source_ref"]) in specs:
            continue
        if r["claimed_by"] and r["claim_expires_at"] and r["claim_expires_at"] > stamp:
            continue
        conn.execute("UPDATE flags SET status='expired', updated_at=? WHERE id=?", (stamp, r["id"]))
        stats["expired"] += 1
    # Also expire flags that reached their own expiration time.
    stats["expired"] += conn.execute("UPDATE flags SET status='expired', updated_at=? WHERE status='open' AND expires_at IS NOT NULL AND expires_at < ?",
                                     (stamp, stamp)).rowcount
    # Clear out expired claims so other users can claim those flags.
    conn.execute("UPDATE flags SET claimed_by=NULL, claim_expires_at=NULL WHERE claim_expires_at IS NOT NULL AND claim_expires_at < ?", (stamp,))
    # Expire missions that have been sitting in progress for too long.
    conn.execute("UPDATE missions SET status='expired' WHERE status IN ('accepted','arrived') AND created_at < ?", (_iso(now - timedelta(hours=3)),))

    # keep the map light: drop the lowest-priority managed flags if the total gets out of hand
    over = conn.execute("SELECT COUNT(*) c FROM flags WHERE status IN ('open','pending')").fetchone()["c"] - MAX_ACTIVE_FLAGS
    if over > 0:
        conn.execute("UPDATE flags SET status='expired' WHERE id IN (SELECT id FROM flags WHERE status='open' AND claimed_by IS NULL "
                     "AND source IN ('trigger','311') ORDER BY priority ASC, id ASC LIMIT ?)", (over,))
    return {"conditions": {k: cond[k] for k in ("heat", "rain", "dry")}, **stats}
