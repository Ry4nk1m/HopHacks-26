# Builds the flag data sent to the app, and simple lookups for flags.

import json

from . import rules
from .users import current_streak


# Turn a flag row from the database into the dict shape the app expects.
# Adds fields like whether it is claimed, owned by this user, or unconfirmed.
def flag_to_dict(conn, row, user, now, tzname):
    d = dict(row)
    stamp = now.replace(microsecond=0).isoformat()
    # A claim only counts as active if it has not expired yet.
    active_claim = bool(d["claimed_by"] and d["claim_expires_at"] and d["claim_expires_at"] > stamp)
    uid = user["id"] if user else None
    cfg = rules.FLAG_TYPES[d["type"]]
    streak = current_streak(conn, uid, now, tzname) if uid else 0
    return {
        "id": d["id"], "type": d["type"], "title": d["title"], "lat": d["lat"], "lon": d["lon"], "urgency": d["urgency"],
        "priority": d["priority"], "status": d["status"], "source": d["source"], "created_at": d["created_at"],
        "expires_at": d["expires_at"], "verified_count": d["verified_count"], "context": json.loads(d["context"] or "{}"),
        "claimed": active_claim and d["claimed_by"] != uid, "claimed_by_me": active_claim and d["claimed_by"] == uid,
        "mine": bool(uid and d["reporter_id"] == uid), "unconfirmed": d["source"] == "user" and d["verified_count"] == 0 and d["status"] == "open",
        "reward": rules.mission_points(d["type"], d["urgency"], streak + 1 if streak else 1, False),
        "purpose": cfg["purpose"], "radius_m": cfg["radius_m"],
    }


# Get all open or pending flags, highest priority first.
def list_flags(conn, user, now, tzname):
    rows = conn.execute("SELECT * FROM flags WHERE status IN ('open','pending') ORDER BY priority DESC, id DESC").fetchall()
    return [flag_to_dict(conn, r, user, now, tzname) for r in rows]


# Get a single flag by id, or None if it does not exist.
def get_flag(conn, flag_id):
    r = conn.execute("SELECT * FROM flags WHERE id=?", (flag_id,)).fetchone()
    return dict(r) if r else None
