import json
import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import numpy as np
import pytest

from app import db, features, photos, rules, signals, triggers, users
from app.geo import haversine_m, in_bbox, valid_coord
from app.satellite import SatelliteGrid
from app.validation import GeminiValidator, MockValidator, ValidatorUnavailable, build_prompt, parse_verdict
from tests.helpers import blurry_photo, photo_bytes

NOW = datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------- geo and rules
def test_haversine_and_bbox():
    assert haversine_m(39.33, -76.62, 39.33, -76.62) == 0
    assert haversine_m(39.0, -76.0, 39.001, -76.0) == pytest.approx(111.2, abs=0.5)
    assert in_bbox(39.33, -76.62, (-76.645, 39.31, -76.595, 39.35)) and not in_bbox(40, -76.62, (-76.645, 39.31, -76.595, 39.35))
    assert valid_coord(39, -76) and not valid_coord("x", 1) and not valid_coord(100, 0) and not valid_coord(float("nan"), 0)


def test_mission_points_scale_with_urgency_streak_and_before_photo():
    assert rules.mission_points("tree_water", 1, 1, False) == 12
    assert rules.mission_points("tree_water", 3, 1, False) == 18
    assert rules.mission_points("tree_water", 1, 1, True) == 17
    assert rules.mission_points("tree_water", 1, 3, False) == round(12 * 1.2)
    assert rules.mission_points("tree_water", 1, 99, False) == round(12 * 1.5)  # streak bonus is capped


# ---------------------------------------------------------------- satellite snapshot
def test_bundled_satellite_snapshot_is_sane(settings):
    g = SatelliteGrid.load(settings.snapshot_dir / "satellite.json")
    assert g is not None and g.rows > 50 and g.cols > 50
    inside = g.sample(39.33, -76.62)
    assert -1 <= inside["ndvi"] <= 1 and 15 < inside["lst_c"] < 60
    assert 0 <= inside["veg_low_pct"] <= 1 and 0 <= inside["heat_pct"] <= 1
    assert g.sample(41.0, -76.62) is None and g.sample(39.33, -70.0) is None
    assert 0 <= g.need_score(39.33, -76.62) <= 1
    green, dense = g.sample(39.3299, -76.6205), g.sample(39.3242, -76.6117)  # campus field vs. parking lot / rowhouses
    assert green["ndvi"] > dense["ndvi"] and green["lst_c"] < dense["lst_c"]


def test_missing_snapshot_returns_none(tmp_path):
    assert SatelliteGrid.load(tmp_path / "nope.json") is None


# ---------------------------------------------------------------- photos
def test_process_upload_rejects_garbage_small_and_blurry():
    for bad, code in ((b"", "bad_photo"), (b"not an image", "bad_photo"), (blurry_photo(), "too_blurry")):
        with pytest.raises(photos.PhotoError) as e:
            photos.process_upload(bad)
        assert e.value.code == code
    import cv2
    tiny = cv2.imencode(".jpg", np.random.default_rng(1).integers(0, 255, (100, 100, 3), dtype=np.uint8))[1].tobytes()
    with pytest.raises(photos.PhotoError) as e:
        photos.process_upload(tiny)
    assert e.value.code == "bad_photo"


def test_process_upload_reencodes_and_limits_size():
    p = photos.process_upload(photo_bytes(1, size=(2400, 3200)))
    assert max(p.width, p.height) == photos.MAX_SIDE and len(p.sha256) == 64 and len(p.ahash) == 64 and p.jpeg[:2] == b"\xff\xd8"


def test_duplicate_detection_rules(settings):
    with db.connect(settings.data_dir / "app.db") as c:
        a = photos.process_upload(photo_bytes(1))
        photos.remember_photo(c, a, user_id=1, flag_id=10, now_iso="2026-09-19T00:00:00")
        assert photos.find_duplicate(c, a, flag_id=10, user_id=2)          # identical bytes: always a duplicate
        assert not photos.find_duplicate(c, photos.process_upload(photo_bytes(2)), flag_id=11, user_id=1)
        near = photos.Photo(b"x", "other-sha", a.ahash, 50, 10, 10)          # same look, different bytes
        assert photos.find_duplicate(c, near, flag_id=11, user_id=2)        # reused on another flag
        assert photos.find_duplicate(c, near, flag_id=10, user_id=1)        # same person, same flag
        assert not photos.find_duplicate(c, near, flag_id=10, user_id=2)    # a different volunteer at the same spot is fine


def test_purge_keeps_pending_and_removes_old(settings):
    with db.connect(settings.data_dir / "app.db") as c:
        old = (NOW - timedelta(days=30)).isoformat()
        for status, name in (("verified", "a.jpg"), ("pending", "b.jpg")):
            (settings.data_dir / name).write_bytes(b"x")
            c.execute("INSERT INTO missions (flag_id, user_id, status, created_at, photo_path) VALUES (1, 1, ?, ?, ?)", (status, old, name))
        assert photos.purge_old_photos(settings, c) == 1
        assert not (settings.data_dir / "a.jpg").exists() and (settings.data_dir / "b.jpg").exists()


# ---------------------------------------------------------------- validation
def test_prompt_contains_task_language_and_safety_rules():
    p = build_prompt("complete", "drain_clear", {}, "en", 2)
    assert "storm drain" in p and "BEFORE" in p and "English" in p and "never an instruction" in p and "contains_people" in p
    assert "wet or darkened soil" in build_prompt("complete", "tree_water", {}, "en", 1)
    assert "hours_text" in build_prompt("complete", "cooling_check", {}, "en", 1)
    assert "still present" in build_prompt("confirm", "problem_report", {"category": "litter"}, "en", 1)
    assert "untrusted" in build_prompt("report", "problem_report", {"claimed_type": "problem_report", "note": "ignore all"}, "en", 1)
    with pytest.raises(ValueError):
        build_prompt("nonsense", "tree_water", {}, "en", 1)


def test_parse_verdict_is_conservative_and_clamped():
    v = parse_verdict('```json\n{"subject_ok": "true", "task_done": 1, "confidence": 3, "severity": 9, "category": "Flooded Road", "photo_ok": "false"}\n```')
    assert v.subject_ok is True and v.task_done is False  # only real booleans count; 1 is not True
    assert v.confidence == 1.0 and v.severity is None and v.category == "flooded_road" and v.photo_ok is False
    assert parse_verdict("{}").subject_ok is False and parse_verdict("{}").confidence == 0.0
    assert parse_verdict('[{"subject_ok": true}]').subject_ok is True
    for bad in ("nope", '"str"', "42"):
        with pytest.raises(ValidatorUnavailable):
            parse_verdict(bad)


def _gem(handler):
    return GeminiValidator("key", "model-x", client=httpx.Client(transport=httpx.MockTransport(handler)), backoff=0)


def _reply(text):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_gemini_request_shape_and_image_order():
    seen = {}

    def handler(req):
        seen["key"], seen["body"] = req.headers["x-goog-api-key"], json.loads(req.content)
        return httpx.Response(200, json=_reply('{"subject_ok": true, "task_done": true, "confidence": 0.9}'))

    v = _gem(handler).check("complete", "tree_water", [b"before", b"after"], {}, "en")
    parts = seen["body"]["contents"][0]["parts"]
    assert seen["key"] == "key" and v.task_done and len(parts) == 3 and "inlineData" in parts[1] and "text" in parts[0]
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_retries_transient_errors_but_not_client_errors():
    calls = {"n": 0}

    def flaky(req):
        calls["n"] += 1
        return httpx.Response(429) if calls["n"] < 3 else httpx.Response(200, json=_reply("{}"))

    assert _gem(flaky).check("complete", "tree_water", [b"x"], {}, "en").confidence == 0.0 and calls["n"] == 3
    calls["n"] = 0

    def bad_key(req):
        calls["n"] += 1
        return httpx.Response(400, text="bad key")

    with pytest.raises(ValidatorUnavailable, match="400"):
        _gem(bad_key).check("complete", "tree_water", [b"x"], {}, "en")
    assert calls["n"] == 1
    seq = iter(["garbage", '{"subject_ok": true}'])
    assert _gem(lambda r: httpx.Response(200, json=_reply(next(seq)))).check("confirm", "flood_report", [b"x"], {}, "en").subject_ok
    with pytest.raises(ValidatorUnavailable, match="SAFETY"):
        _gem(lambda r: httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}})).check("confirm", "flood_report", [b"x"], {}, "en")


def test_retry_waits_follow_the_server_hint_and_stop_when_pointless(monkeypatch):
    sleeps = []
    monkeypatch.setattr("app.validation.time.sleep", lambda s: sleeps.append(s))
    calls = {"n": 0}

    def hinted(req):
        calls["n"] += 1
        return httpx.Response(429, text='{"error": {"details": [{"retryDelay": "3s"}]}}') if calls["n"] < 3 else httpx.Response(200, json=_reply("{}"))

    assert _gem(hinted).check("complete", "tree_water", [b"x"], {}, "en").confidence == 0.0
    assert sleeps == [3.0, 3.0]                                   # honours the hint, no wait after success

    sleeps.clear()
    calls["n"] = 0
    daily = lambda req: (calls.__setitem__("n", calls["n"] + 1), httpx.Response(429, text="quota GenerateRequestsPerDayPerProjectPerModel"))[1]
    with pytest.raises(ValidatorUnavailable, match="429"):
        _gem(daily).check("complete", "tree_water", [b"x"], {}, "en")
    assert calls["n"] == 1 and sleeps == []                       # daily quota: give up immediately

    sleeps.clear()
    slow = lambda req: httpx.Response(429, headers={"retry-after": "500"})
    with pytest.raises(ValidatorUnavailable):
        _gem(slow).check("complete", "tree_water", [b"x"], {}, "en")
    assert sleeps and max(sleeps) == 20.0 and len(sleeps) == 2    # waits are capped, and never after the last attempt


def test_mock_validator_is_labelled_and_plausible():
    v = MockValidator().check("complete", "cooling_check", [b"x"], {}, "en")
    assert v.subject_ok and v.task_done and v.hours_text and "demo" in v.reasoning.lower()
    assert MockValidator().check("report", "flood_report", [b"x"], {"claimed_type": "flood_report"}, "en").category == "flooded_road"


# ---------------------------------------------------------------- signals
class FakeResp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("bad", request=None, response=None)


class FakeClient:
    def __init__(self, data):
        self.data, self.calls = data, []

    def get(self, url, params=None, headers=None):
        self.calls.append((url, params))
        return FakeResp(self.data)


def weather_payload(precip, apparent, temp, prob=None):
    days = [(datetime(2026, 9, 12) + timedelta(days=i)).date().isoformat() for i in range(11)]
    return {"utc_offset_seconds": -14400, "daily": {"time": days, "precipitation_sum": precip, "apparent_temperature_max": apparent,
                                                     "temperature_2m_max": temp, "precipitation_probability_max": prob or [None] * 7 + [10, 10, 10, 10]}}


def test_fetch_weather_computes_windows(settings):
    payload = weather_payload([1, 0, 0, 2, 0, 0, 3, 0, 4, 5, 6], [30] * 7 + [33, 34, 31, 30], [28] * 11, prob=[None] * 7 + [20, 80, 0, 0])
    m = signals.fetch_weather(settings, FakeClient(payload), now=datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc))["metrics"]
    assert m["today"] == "2026-09-19" and m["past7_mm"] == 6 and m["next2_mm"] == 4 and m["next3_mm"] == 9
    assert m["max_apparent_next3_c"] == 34 and m["max_prob_next2"] == 80


def _store(conn, weather=None, nws=None, forced=None):
    if weather is not None:
        signals.save_signal(conn, "weather", {"metrics": weather})
    if nws is not None:
        signals.save_signal(conn, "nws", {"events": [{"event": e} for e in nws]})
    for name, mode in (forced or {}).items():
        signals.set_forced(conn, name, mode)


def test_condition_rules_and_forcing(settings):
    with db.connect(settings.data_dir / "app.db") as c:
        assert signals.conditions(c)["dry"] is False  # no weather data: never claim a dry spell
        _store(c, weather={"past7_mm": 1, "next2_mm": 0, "next3_mm": 1, "max_prob_next2": 10, "max_apparent_next3_c": 36, "max_temp_c": 31})
        cond = signals.conditions(c)
        assert cond["heat"] and cond["dry"] and not cond["rain"]
        _store(c, weather={"past7_mm": 30, "next2_mm": 14, "next3_mm": 20, "max_prob_next2": 90, "max_apparent_next3_c": 25, "max_temp_c": 25})
        cond = signals.conditions(c)
        assert cond["rain"] and not cond["heat"] and not cond["dry"]
        _store(c, weather={"past7_mm": 30, "next2_mm": 7, "next3_mm": 9, "max_prob_next2": 80, "max_apparent_next3_c": 25, "max_temp_c": 25})
        assert signals.conditions(c)["rain"]  # moderate rain counts when the chance is high
        _store(c, nws=["Flood Watch"])
        assert signals.conditions(c)["flood_alert"]
        _store(c, nws=["Heat Advisory"])
        assert signals.conditions(c)["heat"]
        _store(c, forced={"heat": "off", "rain": "on"})
        cond = signals.conditions(c)
        assert not cond["heat"] and cond["rain"] and cond["rain_mm"] >= 15 and cond["forced"]["heat"] == "off"
        with pytest.raises(ValueError):
            signals.set_forced(c, "hail", "on")
        with pytest.raises(ValueError):
            signals.set_forced(c, "heat", "maybe")


def test_fetch_311_filters_age_area_status_and_caps(settings):
    now = datetime(2026, 9, 19, tzinfo=timezone.utc)
    ms = lambda days: int((now - timedelta(days=days)).timestamp() * 1000)
    feat = lambda t, d, lat=39.33, lon=-76.62, ref="x": {"attributes": {"SRType": t, "SRStatus": "Open", "CreatedDate": ms(d), "Address": "1 MAIN ST, Baltimore City, 21218",
                                                                      "Neighborhood": "Charles Village", "Latitude": str(lat), "Longitude": str(lon), "ServiceRequestNum": ref}}
    data = {"features": [feat("WW-Storm Inlet Choke", 2, ref="a"), feat("WW-Storm Inlet Choke", 90, ref="old"), feat("WW-Storm Flooded Street", 6, ref="stale-flood"),
                         feat("SW-Dirty Street", 3, lat=39.5, ref="outside"), feat("SW-Dirty Street", 3, ref="ok"), feat("Unknown Type", 1, ref="u"),
                         {"attributes": {"SRType": "SW-Dirty Street", "CreatedDate": ms(1), "Latitude": "bad", "Longitude": "bad"}}]}
    rows = signals.fetch_311(settings, FakeClient(data), now)["requests"]
    assert sorted(r["ref"] for r in rows) == ["a", "ok"]
    assert rows[0]["address"] == "1 MAIN ST" and rows[0]["flag_type"] == "drain_clear"
    with pytest.raises(RuntimeError):
        signals.fetch_311(settings, FakeClient({"error": {"message": "boom"}}), now)


def test_refresh_keeps_old_data_when_a_source_fails(settings):
    path = settings.data_dir / "app.db"
    with db.connect(path) as c:
        signals.save_signal(c, "city311", {"requests": [{"ref": "keep"}]})

    class Boom:
        def get(self, *a, **k):
            raise httpx.ConnectError("offline")

    status = signals.refresh_signals(settings, path, client=Boom())
    assert all(v.startswith("error") for v in status.values())
    with db.connect(path) as c:
        assert signals.load_signal(c, "city311")[0]["requests"][0]["ref"] == "keep"


# ---------------------------------------------------------------- triggers
@pytest.fixture
def world(settings):
    path = settings.data_dir / "app.db"
    sat = SatelliteGrid.load(settings.snapshot_dir / "satellite.json")
    with db.connect(path) as c:
        features.load_snapshot(c, settings)
    return settings, path, sat


def sync(world_, mutate=None, now=NOW):
    settings, path, sat = world_
    with db.connect(path) as c:
        if mutate:
            mutate(c)
        return triggers.sync_flags(c, settings, sat, now)


def flags(path, **where):
    with db.connect(path) as c:
        q = "SELECT * FROM flags WHERE 1=1" + "".join(f" AND {k}=?" for k in where)
        return [dict(r) for r in c.execute(q, tuple(where.values()))]


DRY = {"past7_mm": 0, "next2_mm": 0, "next3_mm": 0, "max_prob_next2": 0, "max_apparent_next3_c": 28, "max_temp_c": 30}
WET = {"past7_mm": 20, "next2_mm": 30, "next3_mm": 35, "max_prob_next2": 95, "max_apparent_next3_c": 24, "max_temp_c": 24}
HOT = {"past7_mm": 20, "next2_mm": 0, "next3_mm": 2, "max_prob_next2": 0, "max_apparent_next3_c": 37, "max_temp_c": 35}
MILD = {"past7_mm": 20, "next2_mm": 0, "next3_mm": 2, "max_prob_next2": 0, "max_apparent_next3_c": 27, "max_temp_c": 25}


def test_dry_spell_flags_well_spread_high_need_trees(world):
    res = sync(world, lambda c: _store(c, weather=DRY))
    _, path, sat = world
    trees = flags(path, type="tree_water", status="open")
    assert res["conditions"]["dry"] and len(trees) == triggers.TREE_LIMIT
    for i, a in enumerate(trees):
        for b in trees[i + 1:]:
            assert haversine_m(a["lat"], a["lon"], b["lat"], b["lon"]) >= triggers.TREE_MIN_SEP_M
    assert min(json.loads(t["context"])["need"] for t in trees) > 0.5  # chosen from the neediest cells
    ctx = json.loads(trees[0]["context"])
    assert ctx["reasons"][0]["code"] == "dry_spell" and ctx["satellite"]["ndvi"] is not None


def test_rain_flags_drains_with_urgency_and_heat_flags_cooling(world):
    sync(world, lambda c: _store(c, weather=WET))
    _, path, _ = world
    drains = flags(path, type="drain_clear", source="trigger", status="open")
    assert len(drains) == 14 and all(d["urgency"] == 3 for d in drains)
    sync(world, lambda c: _store(c, weather=HOT))
    cooling = flags(path, type="cooling_check", status="open")
    assert cooling and {c["urgency"] for c in cooling} == {2, 3}
    assert flags(path, type="drain_clear", source="trigger", status="expired")  # rain passed


def test_cooling_flags_appear_when_hours_are_stale_and_go_away_once_verified(world):
    sync(world, lambda c: _store(c, weather=MILD))
    _, path, _ = world
    cool = flags(path, type="cooling_check", status="open")
    assert cool and {c["urgency"] for c in cool} == {1}
    fid = cool[0]["feature_id"]

    def verify(c):
        features.mark_verified(c, fid, {"hours_text": "9-5"}, NOW.isoformat())
        c.execute("UPDATE flags SET status='resolved', resolved_at=? WHERE feature_id=?", (NOW.isoformat(), fid))

    sync(world, verify)
    assert not [f for f in flags(path, feature_id=fid) if f["status"] == "open"]
    later = NOW + timedelta(days=rules.FLAG_TYPES["cooling_check"]["cooldown_days"] + 1)
    sync(world, now=later)
    assert [f for f in flags(path, feature_id=fid) if f["status"] == "open"]  # stale again


def test_sync_is_idempotent_and_expires_ended_conditions(world):
    sync(world, lambda c: _store(c, weather=DRY))
    _, path, _ = world
    before = len(flags(path))
    res = sync(world)
    assert res["created"] == 0 and len(flags(path)) == before
    res = sync(world, lambda c: _store(c, weather=MILD))
    assert res["expired"] >= triggers.TREE_LIMIT and not flags(path, type="tree_water", status="open")


def test_claimed_flags_survive_condition_changes_and_resolved_ones_wait_out_cooldown(world):
    sync(world, lambda c: _store(c, weather=DRY))
    _, path, _ = world
    trees = flags(path, type="tree_water", status="open")
    claimed, done = trees[0], trees[1]
    claim_until = (NOW + timedelta(minutes=30)).isoformat()

    def prep(c):
        c.execute("UPDATE flags SET claimed_by=1, claim_expires_at=? WHERE id=?", (claim_until, claimed["id"]))
        c.execute("UPDATE flags SET status='resolved', resolved_at=? WHERE id=?", (NOW.isoformat(), done["id"]))
        _store(c, weather=MILD)

    sync(world, prep)
    assert flags(path, id=claimed["id"])[0]["status"] == "open"   # someone is working on it
    sync(world, lambda c: _store(c, weather=DRY), now=NOW + timedelta(days=1))
    assert flags(path, id=done["id"])[0]["status"] == "resolved"  # still cooling down
    sync(world, now=NOW + timedelta(days=6))
    assert flags(path, id=done["id"])[0]["status"] == "open"    # cooldown over, still dry: needs water again


def test_311_requests_become_flags_and_close_when_ticket_closes(world):
    row = {"ref": "26-1", "srtype": "WW-Storm Inlet Choke", "flag_type": "drain_clear", "category": "storm_inlet_choke", "status": "Open",
           "created": "2026-09-15T10:00:00+00:00", "address": "3100 CHARLES ST", "neighborhood": "Charles Village", "lat": 39.33, "lon": -76.615}
    sync(world, lambda c: (_store(c, weather=MILD), signals.save_signal(c, "city311", {"requests": [row]})))
    _, path, _ = world
    f = flags(path, source="311")[0]
    assert f["type"] == "drain_clear" and f["urgency"] == 2 and json.loads(f["context"])["city"]["address"] == "3100 CHARLES ST"
    sync(world, lambda c: signals.save_signal(c, "city311", {"requests": []}))
    assert flags(path, source="311")[0]["status"] == "expired"


def test_user_report_flags_expire_on_schedule(world):
    _, path, _ = world
    past = (NOW - timedelta(hours=1)).isoformat()

    def add(c):
        c.execute("INSERT INTO flags (type, source, source_ref, title, lat, lon, urgency, status, context, created_at, updated_at, expires_at) "
                  "VALUES ('flood_report','user','r1','x',39.33,-76.62,2,'open','{}',?,?,?)", (past, past, past))

    sync(world, add)
    assert flags(path, source_ref="r1")[0]["status"] == "expired"


def test_flag_cap_drops_lowest_priority(world, monkeypatch):
    monkeypatch.setattr(triggers, "MAX_ACTIVE_FLAGS", 20)
    sync(world, lambda c: _store(c, weather=DRY))
    _, path, _ = world
    assert len(flags(path, status="open")) <= 20


# ---------------------------------------------------------------- users
def test_nickname_rules(settings):
    with db.connect(settings.data_dir / "app.db") as c:
        assert users.create_user(c, "  Maya  R. ", "es")["lang"] == "en"  # English is the only language
        for bad in ("a", "x" * 21, "12345", "<script>", "", "   ", "no\nnewline!"):
            with pytest.raises(users.UserError) as e:
                users.create_user(c, bad, "en")
            assert e.value.code == "bad_nickname"
        with pytest.raises(users.UserError) as e:
            users.create_user(c, "maya r.", "en")
        assert e.value.code == "nickname_taken"
        assert users.create_user(c, "Sam", "xx")["lang"] == "en"


def test_streak_logic_and_badges(settings):
    with db.connect(settings.data_dir / "app.db") as c:
        u = users.create_user(c, "Streaker", "en")
        day = lambda n: datetime(2026, 9, 10 + n, 16, 0, tzinfo=timezone.utc)
        assert users.touch_streak(c, u["id"], day(0), "America/New_York") == 1
        assert users.touch_streak(c, u["id"], day(0), "America/New_York") == 1   # same day
        assert users.touch_streak(c, u["id"], day(1), "America/New_York") == 2
        assert users.touch_streak(c, u["id"], day(2), "America/New_York") == 3
        assert users.check_badges(c, u["id"], day(2)) == ["streak_3"]
        assert users.current_streak(c, u["id"], day(3), "America/New_York") == 3
        assert users.current_streak(c, u["id"], day(5), "America/New_York") == 0  # lapsed
        assert users.touch_streak(c, u["id"], day(5), "America/New_York") == 1
        assert c.execute("SELECT best_streak FROM users WHERE id=?", (u["id"],)).fetchone()[0] == 3


def test_day_boundary_uses_local_time(settings):
    late = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)  # 11:30 pm on the 19th in Baltimore
    assert users.local_day(late, "America/New_York").isoformat() == "2026-09-19"
    assert users.local_day(late, "Not/AZone").isoformat() == "2026-09-20"  # bad zone falls back to UTC


def test_points_leaderboard_and_weekly_window(settings):
    with db.connect(settings.data_dir / "app.db") as c:
        a, b = users.create_user(c, "Ana", "en"), users.create_user(c, "Ben", "en")
        users.award(c, a["id"], 30, "mission", 1, NOW)
        users.award(c, b["id"], 50, "mission", 2, NOW - timedelta(days=10))
        users.award(c, b["id"], 0, "mission", 3, NOW)  # zero awards are ignored
        board = users.leaderboard(c, now=NOW)
        assert [r["nickname"] for r in board["weekly"]] == ["Ana"] and [r["nickname"] for r in board["all_time"]] == ["Ben", "Ana"]
        assert users.weekly_points(c, b["id"], NOW) == 0


# ---------------------------------------------------------------- evaluation helpers
def test_eval_validation_metrics_and_classification():
    from eval.eval_validation import classify, summarize
    from tests.helpers import Scripted

    v = Scripted(confidence=0.9)
    assert classify(v, "drain_clear", b"x") == (True, "verified")
    v.set(task_done=False)
    assert classify(v, "drain_clear", b"x") == (False, "rejected")
    v2 = Scripted(problem_present=False)
    assert classify(v2, "flood_report", b"x") == (False, "verified")   # problem gone: verified outcome, but not "present"
    v3 = Scripted(confidence=0.5)
    assert classify(v3, "cooling_check", b"x") == (False, "pending")
    m = summarize([("pass", True, "verified"), ("pass", False, "rejected"), ("fail", False, "rejected"), ("fail", True, "verified"), ("pass", False, "pending")])
    assert m["n"] == 5 and m["false_accepts"] == 1 and m["false_rejects"] == 2 and m["sent_to_review"] == 1
    assert m["precision"] == pytest.approx(0.5) and m["recall"] == pytest.approx(1 / 3)
    assert summarize([])["accuracy"] is None


def test_eval_satellite_terciles():
    from eval.eval_satellite import collect, tercile_table
    rows = [(0.1, "no"), (0.2, "no"), (0.3, "yes"), (0.5, "no"), (0.6, "yes"), (0.7, "yes"), (0.8, "yes"), (0.9, "yes"), (0.95, "yes"), (0.4, "unsure"), (None, "yes")]
    table = tercile_table(rows)
    assert table[1]["precision"] < table[3]["precision"] and table[3]["precision"] == 1.0 and sum(b["n"] for b in table.values()) == 9
    assert tercile_table([]) == {}
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("CREATE TABLE missions (flag_id, status, was_problem); CREATE TABLE flags (id, type, context);"
                       "INSERT INTO flags VALUES (1, 'tree_water', '{\"need\": 0.8}'); INSERT INTO missions VALUES (1, 'verified', 'yes'), (1, 'rejected', 'no');")
    assert collect(conn) == [(0.8, "yes", "tree_water")]
