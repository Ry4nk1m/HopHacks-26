import json
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import BaseModel, Field

from . import db, features, flags as flagmod, layers, missions, photos, reports, rules, signals, triggers, users
from .config import APP_NAME, SITE_NAME, Settings, load_settings
from .ratelimit import RateLimiter
from .satellite import SatelliteGrid
from .tts import TTSUnavailable, synthesize
from .validation import make_validator

STATIC = Path(__file__).parent / "static"
NO_CACHE = {"Cache-Control": "no-cache"}
SYNC_STALE_SECONDS = 300


class UserIn(BaseModel):
    nickname: str = Field(min_length=1, max_length=60)


class ArriveIn(BaseModel):
    lat: float
    lon: float
    accuracy: Optional[float] = None
    manual: bool = False


class ConfirmIn(BaseModel):
    lat: float
    lon: float
    accuracy: Optional[float] = None


class ForceIn(BaseModel):
    name: str
    mode: str


class ReviewIn(BaseModel):
    approve: bool
    note: Optional[str] = Field(default=None, max_length=200)


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=1500)
    lang: str = "en"


def create_app(settings: Optional[Settings] = None, validator=None):
    settings = settings or load_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db_path = settings.data_dir / "app.db"
    validator = validator or make_validator(settings)
    satellite = SatelliteGrid.load(settings.snapshot_dir / "satellite.json")
    limiter = RateLimiter()
    sync_lock = threading.Lock()
    state = {"last_sync": 0.0}
    render_cache = {}

    def conn():
        return db.connect(db_path)

    def sync(force=False):
        with sync_lock:
            if not force and time.monotonic() - state["last_sync"] < SYNC_STALE_SECONDS:
                return None
            with conn() as c:
                result = triggers.sync_flags(c, settings, satellite)
            state["last_sync"] = time.monotonic()
            return result

    def refresh_external():
        if not settings.external_fetch:
            return {}
        status = signals.refresh_signals(settings, db_path)
        sync(force=True)
        return status

    def background_loop():
        while True:
            time.sleep(settings.signals_refresh_minutes * 60)
            try:
                refresh_external()
                with conn() as c:
                    photos.purge_old_photos(settings, c)
            except Exception:
                pass

    @asynccontextmanager
    async def lifespan(app):
        with conn() as c:
            features.load_snapshot(c, settings)
            photos.purge_old_photos(settings, c)
            first_run = signals.load_signal(c, "city311")[0] is None
        if settings.external_fetch and first_run:
            refresh_external()
        else:
            sync(force=True)
            if settings.external_fetch:
                threading.Thread(target=refresh_external, daemon=True).start()
        if settings.background_jobs:
            threading.Thread(target=background_loop, daemon=True).start()
        yield

    app = FastAPI(title=APP_NAME, docs_url="/api/docs", openapi_url="/api/openapi.json", lifespan=lifespan)
    app.state.settings = settings
    app.state.limiter = limiter
    app.state.sync = sync

    @app.exception_handler(missions.MissionError)
    async def mission_error(request, exc):
        return JSONResponse({"code": exc.code, **exc.extra}, status_code=exc.status)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        # one JSON shape for every API error: {"code": ...} (framework errors keep {"detail": ...})
        body = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(users.UserError)
    async def user_error(request, exc):
        return JSONResponse({"code": exc.code}, status_code=exc.status)

    # ------------------------------------------------------------ auth
    def current_user(authorization: Optional[str] = Header(None)):
        token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
        with conn() as c:
            u = users.user_by_token(c, token)
        if u is None:
            raise HTTPException(401, detail={"code": "unauthorized"})
        return u

    def optional_user(authorization: Optional[str] = Header(None)):
        token = authorization[7:].strip() if authorization and authorization.lower().startswith("bearer ") else None
        with conn() as c:
            return users.user_by_token(c, token)

    def require_dev():
        if not settings.dev_tools:
            raise HTTPException(404, detail="not found")

    def require_admin(x_admin_key: Optional[str] = Header(None), key: Optional[str] = None):
        supplied = x_admin_key or key
        if not settings.admin_key or supplied != settings.admin_key:
            raise HTTPException(403, detail={"code": "forbidden"})

    def limit(key, count, window):
        if not limiter.allow(key, count, window):
            raise HTTPException(429, detail={"code": "rate_limited"})

    def client_ip(request: Request):
        fwd = request.headers.get("x-forwarded-for")
        return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?"))

    def read_upload(upload: UploadFile):
        data = upload.file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, detail={"code": "too_large"})
        return data

    # ------------------------------------------------------------ public
    @app.get("/api/health")
    def health():
        return {"ok": True}

    @app.get("/api/config")
    def get_config():
        meta = satellite.meta if satellite else None
        return {
            "app_name": APP_NAME, "site_name": SITE_NAME, "validator": validator.name, "tts": settings.tts_enabled, "dev_tools": settings.dev_tools,
            "aoi": list(settings.aoi), "center": list(settings.center), "map_style_url": settings.map_style_url,
            "photo_retention_days": settings.photo_retention_days,
            "rules": {"flag_types": {k: {"points": v["points"], "radius_m": v["radius_m"], "purpose": v["purpose"]} for k, v in rules.FLAG_TYPES.items()},
                      "manual_arrival_max_m": rules.MANUAL_ARRIVAL_MAX_M, "accuracy_cap_m": rules.ACCURACY_ALLOWANCE_CAP_M,
                      "max_attempts": rules.MAX_ATTEMPTS, "max_active_missions": rules.MAX_ACTIVE_MISSIONS, "confirm_radius_m": rules.CONFIRM_RADIUS_M,
                      "claim_minutes": rules.CLAIM_MINUTES, "badges": list(rules.BADGES)},
            "layers": {
                "bounds": layers.bounds_of(satellite) if satellite else None,
                "ndvi": {"scenes": meta["ndvi"]["scenes"], "source": meta["ndvi"]["source"]} if meta else None,
                "lst": {"scenes": meta["lst"]["scenes"], "source": meta["lst"]["source"]} if meta else None,
                "nasa_true_color": layers.NASA_TRUE_COLOR, "legends": layers.LEGENDS},
        }

    @app.get("/api/layers/{name}.png")
    def layer_png(name: str):
        if satellite is None or name not in ("ndvi", "lst"):
            raise HTTPException(404, detail="not found")
        if name not in render_cache:
            render_cache[name] = layers.render_png(satellite, name)
        return Response(render_cache[name], media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})

    @app.post("/api/users", status_code=201)
    def create_user(body: UserIn, request: Request):
        limit(("signup", client_ip(request)), 20, 3600)
        with conn() as c:
            u = users.create_user(c, body.nickname, "en")
            return users.public_user(c, u, include_token=True)

    @app.get("/api/me")
    def me(user=Depends(current_user)):
        now = datetime.now(timezone.utc)
        with conn() as c:
            out = users.public_user(c, user)
            out["streak"] = users.current_streak(c, user["id"], now, settings.timezone)
            out["active_missions"] = missions.active_missions(c, user["id"], now)
            return out

    @app.get("/api/flags")
    def list_flags(user=Depends(optional_user)):
        sync()
        now = datetime.now(timezone.utc)
        with conn() as c:
            items = flagmod.list_flags(c, user, now, settings.timezone)
            cond = signals.conditions(c)
            _, weather_at = signals.load_signal(c, "weather")
            status, _ = signals.load_signal(c, "status")
        return {"flags": items, "conditions": {
            "heat": cond["heat"], "rain": cond["rain"], "dry": cond["dry"], "forced": cond["forced"], "auto": cond["auto"],
            "metrics": cond["metrics"], "events": [e["event"] for e in cond["events"]], "weather_at": weather_at, "status": status}}

    @app.get("/api/flags/{flag_id}")
    def get_flag(flag_id: int, user=Depends(optional_user)):
        with conn() as c:
            row = c.execute("SELECT * FROM flags WHERE id=?", (flag_id,)).fetchone()
            if row is None:
                raise HTTPException(404, detail={"code": "not_found"})
            return flagmod.flag_to_dict(c, row, user, datetime.now(timezone.utc), settings.timezone)

    @app.get("/api/leaderboard")
    def leaderboard():
        with conn() as c:
            return users.leaderboard(c)

    @app.get("/api/impact")
    def impact():
        with conn() as c:
            return users.impact(c)

    # ------------------------------------------------------------ missions
    @app.post("/api/flags/{flag_id}/accept")
    def accept_mission(flag_id: int, user=Depends(current_user)):
        with conn() as c:
            m = missions.accept(c, user, flag_id, datetime.now(timezone.utc))
        return {"mission": m}

    @app.post("/api/missions/{mission_id}/arrive")
    def arrive(mission_id: int, body: ArriveIn, user=Depends(current_user)):
        with conn() as c:
            return missions.arrive(c, user, mission_id, body.lat, body.lon, body.accuracy, body.manual, datetime.now(timezone.utc))

    @app.post("/api/missions/{mission_id}/cancel")
    def cancel(mission_id: int, user=Depends(current_user)):
        with conn() as c:
            return {"mission": missions.cancel(c, user, mission_id, datetime.now(timezone.utc))}

    @app.post("/api/missions/{mission_id}/submit")
    def submit(mission_id: int, photo: UploadFile = File(...), before: Optional[UploadFile] = File(None), lat: float = Form(...), lon: float = Form(...),
               accuracy: Optional[float] = Form(None), was_problem: Optional[str] = Form(None), lang: str = Form("en"), user=Depends(current_user)):
        limit(("submit", user["id"]), 8, 600)
        data = read_upload(photo)
        before_data = read_upload(before) if before is not None else None
        result = missions.submit(settings, validator, user["id"], mission_id, data, before_data or None, lat, lon, accuracy, was_problem, lang)
        if result.get("outcome") in ("verified", "pending"):
            sync(force=True)
        return result

    @app.post("/api/reports")
    def create_report(photo: UploadFile = File(...), type: str = Form(...), note: Optional[str] = Form(None), lat: float = Form(...), lon: float = Form(...),
                      accuracy: Optional[float] = Form(None), lang: str = Form("en"), user=Depends(current_user)):
        limit(("report", user["id"]), 6, 3600)
        data = read_upload(photo)
        result = reports.create_report(settings, validator, user["id"], data, type, note, lat, lon, accuracy, lang)
        return result

    @app.post("/api/flags/{flag_id}/confirm")
    def confirm(flag_id: int, body: ConfirmIn, user=Depends(current_user)):
        with conn() as c:
            return reports.confirm_flag(c, user, flag_id, body.lat, body.lon, body.accuracy, datetime.now(timezone.utc), settings)

    # ------------------------------------------------------------ voice
    @app.post("/api/tts")
    def tts(body: TTSIn):
        try:
            audio = synthesize(settings, body.text, body.lang if body.lang in rules.LANGS else "en")
        except TTSUnavailable as exc:
            return JSONResponse({"detail": str(exc), "fallback": "browser"}, status_code=503)
        return Response(audio, media_type="audio/mpeg")

    # ------------------------------------------------------------ dev tools (demo and walk-testing)
    @app.get("/api/dev/state", dependencies=[Depends(require_dev)])
    def dev_state():
        with conn() as c:
            cond = signals.conditions(c)
            counts = {r["type"] + ":" + r["status"]: r["c"] for r in c.execute("SELECT type, status, COUNT(*) c FROM flags GROUP BY type, status")}
            return {"conditions": {k: cond[k] for k in ("heat", "rain", "dry", "auto", "forced")}, "flags": counts,
                    "status": signals.load_signal(c, "status")[0], "validator": validator.name}

    @app.post("/api/dev/force", dependencies=[Depends(require_dev)])
    def dev_force(body: ForceIn):
        try:
            with conn() as c:
                signals.set_forced(c, body.name, body.mode)
        except ValueError:
            raise HTTPException(422, detail={"code": "bad_trigger"})
        return {"sync": sync(force=True)}

    @app.post("/api/dev/refresh", dependencies=[Depends(require_dev)])
    def dev_refresh():
        return {"status": refresh_external(), "sync": sync(force=True)}

    @app.post("/api/dev/reset", dependencies=[Depends(require_dev)])
    def dev_reset():
        with conn() as c:
            for table in ("missions", "confirmations", "photo_hashes", "point_events", "badges"):
                c.execute(f"DELETE FROM {table}")
            c.execute("DELETE FROM flags WHERE source='user' OR status IN ('resolved','expired','pending')")
            c.execute("UPDATE flags SET claimed_by=NULL, claim_expires_at=NULL, verified_count=0")
            c.execute("UPDATE features SET last_verified_at=NULL, info=NULL")
            c.execute("UPDATE users SET points=0, streak=0, best_streak=0, last_active_day=NULL")
            c.execute("DELETE FROM signals WHERE key='forced'")
        return {"sync": sync(force=True)}

    # ------------------------------------------------------------ admin review queue
    @app.get("/api/admin/review", dependencies=[Depends(require_admin)])
    def admin_review():
        with conn() as c:
            return {"pending": missions.pending_reviews(c)}

    @app.post("/api/admin/review/{mission_id}", dependencies=[Depends(require_admin)])
    def admin_decide(mission_id: int, body: ReviewIn):
        with conn() as c:
            return missions.review_decide(c, settings, mission_id, body.approve, body.note, datetime.now(timezone.utc))

    @app.get("/api/admin/photo/{mission_id}", dependencies=[Depends(require_admin)])
    def admin_photo(mission_id: int, which: str = "after"):
        with conn() as c:
            r = c.execute("SELECT photo_path, before_path FROM missions WHERE id=?", (mission_id,)).fetchone()
        rel = r["before_path" if which == "before" else "photo_path"] if r else None
        path = (settings.data_dir / rel).resolve() if rel else None
        if not path or not path.is_file() or settings.data_dir.resolve() not in path.parents:
            raise HTTPException(404, detail="not found")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------ static
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", headers=NO_CACHE)

    @app.get("/admin")
    def admin_page():
        return FileResponse(STATIC / "admin.html", headers=NO_CACHE)

    @app.get("/manifest.webmanifest")
    def manifest():
        return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json", headers=NO_CACHE)

    @app.get("/static/{path:path}")
    def static_file(path: str):
        target = (STATIC / path).resolve()
        if STATIC.resolve() not in target.parents or not target.is_file():
            raise HTTPException(404, detail="not found")
        headers = {"Cache-Control": "public, max-age=86400"} if ("/vendor/" in "/" + path or path.endswith((".gif", ".png", ".webp", ".jpg", ".woff2"))) else NO_CACHE
        return FileResponse(target, headers=headers)

    return app


app = create_app()
