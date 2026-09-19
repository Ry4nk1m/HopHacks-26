# User accounts, nicknames, streaks, points, badges, and leaderboards.

import re
from datetime import datetime, timedelta, timezone

from . import db, rules

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

NICK_RE = re.compile(r"^[\w .\-']+$", re.UNICODE)


# Raised for user-facing problems, with an error code and an HTTP status.
class UserError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


# Get today's date in the user's own timezone (falls back to UTC).
def local_day(now, tzname):
    try:
        tz = ZoneInfo(tzname) if ZoneInfo else timezone.utc
    except Exception:
        tz = timezone.utc
    return now.astimezone(tz).date()


# Get the start of today (midnight) in the user's timezone, converted to a UTC timestamp string.
def local_day_start_utc(now, tzname):
    from datetime import time
    try:
        tz = ZoneInfo(tzname) if ZoneInfo else timezone.utc
    except Exception:
        tz = timezone.utc
    start = datetime.combine(now.astimezone(tz).date(), time(0, 0), tzinfo=tz)
    return start.astimezone(timezone.utc).replace(microsecond=0).isoformat()


# Normalize a nickname (collapse whitespace) and check it against length, character, and letter rules.
def clean_nickname(raw):
    nick = re.sub(r"\s+", " ", (raw or "").strip())
    if not (rules.NICKNAME_MIN <= len(nick) <= rules.NICKNAME_MAX) or not NICK_RE.match(nick) or not re.search(r"[^\W\d_]", nick):
        raise UserError("bad_nickname", 422)
    return nick


# Create a new user row with a clean nickname, a valid language, and a fresh login token.
def create_user(conn, nickname, lang, now=None):
    nick = clean_nickname(nickname)
    lang = lang if lang in rules.LANGS else "en"
    if conn.execute("SELECT 1 FROM users WHERE nickname=?", (nick,)).fetchone():
        raise UserError("nickname_taken", 409)
    token = db.new_token()
    conn.execute("INSERT INTO users (nickname, token, lang, created_at) VALUES (?,?,?,?)",
                 (nick, token, lang, db.now_iso()))
    user = dict(conn.execute("SELECT * FROM users WHERE nickname=?", (nick,)).fetchone())
    return user


# Look up a user by their login token. Returns None if there is no token or no match.
def user_by_token(conn, token):
    if not token:
        return None
    r = conn.execute("SELECT * FROM users WHERE token=?", (token,)).fetchone()
    return dict(r) if r else None


# List the badge codes a user has earned, oldest first.
def badges_of(conn, user_id):
    return [r["code"] for r in conn.execute("SELECT code FROM badges WHERE user_id=? ORDER BY earned_at", (user_id,))]


# Build the version of a user record that is safe to send to clients (optionally with their token).
def public_user(conn, user, include_token=False):
    out = {k: user[k] for k in ("id", "nickname", "points", "streak", "best_streak", "lang")}
    out["badges"] = badges_of(conn, user["id"])
    out["weekly_points"] = weekly_points(conn, user["id"])
    if include_token:
        out["token"] = user["token"]
    return out


# Update a user's streak for today: continue it if active yesterday, reset to 1 otherwise, and track the best streak.
def touch_streak(conn, user_id, now, tzname):
    u = conn.execute("SELECT streak, best_streak, last_active_day FROM users WHERE id=?", (user_id,)).fetchone()
    today = local_day(now, tzname)
    last = u["last_active_day"]
    if last == today.isoformat():
        return u["streak"]
    streak = u["streak"] + 1 if last == (today - timedelta(days=1)).isoformat() else 1
    conn.execute("UPDATE users SET streak=?, best_streak=?, last_active_day=? WHERE id=?",
                 (streak, max(streak, u["best_streak"]), today.isoformat(), user_id))
    return streak


def current_streak(conn, user_id, now, tzname):
    """Streak as it stands today: a lapse of more than one day resets it to zero for display and scoring."""
    u = conn.execute("SELECT streak, last_active_day FROM users WHERE id=?", (user_id,)).fetchone()
    today = local_day(now, tzname)
    if u["last_active_day"] in (today.isoformat(), (today - timedelta(days=1)).isoformat()):
        return u["streak"]
    return 0


# Give a user points, logging the event. Does nothing if delta is zero or negative.
def award(conn, user_id, delta, reason, ref, now):
    if delta <= 0:
        return
    conn.execute("INSERT INTO point_events (user_id, delta, reason, ref, created_at) VALUES (?,?,?,?,?)",
                 (user_id, delta, reason, str(ref), now.replace(microsecond=0).isoformat()))
    conn.execute("UPDATE users SET points = points + ? WHERE id=?", (delta, user_id))


# Sum a user's points earned in the last 7 days.
def weekly_points(conn, user_id, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=7)).replace(microsecond=0).isoformat()
    return conn.execute("SELECT COALESCE(SUM(delta),0) s FROM point_events WHERE user_id=? AND created_at >= ?",
                        (user_id, cutoff)).fetchone()["s"]


def check_badges(conn, user_id, now):
    """Grant any newly earned badges; returns their codes."""
    earned = set(badges_of(conn, user_id))
    verified = conn.execute("SELECT COUNT(*) c FROM missions WHERE user_id=? AND status='verified'", (user_id,)).fetchone()["c"]
    by_type = {r["type"]: r["c"] for r in conn.execute(
        "SELECT f.type, COUNT(*) c FROM missions m JOIN flags f ON f.id=m.flag_id WHERE m.user_id=? AND m.status='verified' GROUP BY f.type", (user_id,))}
    reports = conn.execute("SELECT COUNT(*) c FROM flags WHERE reporter_id=? AND source='user'", (user_id,)).fetchone()["c"]
    streak = conn.execute("SELECT best_streak FROM users WHERE id=?", (user_id,)).fetchone()["best_streak"]
    new = []
    for code, cond in rules.BADGES.items():
        if code in earned:
            continue
        ok = False
        if "verified" in cond:
            ok = verified >= cond["verified"]
        elif "type" in cond:
            ok = by_type.get(cond["type"], 0) >= cond["count"]
        elif "types" in cond:
            ok = sum(by_type.get(t, 0) for t in cond["types"]) + reports >= cond["count"]
        elif "streak" in cond:
            ok = streak >= cond["streak"]
        if ok:
            conn.execute("INSERT INTO badges (user_id, code, earned_at) VALUES (?,?,?)", (user_id, code, now.replace(microsecond=0).isoformat()))
            new.append(code)
    return new


# Build the leaderboard: top users by points in the last 7 days, and top users by all-time points.
def leaderboard(conn, limit=10, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=7)).replace(microsecond=0).isoformat()
    weekly = [dict(r) for r in conn.execute(
        "SELECT u.id, u.nickname, SUM(p.delta) points FROM point_events p JOIN users u ON u.id=p.user_id "
        "WHERE p.created_at >= ? GROUP BY u.id ORDER BY points DESC, u.nickname LIMIT ?", (cutoff, limit))]
    total = [dict(r) for r in conn.execute("SELECT id, nickname, points FROM users WHERE points > 0 ORDER BY points DESC, nickname LIMIT ?", (limit,))]
    return {"weekly": weekly, "all_time": total}


# Build overall impact stats: verified missions by type, this week's count, volunteer count, and open flags.
def impact(conn, now=None):
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=7)).replace(microsecond=0).isoformat()
    by_type = {r["type"]: r["c"] for r in conn.execute(
        "SELECT f.type, COUNT(*) c FROM missions m JOIN flags f ON f.id=m.flag_id WHERE m.status='verified' GROUP BY f.type")}
    week = conn.execute("SELECT COUNT(*) c FROM missions WHERE status='verified' AND submitted_at >= ?", (cutoff,)).fetchone()["c"]
    return {"verified_total": sum(by_type.values()), "verified_week": week, "by_type": by_type,
            "volunteers": conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"],
            "open_flags": conn.execute("SELECT COUNT(*) c FROM flags WHERE status IN ('open','pending')").fetchone()["c"]}
