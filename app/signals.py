# Fetches weather, storm alerts, and city 311 requests, and turns them into simple
# heat/rain/dry conditions the rest of the app can use.

import json
from datetime import datetime, timedelta, timezone

import httpx

from . import db

# API endpoints for weather, storm alerts, and city 311 data.
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
NWS_ALERTS = "https://api.weather.gov/alerts/active"
CITY_311 = ("https://services1.arcgis.com/UWYHeuuJISiGmgXx/arcgis/rest/services/"
            "311_Customer_Service_Requests_current/FeatureServer/0/query")
UA = {"User-Agent": "neighborhood-missions/0.1 (hackathon project)", "Accept": "application/json"}

# Thresholds used to decide if it counts as a heat, rain, or dry day.
HEAT_APPARENT_C = 32.0      # about 90 F
RAIN_SOON_MM = 10.0
RAIN_SOON_MM_WITH_PROB = 6.0
RAIN_PROB_PCT = 70
DRY_PAST7_MM = 6.0
DRY_NEXT3_MM = 4.0
DRY_MIN_TEMP_C = 22.0

# 311 request type -> (flag type, category, max age in days, cap)
CITY_TYPES = {
    "WW-Storm Inlet Choke": ("drain_clear", "storm_inlet_choke", 30, 100),
    "WW-Storm Flooded Street": ("flood_report", "flooded_street", 5, 100),
    "WW-Storm Damaged Inlet": ("problem_report", "damaged_inlet", 30, 100),
    "HCD-Illegal Dumping": ("problem_report", "illegal_dumping", 30, 150),
    "SW-Dirty Street": ("problem_report", "litter", 21, 250),
    "FOR-Down Tree": ("problem_report", "fallen_tree", 30, 100),
    "FOR-Broken Branch in Tree": ("problem_report", "broken_branch", 30, 100),
    "FOR-Tree Maintenance": ("problem_report", "tree_issue", 30, 300),
}
# Conditions that can be manually forced on or off instead of decided automatically.
PAGE_311 = 1000
MAX_311_PAGES = 6
FORCE_KEYS = ("heat", "rain", "dry")


# Save a signal's value to the database, keyed by name.
def save_signal(conn, key, value):
    conn.execute("INSERT INTO signals (key, value, fetched_at) VALUES (?,?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value, fetched_at=excluded.fetched_at",
                 (key, json.dumps(value), db.now_iso()))


# Load a saved signal's value, along with when it was last fetched.
def load_signal(conn, key):
    r = conn.execute("SELECT value, fetched_at FROM signals WHERE key=?", (key,)).fetchone()
    return (json.loads(r["value"]), r["fetched_at"]) if r else (None, None)


# Drop missing values from a list and return the remaining values plus their max (or a default).
def _num(vals, default=0.0):
    vals = [v for v in vals if v is not None]
    return vals, (max(vals) if vals else default)


# Get rain and temperature forecasts from Open-Meteo for the area.
def fetch_weather(settings, client, now=None):
    lat, lon = settings.center
    r = client.get(OPEN_METEO, params={
        "latitude": round(lat, 4), "longitude": round(lon, 4), "past_days": 7, "forecast_days": 4, "timezone": "auto",
        "daily": "precipitation_sum,temperature_2m_max,apparent_temperature_max,precipitation_probability_max"}, headers=UA)
    r.raise_for_status()
    data = r.json()
    daily = data["daily"]
    times = daily["time"]
    now = now or datetime.now(timezone.utc)
    # Find which row in the forecast data corresponds to today, in the area's local time.
    local_today = (now + timedelta(seconds=data.get("utc_offset_seconds", 0))).date().isoformat()
    i = times.index(local_today) if local_today in times else min(7, len(times) - 1)

    # Sum a daily value over a range of days, treating missing days as zero.
    def total(key, a, b):
        return round(sum(v or 0 for v in daily[key][max(a, 0):b]), 1)

    _, prob = _num(daily["precipitation_probability_max"][i:i + 2])
    _, app_max = _num(daily["apparent_temperature_max"][i:i + 3], default=None)
    _, t_max = _num(daily["temperature_2m_max"][max(i - 3, 0):i + 3], default=None)
    return {"metrics": {
        "today": local_today, "past7_mm": total("precipitation_sum", i - 7, i), "next2_mm": total("precipitation_sum", i, i + 2),
        "next3_mm": total("precipitation_sum", i, i + 3), "max_prob_next2": prob,
        "max_apparent_next3_c": app_max, "max_temp_c": t_max}}


# Get active weather alerts from the National Weather Service for the area.
def fetch_nws(settings, client):
    lat, lon = settings.center
    r = client.get(NWS_ALERTS, params={"point": f"{lat:.4f},{lon:.4f}"}, headers=UA)
    r.raise_for_status()
    events = []
    for f in r.json().get("features", []):
        p = f.get("properties", {})
        events.append({"event": p.get("event", ""), "severity": p.get("severity"), "headline": p.get("headline"), "ends": p.get("ends")})
    return {"events": events}


# Get open 311 service requests from the city that match the report types we track.
def fetch_311(settings, client, now=None):
    w, s, e, n = settings.aoi
    types = ",".join("'" + t + "'" for t in CITY_TYPES)
    now = now or datetime.now(timezone.utc)
    oldest_wanted = now - timedelta(days=max(c[2] for c in CITY_TYPES.values()))
    # a whole city has thousands of open requests: read newest-first, a page at a time, until they are older than any type keeps
    feats = []
    for page in range(MAX_311_PAGES):
        r = client.get(CITY_311, params={
            "where": f"SRType IN ({types}) AND SRStatus IN ('New','Open')",
            "geometry": f"{w},{s},{e},{n}", "geometryType": "esriGeometryEnvelope", "inSR": 4326, "spatialRel": "esriSpatialRelIntersects",
            "outFields": "SRRecordID,ServiceRequestNum,SRType,SRStatus,CreatedDate,Address,Neighborhood,Latitude,Longitude",
            "returnGeometry": "false", "orderByFields": "CreatedDate DESC", "resultOffset": page * PAGE_311,
            "resultRecordCount": PAGE_311, "f": "json"}, headers=UA)
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(f"311 service error: {body['error'].get('message')}")
        got = body.get("features", [])
        feats.extend(got)
        try:
            last = datetime.fromtimestamp(got[-1]["attributes"]["CreatedDate"] / 1000, tz=timezone.utc)
        except (IndexError, KeyError, TypeError, ValueError):
            break
        if not body.get("exceededTransferLimit") or len(got) < PAGE_311 or last < oldest_wanted:
            break
    per_type, rows = {}, []
    for f in feats:
        a = f["attributes"]
        cfg = CITY_TYPES.get(a.get("SRType"))
        if not cfg:
            continue
        try:
            lat, lon = float(a["Latitude"]), float(a["Longitude"])
            created = datetime.fromtimestamp(a["CreatedDate"] / 1000, tz=timezone.utc)
        except (TypeError, ValueError, KeyError):
            continue
        # Skip requests that are too old, outside the area, or over the per-type cap.
        if now - created > timedelta(days=cfg[2]) or not (s <= lat <= n and w <= lon <= e):
            continue
        if per_type.get(a["SRType"], 0) >= cfg[3]:
            continue
        per_type[a["SRType"]] = per_type.get(a["SRType"], 0) + 1
        rows.append({"ref": a.get("ServiceRequestNum") or a.get("SRRecordID"), "srtype": a["SRType"], "flag_type": cfg[0],
                     "category": cfg[1], "status": a.get("SRStatus"), "created": created.isoformat(timespec="seconds"),
                     "address": (a.get("Address") or "").split(", Baltimore")[0], "neighborhood": a.get("Neighborhood"),
                     "lat": lat, "lon": lon})
    return {"requests": rows}


def refresh_signals(settings, db_path, client=None, now=None):
    """Fetch weather, alerts and 311 requests. A failed source keeps its previous data; returns per-source status."""
    own = client is None
    client = client or httpx.Client(timeout=30, follow_redirects=True)
    status = {}
    try:
        # Try each source on its own, so one failing does not stop the others from updating.
        for key, fn in (("weather", fetch_weather), ("nws", fetch_nws), ("city311", fetch_311)):
            try:
                value = fn(settings, client, now) if key != "nws" else fn(settings, client)
                with db.connect(db_path) as conn:
                    save_signal(conn, key, value)
                status[key] = "ok"
            except Exception as exc:
                status[key] = f"error: {str(exc)[:120]}"
    finally:
        if own:
            client.close()
    with db.connect(db_path) as conn:
        save_signal(conn, "status", status)
    return status


# Manually force a condition on, off, or back to automatic.
def set_forced(conn, name, mode):
    if name not in FORCE_KEYS or mode not in ("on", "off", "auto"):
        raise ValueError("bad trigger")
    forced, _ = load_signal(conn, "forced")
    forced = forced or {}
    forced[name] = mode
    save_signal(conn, "forced", forced)
    return forced


# Combine weather, alerts, and any manual overrides into today's heat/rain/dry conditions.
def conditions(conn):
    weather, weather_at = load_signal(conn, "weather")
    nws, _ = load_signal(conn, "nws")
    forced, _ = load_signal(conn, "forced")
    forced = forced or {}
    m = (weather or {}).get("metrics") or {}
    events = (nws or {}).get("events") or []
    # Check the active alerts for heat or flood warnings.
    heat_alert = any("heat" in e["event"].lower() for e in events)
    flood_alert = any("flood" in e["event"].lower() for e in events)
    # Decide each condition automatically from alerts and forecast numbers.
    app = m.get("max_apparent_next3_c")
    auto_heat = heat_alert or (app is not None and app >= HEAT_APPARENT_C)
    next2 = m.get("next2_mm") or 0
    auto_rain = flood_alert or next2 >= RAIN_SOON_MM or (next2 >= RAIN_SOON_MM_WITH_PROB and (m.get("max_prob_next2") or 0) >= RAIN_PROB_PCT)
    auto_dry = bool(m) and (m.get("past7_mm", 99) <= DRY_PAST7_MM and (m.get("next3_mm", 99) <= DRY_NEXT3_MM)
                            and (m.get("max_temp_c") or 0) >= DRY_MIN_TEMP_C)

    # Use the forced setting if one was set, otherwise fall back to the automatic result.
    def pick(name, auto):
        mode = forced.get(name, "auto")
        return True if mode == "on" else False if mode == "off" else auto

    rain = pick("rain", auto_rain)
    return {
        "heat": pick("heat", auto_heat), "rain": rain, "dry": pick("dry", auto_dry),
        "flood_alert": flood_alert or forced.get("rain") == "on",
        "rain_mm": max(next2, 15.0 if forced.get("rain") == "on" else 0.0),
        "auto": {"heat": auto_heat, "rain": auto_rain, "dry": auto_dry}, "forced": {k: forced.get(k, "auto") for k in FORCE_KEYS},
        "metrics": m, "events": events, "weather_at": weather_at,
    }
