import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import db, signals
from app.config import Settings
from app.main import create_app
from tests.helpers import Scripted, blurry_photo, photo_bytes

MILD = {"past7_mm": 20, "next2_mm": 0, "next3_mm": 2, "max_prob_next2": 0, "max_apparent_next3_c": 27, "max_temp_c": 25}
NOW_ISO = datetime.now(timezone.utc).isoformat(timespec="seconds")
ROWS = [
    {"ref": "26-DRAIN", "srtype": "WW-Storm Inlet Choke", "flag_type": "drain_clear", "category": "storm_inlet_choke", "status": "Open",
     "created": NOW_ISO, "address": "3100 CHARLES ST", "neighborhood": "Charles Village", "lat": 39.3300, "lon": -76.6150},
    {"ref": "26-FLOOD", "srtype": "WW-Storm Flooded Street", "flag_type": "flood_report", "category": "flooded_street", "status": "Open",
     "created": NOW_ISO, "address": "2900 ST PAUL ST", "neighborhood": "Charles Village", "lat": 39.3320, "lon": -76.6180},
    {"ref": "26-LITTER", "srtype": "SW-Dirty Street", "flag_type": "problem_report", "category": "litter", "status": "New",
     "created": NOW_ISO, "address": "500 E 33RD ST", "neighborhood": "Waverly", "lat": 39.3260, "lon": -76.6120},
]


@pytest.fixture
def env(tmp_path):
    settings = Settings(data_dir=tmp_path, external_fetch=False, background_jobs=False, dev_tools=True, admin_key="adm-key")
    path = tmp_path / "app.db"
    with db.connect(path) as c:
        signals.save_signal(c, "weather", {"metrics": MILD})
        signals.save_signal(c, "city311", {"requests": ROWS})
    validator = Scripted()
    with TestClient(create_app(settings, validator)) as client:
        yield SimpleNamespace(client=client, v=validator, settings=settings, path=path)


def signup(env, name="Maya", lang="en"):
    r = env.client.post("/api/users", json={"nickname": name, "lang": lang})
    assert r.status_code == 201, r.text
    return r.json()


def H(u):
    return {"Authorization": f"Bearer {u['token']}"}


def flag(env, ftype, source=None, user=None):
    fl = env.client.get("/api/flags", headers=H(user) if user else {}).json()["flags"]
    return next(f for f in fl if f["type"] == ftype and (source is None or f["source"] == source))


def accept(env, u, f):
    return env.client.post(f"/api/flags/{f['id']}/accept", headers=H(u))


def arrive(env, u, mid, lat, lon, acc=10, manual=False):
    return env.client.post(f"/api/missions/{mid}/arrive", headers=H(u), json={"lat": lat, "lon": lon, "accuracy": acc, "manual": manual})


def submit(env, u, mid, lat, lon, seed=1, acc=10, before=None, was_problem=None, lang="en", photo=None):
    files = {"photo": ("p.jpg", photo or photo_bytes(seed), "image/jpeg")}
    if before is not None:
        files["before"] = ("b.jpg", photo_bytes(before), "image/jpeg")
    data = {"lat": lat, "lon": lon, "accuracy": acc, "lang": lang}
    if was_problem:
        data["was_problem"] = was_problem
    return env.client.post(f"/api/missions/{mid}/submit", headers=H(u), files=files, data=data)


def start(env, u, f, arrive_now=True):
    mid = accept(env, u, f).json()["mission"]["id"]
    if arrive_now:
        assert arrive(env, u, mid, f["lat"], f["lon"]).json()["arrived"] is True
    return mid


def me(env, u):
    return env.client.get("/api/me", headers=H(u)).json()


# ------------------------------------------------------------------ accounts, config, layers
def test_signup_auth_and_profile(env):
    assert env.client.get("/api/me").status_code == 401 and env.client.get("/api/me").json() == {"code": "unauthorized"}
    assert env.client.get("/api/me", headers={"Authorization": "Bearer nope"}).status_code == 401
    u = signup(env, "Maya")
    assert u["points"] == 0 and u["badges"] == [] and len(u["token"]) > 20
    assert env.client.post("/api/users", json={"nickname": "maya"}).json() == {"code": "nickname_taken"}
    assert env.client.post("/api/users", json={"nickname": "<b>"}).json() == {"code": "bad_nickname"}
    assert me(env, u)["nickname"] == "Maya" and "token" not in me(env, u)
    assert env.client.patch("/api/me", headers=H(u), json={"lang": "es"}).status_code == 405  # no language switching any more


def test_signup_is_rate_limited(env):
    for i in range(20):
        assert env.client.post("/api/users", json={"nickname": f"User {i:02d}"}).status_code == 201
    assert env.client.post("/api/users", json={"nickname": "One too many"}).status_code == 429


def test_config_layers_and_health(env):
    cfg = env.client.get("/api/config").json()
    assert cfg["validator"] == "scripted" and cfg["dev_tools"] is True and len(cfg["aoi"]) == 4
    assert set(cfg["rules"]["flag_types"]) == {"tree_water", "drain_clear", "cooling_check", "flood_report", "problem_report"}
    assert cfg["layers"]["bounds"][0][0] < cfg["layers"]["bounds"][1][0] and cfg["layers"]["ndvi"]["scenes"]
    for name in ("ndvi", "lst"):
        r = env.client.get(f"/api/layers/{name}.png")
        assert r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n" and "max-age" in r.headers["cache-control"]
    assert env.client.get("/api/layers/other.png").status_code == 404
    assert env.client.get("/api/health").json() == {"ok": True}


def test_flags_listing_has_real_sources_and_user_specific_fields(env):
    listing = env.client.get("/api/flags").json()
    types = {(f["type"], f["source"]) for f in listing["flags"]}
    assert {("drain_clear", "311"), ("flood_report", "311"), ("problem_report", "311"), ("cooling_check", "trigger")} <= types
    assert listing["conditions"]["heat"] is False and set(listing["conditions"]["forced"]) == {"heat", "rain", "dry"}
    f = flag(env, "cooling_check")
    assert f["reward"] == 20 and f["radius_m"] == 60 and f["purpose"] == "complete" and f["context"]["feature"]["name"]
    assert env.client.get(f"/api/flags/{f['id']}").json()["id"] == f["id"] and env.client.get("/api/flags/999999").status_code == 404


# ------------------------------------------------------------------ the core mission loop
def test_complete_a_cooling_check_end_to_end(env):
    u = signup(env)
    f = flag(env, "cooling_check", user=u)
    mid = accept(env, u, f).json()["mission"]["id"]
    far = arrive(env, u, mid, f["lat"] + 0.01, f["lon"]).json()
    assert far["arrived"] is False and far["manual_allowed"] is False and far["distance_m"] > 1000
    assert arrive(env, u, mid, f["lat"] + 0.001, f["lon"], manual=False).json()["arrived"] is False   # ~110 m: no auto arrival
    manual = arrive(env, u, mid, f["lat"] + 0.001, f["lon"], manual=True).json()
    assert manual["arrived"] is True and manual["manual"] is True
    assert arrive(env, u, mid, f["lat"], f["lon"]).json()["manual"] is False
    env.v.set(hours_text="Mon-Fri 9am-5pm", accessible=True)
    r = submit(env, u, mid, f["lat"], f["lon"]).json()
    assert r["outcome"] == "verified" and r["points"] == 20 and r["streak"] == 1 and r["new_badges"] == ["first_mission"]
    assert r["flag"]["status"] == "resolved" and r["mission"]["status"] == "verified"
    assert env.v.calls[-1]["purpose"] == "complete" and env.v.calls[-1]["flag_type"] == "cooling_check" and env.v.calls[-1]["n_images"] == 1
    assert me(env, u)["points"] == 20 and me(env, u)["badges"] == ["first_mission"]
    assert f["id"] not in [x["id"] for x in env.client.get("/api/flags").json()["flags"]]
    with db.connect(env.path) as c:
        fid = c.execute("SELECT feature_id FROM flags WHERE id=?", (f["id"],)).fetchone()[0]
        feat = c.execute("SELECT last_verified_at, info FROM features WHERE id=?", (fid,)).fetchone()
        assert feat["last_verified_at"] and json.loads(feat["info"])["hours_text"] == "Mon-Fri 9am-5pm"


def test_before_photo_bonus_and_language_passthrough(env):
    u = signup(env)
    f = flag(env, "drain_clear", user=u)
    mid = start(env, u, f)
    r = submit(env, u, mid, f["lat"], f["lon"], seed=5, before=6, lang="es").json()
    assert r["outcome"] == "verified" and r["points"] == round((15 * 1.25) + 5)  # urgency 2 plus the before-photo bonus
    assert env.v.calls[-1]["n_images"] == 2 and env.v.calls[-1]["lang"] == "en"  # unknown languages fall back to English


@pytest.mark.parametrize("change,code", [
    ({"contains_people": True}, "privacy"), ({"contains_pii": True}, "privacy"), ({"photo_ok": False}, "bad_photo"),
    ({"subject_ok": False}, "wrong_subject"), ({"task_done": False}, "task_not_done"), ({"confidence": 0.2}, "low_confidence")])
def test_rejections_keep_the_mission_open_for_a_retry(env, change, code):
    u = signup(env)
    f = flag(env, "cooling_check", user=u)
    mid = start(env, u, f)
    env.v.set(**change)
    r = submit(env, u, mid, f["lat"], f["lon"]).json()
    assert r["outcome"] == "rejected" and r["code"] == code and r["retry"] is True and r["mission"]["remaining_attempts"] == 2
    assert me(env, u)["points"] == 0 and flag(env, "cooling_check", user=u)["id"] is not None
    env.v.set(contains_people=False, contains_pii=False, photo_ok=True, subject_ok=True, task_done=True, confidence=0.9)
    assert submit(env, u, mid, f["lat"], f["lon"], seed=2).json()["outcome"] == "verified"


def test_three_rejections_end_the_mission_and_release_the_flag(env):
    u = signup(env)
    f = flag(env, "cooling_check", user=u)
    mid = start(env, u, f)
    env.v.set(confidence=0.1)
    for seed in (1, 2, 3):
        r = submit(env, u, mid, f["lat"], f["lon"], seed=seed).json()
    assert r["retry"] is False and r["mission"]["status"] == "rejected"
    assert submit(env, u, mid, f["lat"], f["lon"], seed=4).status_code == 409
    other = signup(env, "Other")
    assert accept(env, other, f).status_code == 200  # the flag was released


def test_photo_problems_are_caught_before_the_model_is_called(env):
    u = signup(env)
    f = flag(env, "cooling_check", user=u)
    mid = start(env, u, f)
    n = len(env.v.calls)
    assert submit(env, u, mid, f["lat"], f["lon"], photo=blurry_photo()).json()["code"] == "too_blurry"
    assert submit(env, u, mid, f["lat"], f["lon"], photo=b"junk").json()["code"] == "bad_photo"
    assert len(env.v.calls) == n
    assert me(env, u)["active_missions"][0]["remaining_attempts"] == 3  # bad uploads don't burn attempts


def test_model_outage_does_not_cost_an_attempt(env):
    u = signup(env)
    f = flag(env, "cooling_check", user=u)
    mid = start(env, u, f)
    env.v.unavailable = True
    r = submit(env, u, mid, f["lat"], f["lon"]).json()
    assert r["outcome"] == "error" and r["code"] == "validator_unavailable"
    assert me(env, u)["active_missions"][0]["remaining_attempts"] == 3


def test_reused_photo_is_rejected_everywhere(env):
    u = signup(env)
    f1, f2 = flag(env, "cooling_check", user=u), flag(env, "drain_clear", user=u)
    m1 = start(env, u, f1)
    assert submit(env, u, m1, f1["lat"], f1["lon"], seed=9).json()["outcome"] == "verified"
    m2 = start(env, u, f2)
    r = submit(env, u, m2, f2["lat"], f2["lon"], seed=9).json()
    assert r["outcome"] == "rejected" and r["code"] == "duplicate_photo" and r["mission"]["remaining_attempts"] == 2


def test_submission_guards(env):
    u, other = signup(env), signup(env, "Other")
    f = flag(env, "cooling_check", user=u)
    mid = start(env, u, f, arrive_now=False)
    assert submit(env, u, mid, f["lat"], f["lon"]).json() == {"code": "no_arrival"}
    assert arrive(env, other, mid, f["lat"], f["lon"]).status_code == 404       # someone else's mission
    assert arrive(env, u, mid, f["lat"], f["lon"]).json()["arrived"]
    far = submit(env, u, mid, f["lat"] + 0.01, f["lon"])
    assert far.status_code == 409 and far.json()["code"] == "too_far"
    assert env.client.post(f"/api/missions/{mid}/submit", headers=H(u), data={"lat": 1, "lon": 1}).status_code == 422
    assert arrive(env, u, mid, "abc", 1).status_code == 422
    with db.connect(env.path) as c:
        c.execute("UPDATE missions SET arrived_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), mid))
    assert submit(env, u, mid, f["lat"], f["lon"]).json() == {"code": "arrival_expired"}
    assert arrive(env, u, mid, f["lat"], f["lon"]).json()["arrived"]
    assert submit(env, u, mid, f["lat"], f["lon"]).json()["outcome"] == "verified"
    assert submit(env, u, mid, f["lat"], f["lon"], seed=2).json() == {"code": "not_active"}


def test_manual_arrival_needs_higher_confidence(env):
    u = signup(env)
    f = flag(env, "cooling_check", user=u)
    mid = accept(env, u, f).json()["mission"]["id"]
    assert arrive(env, u, mid, f["lat"] + 0.001, f["lon"], manual=True).json()["manual"] is True
    env.v.set(confidence=0.75)
    assert submit(env, u, mid, f["lat"] + 0.001, f["lon"]).json()["outcome"] == "pending"   # fine for auto arrival, not for manual


# ------------------------------------------------------------------ review queue
def test_pending_review_queue(env):
    u = signup(env)
    f = flag(env, "cooling_check", user=u)
    mid = start(env, u, f)
    env.v.set(confidence=0.5)
    r = submit(env, u, mid, f["lat"], f["lon"]).json()
    assert r["outcome"] == "pending" and r["points"] == 0 and r["code"] == "pending_review"
    assert env.client.get(f"/api/flags/{f['id']}").json()["status"] == "pending"
    other = signup(env, "Other")
    assert accept(env, other, {"id": f["id"]}).status_code == 409  # pending flags cannot be accepted
    assert env.client.get("/api/admin/review").status_code == 403
    assert env.client.get("/api/admin/review", headers={"X-Admin-Key": "wrong"}).status_code == 403
    admin = {"X-Admin-Key": "adm-key"}
    pend = env.client.get("/api/admin/review", headers=admin).json()["pending"]
    assert [p["id"] for p in pend] == [mid] and pend[0]["nickname"] == "Maya"
    photo = env.client.get(f"/api/admin/photo/{mid}", headers=admin)
    assert photo.status_code == 200 and photo.headers["content-type"] == "image/jpeg" and env.client.get(f"/api/admin/photo/{mid}").status_code == 403
    assert env.client.get(f"/api/admin/photo/{mid}?which=before", headers=admin).status_code == 404
    assert env.client.post(f"/api/admin/review/{mid}", headers=admin, json={"approve": True}).json()["status"] == "verified"
    assert me(env, u)["points"] > 0 and env.client.post(f"/api/admin/review/{mid}", headers=admin, json={"approve": True}).status_code == 409


def test_admin_rejection_reopens_the_flag(env):
    u = signup(env)
    f = flag(env, "cooling_check", user=u)
    mid = start(env, u, f)
    env.v.set(confidence=0.5)
    submit(env, u, mid, f["lat"], f["lon"])
    admin = {"X-Admin-Key": "adm-key"}
    assert env.client.post(f"/api/admin/review/{mid}", headers=admin, json={"approve": False, "note": "blurry"}).json() == {"status": "rejected"}
    assert me(env, u)["points"] == 0
    assert accept(env, signup(env, "Other"), f).status_code == 200


def test_admin_disabled_without_a_configured_key(tmp_path):
    settings = Settings(data_dir=tmp_path, external_fetch=False, background_jobs=False, admin_key="")
    with TestClient(create_app(settings, Scripted())) as client:
        assert client.get("/api/admin/review", headers={"X-Admin-Key": ""}).status_code == 403


# ------------------------------------------------------------------ claims and limits
def test_claims_lockouts_and_caps(env):
    u, other = signup(env), signup(env, "Other")
    f = flag(env, "cooling_check", user=u)
    m = accept(env, u, f).json()["mission"]
    assert accept(env, u, f).json()["mission"]["id"] == m["id"]             # accepting twice is idempotent
    r = accept(env, other, f)
    assert r.status_code == 409 and r.json() == {"code": "claimed"}
    assert next(x for x in env.client.get("/api/flags", headers=H(other)).json()["flags"] if x["id"] == f["id"])["claimed"] is True
    assert next(x for x in env.client.get("/api/flags", headers=H(u)).json()["flags"] if x["id"] == f["id"])["claimed_by_me"] is True
    with db.connect(env.path) as c:
        c.execute("UPDATE flags SET claim_expires_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), f["id"]))
    assert accept(env, other, f).status_code == 200                          # expired claims can be taken
    cancelled = env.client.post(f"/api/missions/{m['id']}/cancel", headers=H(u)).json()["mission"]
    assert cancelled["status"] == "cancelled" and env.client.post(f"/api/missions/{m['id']}/cancel", headers=H(u)).status_code == 409


def test_max_active_missions_and_repeat_lockout(env):
    u = signup(env)
    cool = [x for x in env.client.get("/api/flags").json()["flags"] if x["type"] == "cooling_check"][:4]
    for x in cool[:3]:
        assert accept(env, u, x).status_code == 200
    r = accept(env, u, cool[3])
    assert r.status_code == 409 and r.json() == {"code": "too_many_missions"}
    f = cool[0]
    mid = [m for m in me(env, u)["active_missions"] if m["flag_id"] == f["id"]][0]["id"]
    arrive(env, u, mid, f["lat"], f["lon"])
    assert submit(env, u, mid, f["lat"], f["lon"], seed=21).json()["outcome"] == "verified"
    with db.connect(env.path) as c:
        c.execute("UPDATE flags SET status='open', resolved_at=NULL WHERE id=?", (f["id"],))
    assert accept(env, u, f).json() == {"code": "recently_done"}


def test_submissions_are_rate_limited(env):
    u = signup(env)
    f = flag(env, "cooling_check", user=u)
    mid = start(env, u, f)
    env.v.set(confidence=0.1)
    codes = [submit(env, u, mid, f["lat"], f["lon"], seed=i).status_code for i in range(1, 10)]
    assert codes.count(429) >= 1 and codes[0] == 200
    assert submit(env, u, mid, f["lat"], f["lon"], seed=99).json() == {"code": "rate_limited"}


def test_daily_point_cap_awards_nothing_past_the_limit(env):
    u = signup(env)
    with db.connect(env.path) as c:
        for i in range(12):
            c.execute("INSERT INTO point_events (user_id, delta, reason, ref, created_at) VALUES (?,1,'mission',?,?)", (1, str(i), datetime.now(timezone.utc).isoformat()))
    f = flag(env, "cooling_check", user=u)
    r = submit(env, u, start(env, u, f), f["lat"], f["lon"]).json()
    assert r["outcome"] == "verified" and r["points"] == 0


# ------------------------------------------------------------------ confirming reported problems
def test_confirm_mission_on_a_311_problem(env):
    u, w = signup(env), signup(env, "Walker")
    f = flag(env, "problem_report", "311", user=u)
    mid = start(env, u, f)
    r = submit(env, u, mid, f["lat"], f["lon"], was_problem="yes").json()
    assert r["outcome"] == "verified" and r["flag"]["verified_count"] == 1 and r["flag"]["status"] == "open"
    assert env.v.calls[-1]["purpose"] == "confirm" and env.v.calls[-1]["ctx"]["category"] == "litter"
    mid2 = start(env, w, f)
    env.v.set(problem_present=False)
    r = submit(env, w, mid2, f["lat"], f["lon"], seed=2).json()
    assert r["outcome"] == "verified" and r["flag"]["status"] == "resolved"      # someone saw it cleaned up
    with db.connect(env.path) as c:
        assert c.execute("SELECT was_problem FROM missions WHERE id=?", (mid,)).fetchone()[0] == "yes"


def test_unclear_confirmation_is_rejected(env):
    u = signup(env)
    f = flag(env, "flood_report", "311", user=u)
    mid = start(env, u, f)
    env.v.set(problem_present=None)
    assert submit(env, u, mid, f["lat"], f["lon"]).json()["code"] == "unclear"


# ------------------------------------------------------------------ user reports
HOME = (39.3305, -76.6205)


def report(env, u, seed=1, rtype="problem_report", lat=HOME[0], lon=HOME[1], acc=10, note="", photo=None):
    return env.client.post("/api/reports", headers=H(u), files={"photo": ("p.jpg", photo or photo_bytes(seed), "image/jpeg")},
                           data={"type": rtype, "lat": lat, "lon": lon, "accuracy": acc, "note": note, "lang": "en"})


def test_confident_report_becomes_a_confirmed_flag_and_earns_points(env):
    u = signup(env)
    env.v.set(category="flooded_road", severity=3, confidence=0.9)
    r = report(env, u, note="  water   over the curb ").json()
    assert r["outcome"] == "created" and r["type"] == "flood_report" and r["unconfirmed"] is False and r["points"] == 10
    f = env.client.get(f"/api/flags/{r['flag_id']}", headers=H(u)).json()
    assert f["urgency"] == 3 and f["mine"] is True and f["unconfirmed"] is False and f["context"]["report"]["note"] == "water over the curb"
    assert f["expires_at"] and me(env, u)["points"] == 10 and "eyes_on_street" not in me(env, u)["badges"]


def test_unsure_report_needs_a_second_person_to_confirm(env):
    u, w = signup(env), signup(env, "Walker")
    env.v.set(category="litter", confidence=0.6)
    r = report(env, u).json()
    assert r["outcome"] == "created" and r["unconfirmed"] is True and r["points"] == 0
    fid = r["flag_id"]
    assert env.client.get(f"/api/flags/{fid}", headers=H(w)).json()["unconfirmed"] is True
    body = lambda lat, lon: {"lat": lat, "lon": lon, "accuracy": 10}
    assert env.client.post(f"/api/flags/{fid}/confirm", headers=H(u), json=body(*HOME)).json() == {"code": "own_report"}
    far = env.client.post(f"/api/flags/{fid}/confirm", headers=H(w), json=body(HOME[0] + 0.01, HOME[1]))
    assert far.status_code == 409 and far.json()["code"] == "too_far"
    ok = env.client.post(f"/api/flags/{fid}/confirm", headers=H(w), json=body(*HOME)).json()
    assert ok["points"] == 3 and me(env, w)["points"] == 3 and me(env, u)["points"] == 10   # the reporter is paid once corroborated
    assert env.client.post(f"/api/flags/{fid}/confirm", headers=H(w), json=body(*HOME)).json() == {"code": "already_confirmed"}
    assert env.client.get(f"/api/flags/{fid}", headers=H(w)).json()["unconfirmed"] is False


def test_report_rejections_and_guards(env):
    u = signup(env)
    cases = [({"contains_people": True}, "privacy"), ({"photo_ok": False}, "bad_photo"), ({"subject_ok": False}, "wrong_subject"),
             ({"problem_present": False}, "no_problem_seen"), ({"problem_present": None}, "no_problem_seen"), ({"confidence": 0.2}, "low_confidence")]
    for i, (change, code) in enumerate(cases):
        env.v.set(contains_people=False, photo_ok=True, subject_ok=True, problem_present=True, confidence=0.9)
        env.v.set(**change)
        r = report(env, u, seed=30 + i).json()
        assert r["outcome"] == "rejected" and r["code"] == code and r["retry"] is True, (change, r)
    env.v.set(contains_people=False, photo_ok=True, subject_ok=True, problem_present=True, confidence=0.9)
    env.client.app.state.limiter.reset()
    assert report(env, u, lat=40.0).json() == {"code": "outside_area"}
    assert report(env, u, acc=400).json() == {"code": "weak_gps"}
    assert report(env, u, rtype="tree_water").json() == {"code": "bad_type"}
    assert report(env, u, photo=blurry_photo()).json()["code"] == "too_blurry"
    env.v.unavailable = True
    assert report(env, u, seed=50).json()["code"] == "validator_unavailable"
    env.v.unavailable = False
    assert env.client.post("/api/reports", files={"photo": ("p.jpg", photo_bytes(1), "image/jpeg")}, data={"type": "problem_report", "lat": 1, "lon": 1}).status_code == 401


def test_duplicate_report_and_reused_photo(env):
    u, w = signup(env), signup(env, "Walker")
    assert report(env, u, seed=1).json()["outcome"] == "created"
    dup = report(env, w, seed=2, lat=HOME[0] + 0.0001).json()      # about 11 m away
    assert dup["outcome"] == "duplicate" and dup["flag_id"]
    assert report(env, w, seed=1, lat=HOME[0] + 0.005).json()["code"] == "duplicate_photo"
    assert report(env, w, seed=3, lat=HOME[0] + 0.005).json()["outcome"] == "created"


def test_report_limits_and_three_reports_earn_a_badge(env):
    u = signup(env)
    env.v.set(category="litter", confidence=0.9)
    for i in range(3):
        assert report(env, u, seed=60 + i, lat=HOME[0] + 0.002 * i).json()["outcome"] == "created"
    assert "eyes_on_street" in me(env, u)["badges"]
    for i in range(3, 6):
        report(env, u, seed=60 + i, lat=HOME[0] + 0.002 * i)
    assert report(env, u, seed=90, lat=HOME[0] + 0.05).status_code == 429


# ------------------------------------------------------------------ dev tools and misc endpoints
def test_dev_tools_force_conditions_and_reset(env):
    u = signup(env)
    assert env.client.post("/api/dev/force", json={"name": "dry", "mode": "on"}).json()["sync"]["conditions"]["dry"] is True
    assert len([f for f in env.client.get("/api/flags").json()["flags"] if f["type"] == "tree_water"]) == 60
    env.client.post("/api/dev/force", json={"name": "heat", "mode": "on"})
    assert {f["urgency"] for f in env.client.get("/api/flags").json()["flags"] if f["type"] == "cooling_check"} == {2, 3}
    assert env.client.post("/api/dev/force", json={"name": "hail", "mode": "on"}).status_code == 422
    f = flag(env, "cooling_check", user=u)
    assert submit(env, u, start(env, u, f), f["lat"], f["lon"]).json()["outcome"] == "verified"
    st = env.client.get("/api/dev/state").json()
    assert st["conditions"]["forced"]["dry"] == "on" and st["validator"] == "scripted"
    env.client.post("/api/dev/reset")
    assert me(env, u)["points"] == 0 and me(env, u)["badges"] == []
    assert not [x for x in env.client.get("/api/flags").json()["flags"] if x["type"] == "tree_water"]
    assert env.client.post("/api/dev/refresh").status_code == 200


def test_dev_tools_are_hidden_when_disabled(tmp_path):
    settings = Settings(data_dir=tmp_path, external_fetch=False, background_jobs=False, dev_tools=False)
    with TestClient(create_app(settings, Scripted())) as client:
        assert client.get("/api/dev/state").status_code == 404
        assert client.post("/api/dev/reset").status_code == 404
        assert client.post("/api/dev/force", json={"name": "heat", "mode": "on"}).status_code == 404
        assert client.get("/api/config").json()["dev_tools"] is False


def test_leaderboard_and_impact(env):
    a, b = signup(env, "Ana"), signup(env, "Ben")
    for u, ftype, seed in ((a, "cooling_check", 1), (b, "drain_clear", 2)):
        f = flag(env, ftype, user=u)
        assert submit(env, u, start(env, u, f), f["lat"], f["lon"], seed=seed).json()["outcome"] == "verified"
    board = env.client.get("/api/leaderboard").json()
    assert [r["nickname"] for r in board["weekly"]] == ["Ana", "Ben"] and [r["points"] for r in board["weekly"]] == [20, 19]
    imp = env.client.get("/api/impact").json()
    assert imp["verified_total"] == 2 and imp["by_type"] == {"cooling_check": 1, "drain_clear": 1} and imp["volunteers"] == 2 and imp["open_flags"] > 0


def test_static_files_and_path_safety(env):
    assert env.client.get("/static/../main.py").status_code == 404
    assert env.client.get("/static/%2e%2e/config.py").status_code == 404
    assert env.client.get("/static/nope.js").status_code == 404
    assert env.client.get("/api/admin/photo/1?key=adm-key").status_code == 404
    with sqlite3.connect(env.path) as raw:   # a stored path that tries to escape the data directory must not be served
        raw.execute("INSERT INTO missions (flag_id, user_id, status, created_at, photo_path) VALUES (1, 1, 'pending', 'x', '../../../../etc/hosts')")
    assert env.client.get("/api/admin/photo/1?key=adm-key").status_code == 404
    assert env.client.get("/api/openapi.json").status_code == 200


def test_tts_falls_back_without_a_key(env):
    r = env.client.post("/api/tts", json={"text": "hello", "lang": "en"})
    assert r.status_code == 503 and r.json()["fallback"] == "browser"
