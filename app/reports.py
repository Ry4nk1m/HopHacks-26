# Handles user-submitted reports (flooding, problems, etc): checks the photo and location,
# runs the AI validator, then creates a flag. Also handles confirming someone else's report.

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from . import db, photos, rules, users
from .flags import get_flag
from .geo import haversine_m, in_bbox, valid_coord
from .missions import MissionError, _allowance, award_reporter_if_due, clean_note as _clean_note
from .validation import ValidatorUnavailable

# Display titles shown for each auto-detected report type.
TITLES = {"flood_report": "Reported flooding", "problem_report": "Reported problem"}


# Handle a new report submission: validate the input, check the photo with the AI validator,
# and create a flag if everything checks out.
def create_report(settings, validator, user_id, photo_bytes, claimed_type, note, lat, lon, accuracy, lang, now=None):
    now = now or datetime.now(timezone.utc)
    db_path = settings.data_dir / "app.db"
    if claimed_type not in rules.REPORT_TYPES:
        raise MissionError("bad_type", 422)
    if not valid_coord(lat, lon):
        raise MissionError("bad_location", 422)
    lat, lon = float(lat), float(lon)
    if not in_bbox(lat, lon, settings.aoi):
        raise MissionError("outside_area", 422)
    if accuracy is not None and float(accuracy) > 150:
        raise MissionError("weak_gps", 422)
    lang = lang if lang in rules.LANGS else "en"
    note = _clean_note(note)
    # Decode and validate the uploaded photo file itself.
    try:
        photo = photos.process_upload(photo_bytes)
    except photos.PhotoError as exc:
        return {"outcome": "rejected", "code": exc.code, "retry": True}

    # Reject if a very similar report already exists nearby, or the same photo was used before.
    with db.connect(db_path) as conn:
        for r in conn.execute("SELECT id, lat, lon FROM flags WHERE type IN (?, ?) AND status IN ('open','pending')", rules.REPORT_TYPES):
            if haversine_m(lat, lon, r["lat"], r["lon"]) <= rules.REPORT_DEDUPE_M:
                return {"outcome": "duplicate", "code": "duplicate_report", "flag_id": r["id"], "retry": False}
        if photos.find_duplicate(conn, photo, None, user_id):
            return {"outcome": "rejected", "code": "duplicate_photo", "retry": True}

    # Ask the AI validator to look at the photo and decide if it matches the claim.
    try:
        verdict = validator.check("report", claimed_type, [photo.jpeg], {"claimed_type": claimed_type, "note": note}, lang)
    except ValidatorUnavailable:
        return {"outcome": "error", "code": "validator_unavailable", "retry": True}

    # Reject the report if the validator finds any problem with the photo or subject.
    base = {"reasoning": verdict.reasoning, "verdict": verdict.model_dump()}
    if verdict.contains_people or verdict.contains_pii:
        return {"outcome": "rejected", "code": "privacy", "retry": True, **base}
    if not verdict.photo_ok:
        return {"outcome": "rejected", "code": "bad_photo", "retry": True, **base}
    if not verdict.subject_ok:
        return {"outcome": "rejected", "code": "wrong_subject", "retry": True, **base}
    if verdict.problem_present is not True:
        return {"outcome": "rejected", "code": "no_problem_seen", "retry": True, **base}
    if verdict.confidence < rules.REVIEW_MIN:
        return {"outcome": "rejected", "code": "low_confidence", "retry": True, **base}

    # Work out the final report type and urgency from what the validator saw.
    ftype = "flood_report" if verdict.category == "flooded_road" else ("problem_report" if verdict.category not in (None, "none") else claimed_type)
    urgency = max(verdict.severity or 1, 2 if ftype == "flood_report" else 1)
    # High confidence reports are trusted right away instead of waiting for someone to confirm.
    auto = verdict.confidence >= rules.REPORT_AUTO_CONFIRM_MIN
    stamp = now.replace(microsecond=0).isoformat()
    expires = (now + timedelta(hours=rules.FLAG_TYPES[ftype]["expire_hours"])).replace(microsecond=0).isoformat()
    context = {"reasons": [{"code": "user_report"}], "satellite": None,
               "report": {"category": verdict.category, "note": note, "severity": verdict.severity, "confidence": verdict.confidence,
                          "reasoning": verdict.reasoning, "awarded": False}}
    # Save the photo and the new flag, then update the reporter's streak, points, and badges.
    with db.connect(db_path) as conn:
        rel = photos.save_photo(settings, photo.jpeg)
        cur = conn.execute(
            "INSERT INTO flags (type, source, source_ref, feature_id, title, lat, lon, urgency, priority, status, context, created_at, updated_at, "
            "expires_at, reporter_id, verified_count, photo_path) VALUES (?, 'user', ?, NULL, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?)",
            (ftype, uuid.uuid4().hex, TITLES[ftype], lat, lon, urgency, float(urgency), json.dumps(context), stamp, stamp, expires,
             user_id, 1 if auto else 0, rel))
        flag_id = cur.lastrowid
        photos.remember_photo(conn, photo, user_id, flag_id, stamp)
        streak = users.touch_streak(conn, user_id, now, settings.timezone)
        points = award_reporter_if_due(conn, flag_id, now) if auto else 0
        new_badges = users.check_badges(conn, user_id, now)
        flag = get_flag(conn, flag_id)
    return {"outcome": "created", "code": "report_created", "retry": False, "flag_id": flag_id, "type": ftype, "unconfirmed": not auto,
            "points": points, "streak": streak, "new_badges": new_badges, "flag": {"id": flag["id"], "status": flag["status"]}, **base}


def confirm_flag(conn, user, flag_id, lat, lon, accuracy, now, settings):
    db.write_lock(conn)
    """Tap-to-confirm for someone else's unconfirmed report: you must be standing near it."""
    if not valid_coord(lat, lon):
        raise MissionError("bad_location", 422)
    flag = get_flag(conn, flag_id)
    # Make sure the flag is still open, is a user report, and isn't already confirmed or your own.
    if not flag or flag["status"] != "open" or flag["source"] != "user":
        raise MissionError("flag_unavailable", 409)
    if flag["reporter_id"] == user["id"]:
        raise MissionError("own_report", 403)
    if flag["verified_count"] >= 1:
        raise MissionError("already_confirmed", 409)
    # The confirmer must actually be near the reported spot, allowing for GPS accuracy.
    dist = haversine_m(float(lat), float(lon), flag["lat"], flag["lon"])
    radius = rules.CONFIRM_RADIUS_M + _allowance(accuracy)
    if dist > radius:
        raise MissionError("too_far", 409, distance_m=round(dist), radius_m=round(radius))
    stamp = now.replace(microsecond=0).isoformat()
    if conn.execute("SELECT 1 FROM confirmations WHERE flag_id=? AND user_id=?", (flag_id, user["id"])).fetchone():
        raise MissionError("already_confirmed", 409)
    conn.execute("INSERT INTO confirmations (flag_id, user_id, created_at) VALUES (?,?,?)", (flag_id, user["id"], stamp))
    conn.execute("UPDATE flags SET verified_count = verified_count + 1, updated_at=? WHERE id=?", (stamp, flag_id))
    streak = users.touch_streak(conn, user["id"], now, settings.timezone)
    users.award(conn, user["id"], rules.CONFIRM_POINTS, "confirm", flag_id, now)
    award_reporter_if_due(conn, flag_id, now)
    return {"points": rules.CONFIRM_POINTS, "streak": streak, "new_badges": users.check_badges(conn, user["id"], now)}
