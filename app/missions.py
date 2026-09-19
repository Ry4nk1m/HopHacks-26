# Core mission logic: accepting flags, arriving, submitting photos, and admin review.
# A mission is a user's attempt to complete or confirm a flag (a reported issue on the map).

import json
import re
from datetime import datetime, timedelta, timezone

from . import db, features, photos, rules, users
from .flags import get_flag
from .geo import haversine_m, valid_coord
from .validation import ValidatorUnavailable, Verdict


# Error raised when a mission action is not allowed, with a code and http status.
class MissionError(Exception):
    def __init__(self, code, status=400, **extra):
        super().__init__(code)
        self.code, self.status, self.extra = code, status, extra


# Format a datetime as an ISO string with no microseconds, for storing in the database.
def _iso(now):
    return now.replace(microsecond=0).isoformat()


# Turn a mission database row into a plain dict for the API response.
def mission_to_dict(row):
    d = dict(row)
    return {
        "id": d["id"], "flag_id": d["flag_id"], "status": d["status"], "attempts": d["attempts"],
        "remaining_attempts": max(rules.MAX_ATTEMPTS - d["attempts"], 0), "created_at": d["created_at"],
        "arrived_at": d["arrived_at"], "distance_m": d["distance_m"], "manual_arrival": bool(d["manual_arrival"]),
        "points": d["points"], "verdict": json.loads(d["verdict"]) if d["verdict"] else None,
    }


# Load a mission by id and make sure it belongs to this user, or raise an error.
def _own_mission(conn, user_id, mission_id):
    m = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
    if m is None or m["user_id"] != user_id:
        raise MissionError("mission_not_found", 404)
    return dict(m)


# Turn a GPS accuracy value into extra allowed distance, capped at a max.
def _allowance(accuracy):
    try:
        return max(0.0, min(float(accuracy or 0), rules.ACCURACY_ALLOWANCE_CAP_M))
    except (TypeError, ValueError):
        return 0.0


# List a user's missions that are still in progress (accepted or arrived) recently.
def active_missions(conn, user_id, now):
    cutoff = _iso(now - timedelta(hours=3))
    return [mission_to_dict(r) for r in conn.execute(
        "SELECT * FROM missions WHERE user_id=? AND status IN ('accepted','arrived') AND created_at >= ? ORDER BY id DESC", (user_id, cutoff))]


# Let a user claim an open flag as a new mission, or resume one they already have.
def accept(conn, user, flag_id, now):
    db.write_lock(conn)
    flag = get_flag(conn, flag_id)
    if not flag or flag["status"] != "open":
        raise MissionError("flag_unavailable", 409)
    stamp = _iso(now)
    if flag["claimed_by"] and flag["claimed_by"] != user["id"] and flag["claim_expires_at"] and flag["claim_expires_at"] > stamp:
        raise MissionError("claimed", 409)
    cfg = rules.FLAG_TYPES[flag["type"]]
    if cfg["purpose"] == "confirm" and flag["reporter_id"] == user["id"]:
        raise MissionError("own_report", 403)
    claim_until = _iso(now + timedelta(minutes=rules.CLAIM_MINUTES))
    cutoff = _iso(now - timedelta(hours=3))
    existing = conn.execute("SELECT * FROM missions WHERE user_id=? AND flag_id=? AND status IN ('accepted','arrived') AND created_at >= ?",
                            (user["id"], flag_id, cutoff)).fetchone()
    if existing:
        conn.execute("UPDATE flags SET claimed_by=?, claim_expires_at=? WHERE id=?", (user["id"], claim_until, flag_id))
        return mission_to_dict(existing)
    if len(active_missions(conn, user["id"], now)) >= rules.MAX_ACTIVE_MISSIONS:
        raise MissionError("too_many_missions", 409)
    lock_cutoff = _iso(now - timedelta(hours=rules.REPEAT_LOCKOUT_HOURS))
    if conn.execute("SELECT 1 FROM missions WHERE user_id=? AND flag_id=? AND status IN ('verified','pending') AND submitted_at >= ?",
                    (user["id"], flag_id, lock_cutoff)).fetchone():
        raise MissionError("recently_done", 409)
    cur = conn.execute("INSERT INTO missions (flag_id, user_id, status, created_at) VALUES (?,?, 'accepted', ?)", (flag_id, user["id"], stamp))
    conn.execute("UPDATE flags SET claimed_by=?, claim_expires_at=? WHERE id=?", (user["id"], claim_until, flag_id))
    return mission_to_dict(conn.execute("SELECT * FROM missions WHERE id=?", (cur.lastrowid,)).fetchone())


# Cancel a mission the user has not finished yet, and free up the flag.
def cancel(conn, user, mission_id, now):
    m = _own_mission(conn, user["id"], mission_id)
    if m["status"] not in ("accepted", "arrived"):
        raise MissionError("not_active", 409)
    conn.execute("UPDATE missions SET status='cancelled' WHERE id=?", (mission_id,))
    conn.execute("UPDATE flags SET claimed_by=NULL, claim_expires_at=NULL WHERE id=? AND claimed_by=?", (m["flag_id"], user["id"]))
    return mission_to_dict(conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone())


# Mark a mission as arrived if the user's location is close enough to the flag.
# Allows a manual override for a slightly larger distance if flagged as manual.
def arrive(conn, user, mission_id, lat, lon, accuracy, manual, now):
    if not valid_coord(lat, lon):
        raise MissionError("bad_location", 422)
    lat, lon = float(lat), float(lon)
    m = _own_mission(conn, user["id"], mission_id)
    if m["status"] not in ("accepted", "arrived"):
        raise MissionError("not_active", 409)
    flag = get_flag(conn, m["flag_id"])
    dist = haversine_m(lat, lon, flag["lat"], flag["lon"])
    radius = rules.FLAG_TYPES[flag["type"]]["radius_m"] + _allowance(accuracy)
    if dist <= radius:
        is_manual = False
    elif manual and dist <= rules.MANUAL_ARRIVAL_MAX_M:
        is_manual = True
    else:
        return {"arrived": False, "distance_m": round(dist), "radius_m": round(radius), "manual_allowed": dist <= rules.MANUAL_ARRIVAL_MAX_M}
    conn.execute("UPDATE missions SET status='arrived', arrived_at=?, lat=?, lon=?, accuracy=?, distance_m=?, manual_arrival=? WHERE id=?",
                 (_iso(now), lat, lon, accuracy, round(dist, 1), int(is_manual), mission_id))
    conn.execute("UPDATE flags SET claimed_by=?, claim_expires_at=? WHERE id=?", (user["id"], _iso(now + timedelta(minutes=30)), flag["id"]))
    return {"arrived": True, "manual": is_manual, "distance_m": round(dist), "radius_m": round(radius)}


# ---------------------------------------------------------------- submission

def decide(verdict, purpose, manual_arrival):
    """Turn the model's verdict into (outcome, code). Outcomes: verified, pending, rejected."""
    if verdict.contains_people or verdict.contains_pii:
        return "rejected", "privacy"
    if not verdict.photo_ok:
        return "rejected", "bad_photo"
    if not verdict.subject_ok:
        return "rejected", "wrong_subject"
    if purpose == "complete" and not verdict.task_done:
        return "rejected", "task_not_done"
    if purpose == "confirm" and verdict.problem_present is None:
        return "rejected", "unclear"
    needed = rules.VERIFIED_MIN + (0.10 if manual_arrival else 0.0)
    if verdict.confidence >= needed:
        return "verified", "verified"
    if verdict.confidence >= rules.REVIEW_MIN:
        return "pending", "pending_review"
    return "rejected", "low_confidence"


# Squash extra whitespace and cut a volunteer's note down to 200 characters.
def clean_note(note):
    return re.sub(r"\s+", " ", (note or "")).strip()[:200]


def _finished_by_someone_else(flag, mission):
    """A task you complete was resolved by another volunteer after you accepted it, so there is nothing left to credit."""
    if rules.FLAG_TYPES[flag["type"]]["purpose"] != "complete":
        return False
    return flag["status"] == "resolved" and bool(flag["resolved_at"]) and flag["resolved_at"] >= mission["created_at"]


def _too_late(conn, mission_id, flag, user_id):
    conn.execute("UPDATE missions SET status='cancelled' WHERE id=?", (mission_id,))
    conn.execute("UPDATE flags SET claimed_by=NULL, claim_expires_at=NULL WHERE id=? AND claimed_by=?", (flag["id"], user_id))
    return _result("rejected", "already_done", retry=False)


# Build the standard response dict returned from a photo submission.
def _result(outcome, code, verdict=None, mission=None, flag=None, **extra):
    return {"outcome": outcome, "code": code, "retry": outcome == "rejected" and (mission is None or mission.get("remaining_attempts", 0) > 0),
            "reasoning": verdict.reasoning if verdict else "", "verdict": verdict.model_dump() if verdict else None,
            "mission": mission, "flag": flag, **extra}


# Handle a photo submission for a mission: check it, run it past the validator, then save the result.
def submit(settings, validator, user_id, mission_id, photo_bytes, before_bytes, lat, lon, accuracy, was_problem, lang, now=None, note=None):
    now = now or datetime.now(timezone.utc)
    db_path = settings.data_dir / "app.db"
    lang = lang if lang in rules.LANGS else "en"
    if not valid_coord(lat, lon):
        raise MissionError("bad_location", 422)
    lat, lon = float(lat), float(lon)

    try:
        photo = photos.process_upload(photo_bytes)
        before = photos.process_upload(before_bytes) if before_bytes else None
    except photos.PhotoError as exc:
        return _result("rejected", exc.code)

    # phase 1: everything that can be decided without the model
    with db.connect(db_path) as conn:
        m = _own_mission(conn, user_id, mission_id)
        if m["status"] not in ("accepted", "arrived"):
            raise MissionError("not_active", 409)
        if m["attempts"] >= rules.MAX_ATTEMPTS:
            raise MissionError("attempts_exhausted", 409)
        if m["status"] != "arrived" or not m["arrived_at"]:
            raise MissionError("no_arrival", 409)
        if db.parse_iso(m["arrived_at"]) < now - timedelta(minutes=rules.ARRIVAL_VALID_MINUTES):
            raise MissionError("arrival_expired", 409)
        flag = get_flag(conn, m["flag_id"])
        if _finished_by_someone_else(flag, m):
            return _too_late(conn, mission_id, flag, user_id)
        cfg = rules.FLAG_TYPES[flag["type"]]
        radius = cfg["radius_m"] + _allowance(accuracy)
        if m["manual_arrival"]:
            radius = max(radius, rules.MANUAL_ARRIVAL_MAX_M)
        dist = haversine_m(lat, lon, flag["lat"], flag["lon"])
        if dist > radius:
            raise MissionError("too_far", 409, distance_m=round(dist), radius_m=round(radius))
        if photos.find_duplicate(conn, photo, flag["id"], user_id) or (before and photos.find_duplicate(conn, before, flag["id"], user_id)):
            conn.execute("UPDATE missions SET attempts = attempts + 1 WHERE id=?", (mission_id,))
            row = _own_mission(conn, user_id, mission_id)
            if row["attempts"] >= rules.MAX_ATTEMPTS:
                conn.execute("UPDATE missions SET status='rejected' WHERE id=?", (mission_id,))
                conn.execute("UPDATE flags SET claimed_by=NULL, claim_expires_at=NULL WHERE id=? AND claimed_by=?", (flag["id"], user_id))
                row = _own_mission(conn, user_id, mission_id)
            return _result("rejected", "duplicate_photo", mission=mission_to_dict(row))
        purpose = cfg["purpose"]
        vctx = json.loads(flag["context"] or "{}")
        vctx = {"category": (vctx.get("city") or {}).get("category") or (vctx.get("report") or {}).get("category"), "note": clean_note(note)}
        flag_type, manual = flag["type"], bool(m["manual_arrival"])

    # phase 2: the model (no database connection held while we wait)
    images = ([before.jpeg] if before else []) + [photo.jpeg]
    try:
        verdict = validator.check(purpose, flag_type, images, vctx, lang)
    except ValidatorUnavailable:
        return _result("error", "validator_unavailable")

    # phase 3: record the outcome
    with db.connect(db_path) as conn:
        db.write_lock(conn)
        m = _own_mission(conn, user_id, mission_id)
        if m["status"] not in ("accepted", "arrived"):
            raise MissionError("not_active", 409)
        flag = get_flag(conn, m["flag_id"])
        if _finished_by_someone_else(flag, m):
            return _too_late(conn, mission_id, flag, user_id)
        user = dict(conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone())
        outcome, code = decide(verdict, purpose, manual)
        recheck = None
        if outcome == "verified" and flag["type"] == "cooling_check" and verdict.usable is False:
            code = "verified_unusable"  # a real finding: the space is out of commission right now
            recheck = rules.unusable_recheck_days(verdict.reopen_days)
        stamp = _iso(now)
        attempts = m["attempts"] + 1
        conn.execute("UPDATE missions SET attempts=?, verdict=?, submitted_at=?, was_problem=?, lat=?, lon=?, accuracy=?, note=? WHERE id=?",
                     (attempts, json.dumps(verdict.model_dump()), stamp, was_problem if was_problem in ("yes", "no", "unsure") else None,
                      lat, lon, accuracy, vctx["note"] or None, mission_id))
        if outcome == "rejected":
            if attempts >= rules.MAX_ATTEMPTS:
                conn.execute("UPDATE missions SET status='rejected' WHERE id=?", (mission_id,))
                conn.execute("UPDATE flags SET claimed_by=NULL, claim_expires_at=NULL WHERE id=? AND claimed_by=?", (flag["id"], user_id))
            mission = mission_to_dict(conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone())
            return _result("rejected", code, verdict, mission, None)

        rel = photos.save_photo(settings, photo.jpeg)
        before_rel = photos.save_photo(settings, before.jpeg) if before else None
        photos.remember_photo(conn, photo, user_id, flag["id"], stamp)
        if before:
            photos.remember_photo(conn, before, user_id, flag["id"], stamp)
        conn.execute("UPDATE missions SET photo_path=?, before_path=?, photo_hash=?, photo_ahash=? WHERE id=?",
                     (rel, before_rel, photo.sha256, photo.ahash, mission_id))
        streak = users.touch_streak(conn, user_id, now, settings.timezone)
        if outcome == "pending":
            conn.execute("UPDATE missions SET status='pending' WHERE id=?", (mission_id,))
            conn.execute("UPDATE flags SET status='pending', claimed_by=NULL, claim_expires_at=NULL WHERE id=?", (flag["id"],))
            mission = mission_to_dict(conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone())
            return _result("pending", code, verdict, mission, None, streak=streak, points=0, new_badges=[])
        points, new_badges = _finalize_verified(conn, settings, mission_id, flag, user, verdict, purpose, now, streak, bool(before))
        mission = mission_to_dict(conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone())
        fresh = get_flag(conn, flag["id"])
        return _result("verified", code, verdict, mission, {"id": fresh["id"], "status": fresh["status"], "verified_count": fresh["verified_count"]},
                       streak=streak, points=points, new_badges=new_badges, recheck_days=recheck)


def _finalize_verified(conn, settings, mission_id, flag, user, verdict, purpose, now, streak, has_before):
    """Apply a verified mission: points, flag state, feature info, badges. Shared with admin approval."""
    stamp = _iso(now)
    day_start = users.local_day_start_utc(now, settings.timezone)
    done_today = conn.execute("SELECT COUNT(*) c FROM point_events WHERE user_id=? AND reason='mission' AND created_at >= ?",
                              (user["id"], day_start)).fetchone()["c"]
    points = 0 if done_today >= rules.DAILY_POINT_MISSIONS else rules.mission_points(flag["type"], flag["urgency"], streak, has_before)
    conn.execute("UPDATE missions SET status='verified', points=? WHERE id=?", (points, mission_id))
    users.award(conn, user["id"], points, "mission", mission_id, now)

    if purpose == "complete":
        conn.execute("UPDATE flags SET status='resolved', resolved_at=?, claimed_by=NULL, claim_expires_at=NULL, updated_at=? WHERE id=?",
                     (stamp, stamp, flag["id"]))
        if flag["type"] == "cooling_check" and flag["feature_id"]:
            status = "unusable" if verdict.usable is False else "usable" if verdict.usable is True else "unknown"
            features.mark_verified(conn, flag["feature_id"], {"hours_text": verdict.hours_text, "accessible": verdict.accessible, "observed_at": stamp,
                                                              "status": status, "reason": verdict.unusable_reason if status == "unusable" else None,
                                                              "recheck_days": rules.unusable_recheck_days(verdict.reopen_days) if status == "unusable" else None}, stamp)
    elif verdict.problem_present:
        conn.execute("UPDATE flags SET status='open', verified_count = verified_count + 1, claimed_by=NULL, claim_expires_at=NULL, updated_at=? WHERE id=?",
                     (stamp, flag["id"]))
        conn.execute("INSERT OR IGNORE INTO confirmations (flag_id, user_id, created_at) VALUES (?,?,?)", (flag["id"], user["id"], stamp))
        award_reporter_if_due(conn, flag["id"], now)
    else:
        conn.execute("UPDATE flags SET status='resolved', resolved_at=?, claimed_by=NULL, claim_expires_at=NULL, updated_at=? WHERE id=?",
                     (stamp, stamp, flag["id"]))
    return points, users.check_badges(conn, user["id"], now)


def award_reporter_if_due(conn, flag_id, now):
    """A user's report earns points once it is corroborated (auto-confirmed by the model or confirmed by someone else)."""
    flag = get_flag(conn, flag_id)
    if not flag or flag["source"] != "user" or not flag["reporter_id"]:
        return 0
    ctx = json.loads(flag["context"] or "{}")
    rep = ctx.setdefault("report", {})
    if rep.get("awarded") or flag["verified_count"] < 1:
        return 0
    rep["awarded"] = True
    conn.execute("UPDATE flags SET context=? WHERE id=?", (json.dumps(ctx), flag_id))
    users.award(conn, flag["reporter_id"], rules.REPORT_POINTS, "report", flag_id, now)
    users.check_badges(conn, flag["reporter_id"], now)
    return rules.REPORT_POINTS


# ---------------------------------------------------------------- admin review queue

# List all missions waiting for an admin to review them.
def pending_reviews(conn):
    out = []
    for r in conn.execute("SELECT m.*, f.type AS flag_type, f.title, u.nickname FROM missions m JOIN flags f ON f.id=m.flag_id "
                          "JOIN users u ON u.id=m.user_id WHERE m.status='pending' ORDER BY m.id"):
        d = dict(r)
        out.append({"id": d["id"], "flag_id": d["flag_id"], "flag_type": d["flag_type"], "nickname": d["nickname"],
                    "submitted_at": d["submitted_at"], "verdict": json.loads(d["verdict"]) if d["verdict"] else None,
                    "has_before": bool(d["before_path"]), "note": d["note"], "distance_m": d["distance_m"], "manual_arrival": bool(d["manual_arrival"])})
    return out


# Apply an admin's approve/reject decision on a pending mission.
def review_decide(conn, settings, mission_id, approve, note, now):
    m = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
    if m is None or m["status"] != "pending":
        raise MissionError("not_pending", 409)
    flag = get_flag(conn, m["flag_id"])
    user = dict(conn.execute("SELECT * FROM users WHERE id=?", (m["user_id"],)).fetchone())
    stamp = _iso(now)
    conn.execute("UPDATE missions SET review_note=? WHERE id=?", ((note or "")[:200], mission_id))
    if not approve:
        conn.execute("UPDATE missions SET status='rejected' WHERE id=?", (mission_id,))
        conn.execute("UPDATE flags SET status='open', updated_at=? WHERE id=?", (stamp, flag["id"]))
        return {"status": "rejected"}
    if _finished_by_someone_else(flag, m):
        conn.execute("UPDATE missions SET status='cancelled' WHERE id=?", (mission_id,))
        return {"status": "rejected", "code": "already_done"}
    verdict = Verdict.model_validate(json.loads(m["verdict"]))
    purpose = rules.FLAG_TYPES[flag["type"]]["purpose"]
    conn.execute("UPDATE flags SET status='open' WHERE id=?", (flag["id"],))
    streak = users.current_streak(conn, user["id"], now, settings.timezone) or 1
    points, badges = _finalize_verified(conn, settings, mission_id, flag, user, verdict, purpose, now, streak, bool(m["before_path"]))
    return {"status": "verified", "points": points, "new_badges": badges}
